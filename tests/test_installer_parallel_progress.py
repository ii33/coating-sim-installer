"""Measured optimizations must retain cancellation, verification and truthful telemetry."""
import subprocess
import sys
import threading
import time

import pytest

from tools.dependency_manager import manager
from tools.dependency_manager.download_progress import DownloadProgress
from tools.dependency_manager.parallel_work import LinkedCancellation, bounded_map


def test_smoothed_speed_eta_aggregation_and_retry_reset():
    now = [0.0]
    tracker = DownloadProgress(clock=lambda: now[0])
    for second in range(5):
        now[0] = float(second)
        tracker.observe({'component': 'Python', 'phase': 'runtime_download', 'source': 'mirror',
                         'attempt': 1, 'download_bytes': second * 100, 'total_bytes': 1000})
        tracker.observe({'component': 'Git', 'phase': 'git_download', 'source': 'mirror',
                         'download_bytes': second * 50, 'total_bytes': 2000})
    snapshot = tracker.snapshot()
    assert snapshot['speed'] == pytest.approx(150)
    assert snapshot['components']['Python']['eta'] == pytest.approx(6)
    # A repeated byte observation never double counts transferred bytes.
    tracker.observe({'component': 'Python', 'download_bytes': 400})
    assert tracker.snapshot()['components']['Python']['bytes'] == 400
    now[0] += 1
    tracker.observe({'component': 'Python', 'source': 'official', 'attempt': 1,
                     'download_bytes': 10, 'total_bytes': 1000})
    retry = tracker.snapshot()['components']['Python']
    assert retry['bytes'] == 10 and retry['speed'] == 0 and retry['eta'] is None
    tracker.finish()
    assert tracker.snapshot()['speed'] == 0


def test_unknown_length_stall_completion_and_retry_never_show_stale_eta():
    now = [0.0]
    tracker = DownloadProgress(clock=lambda: now[0])
    for second in range(4):
        now[0] = float(second)
        tracker.observe({'component': '依赖', 'source': 'a', 'attempt': 1,
                         'download_bytes': second * 100})
    assert tracker.snapshot()['components']['依赖']['total'] is None
    assert tracker.snapshot()['components']['依赖']['eta'] is None
    now[0] += 4
    assert tracker.snapshot()['speed'] == 0
    tracker.observe({'component': '依赖', 'download_bytes': 400, 'transfer_finished': True})
    assert not tracker.snapshot()['components']['依赖']['active']
    now[0] += 1
    tracker.observe({'component': '依赖', 'source': 'b', 'download_bytes': 0, 'total_bytes': 0})
    assert tracker.snapshot()['components']['依赖']['speed'] == 0


def test_bounded_file_workers_are_parallel_and_join_before_return():
    lock = threading.Lock()
    state = {'active': 0, 'peak': 0}
    def work(value):
        with lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
        time.sleep(0.01)
        with lock:
            state['active'] -= 1
        return value * value
    assert sorted(bounded_map(work, range(20), threading.Event(), workers=99)) == [i*i for i in range(20)]
    assert 2 <= state['peak'] <= 4 and state['active'] == 0


def test_file_failure_waits_for_outstanding_writes_and_cancel_is_bounded():
    second_started, fail, release, done = (threading.Event() for _ in range(4))
    errors = []
    def work(value):
        if value == 0:
            assert fail.wait(2)
            raise ValueError('corrupt member')
        second_started.set()
        assert release.wait(2)
        return value
    def consume():
        try:
            list(bounded_map(work, [0, 1], threading.Event(), workers=2))
        except ValueError as exc:
            errors.append(str(exc))
        finally:
            done.set()
    thread = threading.Thread(target=consume)
    thread.start()
    assert second_started.wait(2)
    fail.set()
    assert not done.wait(0.03)
    release.set()
    thread.join(timeout=2)
    assert errors == ['corrupt member'] and not thread.is_alive()
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        list(bounded_map(lambda value: pytest.fail('must not start'), [1], cancelled))


def test_pipeline_cancellation_links_parent_without_overwriting_outcome():
    parent = threading.Event()
    child = LinkedCancellation(parent)
    child.set()
    assert child.is_set() and not parent.is_set()
    child = LinkedCancellation(parent)
    parent.set()
    assert child.is_set()


def test_pip_raw_output_is_typed_and_tail_is_not_lost(monkeypatch):
    real_popen = subprocess.Popen
    script = "print('Progress 0 of 100');print('Progress 50 of 100');print('Progress 100 of 100');print('Successfully installed safe-package');print('final-tail')"
    def popen(unused_argv, **kwargs):
        return real_popen([sys.executable, '-I', '-c', script], **kwargs)
    monkeypatch.setattr(manager, 'external_popen', popen)
    events = []
    assert manager.run_command([sys.executable, '-I', '-m', 'pip', 'install'], events.append, threading.Event()) == 0
    progress = [e for e in events if e.get('download_bytes') is not None]
    assert [e['download_bytes'] for e in progress] == [0, 50, 100]
    assert progress[-1]['transfer_finished'] is True
    assert any(e.get('text') == 'final-tail' for e in events)
