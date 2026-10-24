import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.dependency_manager import desktop_shortcut as shortcuts
from tools.dependency_manager import project_update as updater
from tools.dependency_manager.mirrors import ProxyConfig
from tools.dependency_manager.project_operations import project_operation


@pytest.fixture
def installation(tmp_path, monkeypatch):
    git = os.environ.get('COATING_TEST_GIT') or shutil.which('git')
    if not git:
        pytest.skip('Git unavailable')
    environment = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
    def command(directory, *args):
        return subprocess.check_output([git, '-c', 'user.name=Installer QA', '-c', 'user.email=qa@example.invalid',
            '-c', 'commit.gpgsign=false', '-C', str(directory), *args], env=environment, stderr=subprocess.STDOUT, text=True).strip()
    remote = tmp_path / 'synthetic-remote'
    remote.mkdir()
    command(remote, 'init', '--quiet', '--initial-branch=main', '--template=')
    (remote / 'coat_sim').mkdir()
    (remote / 'coat_sim/version.txt').write_text('dev.20261017.1\n')
    (remote / 'launcher.py').write_text('print("synthetic QA only")\n')
    (remote / 'requirements.txt').write_text('')
    (remote / 'source.py').write_text('value = 1\n')
    (remote / '.gitignore').write_text('.venv/\nconfig/settings.json\ndata/runtime/\nlaunch-coating.cmd\n')
    command(remote, 'add', '.')
    command(remote, 'commit', '--quiet', '-m', 'initial synthetic project')
    container = tmp_path / '隔离安装'
    container.mkdir()
    app = container / 'app'
    command(container, 'clone', '--quiet', '--no-hardlinks', str(remote), str(app))
    command(app, 'remote', 'set-url', 'origin', updater.deploy.REPOSITORY_URL)
    (app / 'config').mkdir()
    (app / 'config/settings.json').write_bytes(b'user-settings')
    (app / 'data/runtime').mkdir(parents=True)
    (app / 'data/runtime/result.bin').write_bytes(b'user-result')
    (app / '.venv').mkdir()
    (app / '.venv/keep').write_bytes(b'private-venv')
    base = container / '.runtime/python'
    base.mkdir(parents=True)
    (base / 'keep').write_bytes(b'private-runtime')
    before = command(app, 'rev-parse', 'HEAD')
    real_run = updater.deploy._run_git
    calls = []
    def transport(executable, args, **kwargs):
        args = list(args)
        calls.append(args)
        # Production URLs/config are unchanged. Only this test's Git transport is local.
        mapped = [str(remote) if value in (updater.deploy.REPOSITORY_URL, updater.CNB_URL) else value for value in args]
        if '--get-url' in args:
            return args[-1] + '\n'
        if 'fetch' in args or 'ls-remote' in args:
            mapped = ['-c', 'protocol.file.allow=always', *mapped]
        return real_run(executable, mapped, **kwargs)
    monkeypatch.setattr(updater.deploy, '_run_git', transport)
    monkeypatch.setattr(updater, 'discover_git', lambda **kwargs: git)
    monkeypatch.setattr(updater.manager, 'inspect_environment', lambda *a, **k: {'target': {'valid': True, 'base_prefix': str(base)}})
    monkeypatch.setattr(updater.private_runtime, 'inspect_owned', lambda *a, **k: {})
    planner = MagicMock(return_value={'status': 'ready'})
    execute = MagicMock(return_value={'status': 'success'})
    desktop = MagicMock(return_value={'status': 'success', 'path': 'isolated-desktop/Coating Sim.lnk'})
    monkeypatch.setattr(updater.manager, 'plan_environment', planner)
    monkeypatch.setattr(updater.manager, 'execute_plan', execute)
    monkeypatch.setattr(updater, 'ensure_desktop_shortcut', desktop)
    def newer(extra=None, keep_version=False):
        (remote / 'source.py').write_text('value = 2\n')
        if not keep_version:
            (remote / 'coat_sim/version.txt').write_text('dev.20261017.2\n')
        if extra:
            path = remote / extra
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('remote content\n')
            command(remote, 'add', '-f', extra)
        command(remote, 'add', '.')
        command(remote, 'commit', '--quiet', '-m', 'new synthetic source')
        return command(remote, 'rev-parse', 'HEAD')
    def update(**kwargs):
        return updater.update_project(container, proxy=ProxyConfig('none'), **kwargs)
    def preserved():
        assert (app / 'config/settings.json').read_bytes() == b'user-settings'
        assert (app / 'data/runtime/result.bin').read_bytes() == b'user-result'
        assert (app / '.venv/keep').read_bytes() == b'private-venv'
        assert (base / 'keep').read_bytes() == b'private-runtime'
    return locals()


def test_forward_update_preserves_user_data_and_repeated_check_is_noop(installation):
    f = installation
    latest = f['newer']()
    result = f['update']()
    assert result['status'] == 'success', result
    assert result['current_commit'] == latest and result['current_version'] == 'dev.20261017.2'
    f['preserved']()
    receipt = json.loads((f['app'] / '.git/coating-installer-update.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'success' and receipt['new_commit'] == latest
    assert f['planner'].call_args.args[5] == 'repair'
    second = f['update']()
    assert second['status'] == 'success' and second['up_to_date'] is True
    f['execute'].assert_called_once()
    f['preserved']()


def test_main_commit_change_updates_even_when_version_text_unchanged(installation):
    f = installation
    latest = f['newer'](keep_version=True)
    result = f['update'](provider='github')
    assert result['status'] == 'success' and result['current_commit'] == latest


def test_modified_tracked_source_stops_before_fetch(installation):
    f = installation
    f['newer']()
    (f['app'] / 'source.py').write_text('user edit\n')
    result = f['update']()
    assert result['status'] == 'failed' and '本地源码修改' in str(result['errors'])
    assert not any('fetch' in call for call in f['calls'])
    assert (f['app'] / 'source.py').read_text() == 'user edit\n'
    f['preserved']()


def test_ignored_user_file_collision_blocks_checkout(installation):
    f = installation
    f['newer'](extra='config/settings.json')
    result = f['update']()
    assert result['status'] == 'failed' and '冲突' in str(result['errors'])
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == f['before']
    f['preserved']()


def test_local_commits_are_never_reset_to_remote(installation):
    f = installation
    f['newer']()
    (f['app'] / 'local.py').write_text('my local commit\n')
    f['command'](f['app'], 'add', 'local.py')
    f['command'](f['app'], 'commit', '--quiet', '-m', 'local work')
    local = f['command'](f['app'], 'rev-parse', 'HEAD')
    result = f['update']()
    assert result['status'] == 'failed'
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == local
    assert (f['app'] / 'local.py').exists()


@pytest.mark.parametrize('cancelled', [False, True])
def test_dependency_failure_or_cancel_restores_old_source_under_lock(installation, cancelled):
    f = installation
    f['newer']()
    cancel = threading.Event()
    def fail(*args):
        assert (f['container'] / '.app.coating-installer.lock').exists()
        if cancelled:
            cancel.set()
        return {'status': 'cancelled' if cancelled else 'failed', 'errors': ['test dependency failure']}
    f['execute'].side_effect = fail
    result = f['update'](cancel_event=cancel)
    assert result['status'] == ('cancelled' if cancelled else 'failed')
    assert result['source_rolled_back'] is True
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == f['before']
    assert f['command'](f['app'], 'rev-parse', '--abbrev-ref', 'HEAD') == 'main'
    assert not (f['container'] / '.app.coating-installer.lock').exists()
    f['preserved']()


def test_shortcut_failure_keeps_updated_source_and_reports_warning(installation):
    f = installation
    latest = f['newer']()
    f['desktop'].return_value = {'status': 'failed', 'errors': ['桌面写入被拒绝']}
    result = f['update']()
    # The verified source update and dependency transaction are retained: a
    # noncritical shortcut failure returns success-with-warning, never a rollback.
    assert result['status'] == 'success' and result['source_changed'] is True, result
    assert result['current_commit'] == latest
    assert 'source_rolled_back' not in result
    assert result['shortcut']['status'] == 'failed'
    assert any('桌面快捷方式未完成' in warning and '桌面写入被拒绝' in warning
               for warning in result['warnings'])
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == latest
    assert (f['app'] / 'source.py').read_text() == 'value = 2\n'
    assert f['execute'].call_count == 1
    receipt = json.loads((f['app'] / '.git/coating-installer-update.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'success' and receipt['new_commit'] == latest
    assert receipt['shortcut_status'] == 'failed'
    f['preserved']()


def test_already_current_update_with_shortcut_failure_stays_success_without_reinstall(installation):
    f = installation
    f['desktop'].return_value = {'status': 'failed', 'errors': ['桌面写入被拒绝']}
    before = f['command'](f['app'], 'rev-parse', 'HEAD')
    result = f['update']()
    # Source already current: no dependency transaction is re-run, no rollback,
    # and the shortcut failure is a noncritical success-with-warning.
    assert result['status'] == 'success' and result['up_to_date'] is True
    assert result['current_commit'] == before and result['source_changed'] is False
    assert 'source_rolled_back' not in result
    assert result['shortcut']['status'] == 'failed'
    assert any('桌面快捷方式未完成' in warning and '桌面写入被拒绝' in warning
               for warning in result['warnings'])
    f['execute'].assert_not_called()
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == before
    assert (f['app'] / 'source.py').read_text() == 'value = 1\n'
    receipt = json.loads((f['app'] / '.git/coating-installer-update.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'success' and receipt['shortcut_status'] == 'failed'
    f['preserved']()


def test_cancellation_at_update_shortcut_post_step_keeps_verified_update(installation, monkeypatch):
    f = installation
    latest = f['newer']()
    cancel = threading.Event()
    shortcut = MagicMock()
    monkeypatch.setattr(updater, 'ensure_desktop_shortcut', shortcut)
    # Cancel exactly at the post-step: after the verified identity/version check,
    # before the noncritical shortcut. read_text is called once for the old
    # version and once for the verified current version.
    real_read_text = Path.read_text
    seen = []

    def read_text(self, *args, **kwargs):
        text = real_read_text(self, *args, **kwargs)
        if self.name == 'version.txt':
            seen.append(text)
            if len(seen) == 2:
                cancel.set()
        return text

    monkeypatch.setattr(Path, 'read_text', read_text)
    result = f['update'](cancel_event=cancel)
    # The verified update is committed: cancellation must not roll it back.
    assert result['status'] == 'success'
    assert result['current_commit'] == latest
    assert 'source_rolled_back' not in result
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == latest
    assert (f['app'] / 'source.py').read_text() == 'value = 2\n'
    assert any('桌面快捷方式未完成' in warning for warning in result['warnings'])
    shortcut.assert_not_called()
    receipt = json.loads((f['app'] / '.git/coating-installer-update.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'success' and receipt['new_commit'] == latest
    f['preserved']()


def test_raising_shortcut_is_noncritical_and_does_not_roll_back_update(installation):
    f = installation
    latest = f['newer']()
    # An unexpected ordinary Exception (e.g. an injected Windows API error) must
    # not roll back the verified update: the boundary converts it to a redacted
    # failed payload and the update still reports success-with-warning.
    f['desktop'].side_effect = OSError('desktop API failed at https://user:pass@example.invalid/desktop')
    result = f['update']()
    assert result['status'] == 'success' and result['source_changed'] is True
    assert result['current_commit'] == latest and 'source_rolled_back' not in result
    assert result['shortcut']['status'] == 'failed'
    error = "; ".join(result['shortcut']['errors'])
    assert 'OSError' in error and 'desktop API failed' in error
    assert 'example.invalid' in error and 'user:pass' not in error
    assert any('桌面快捷方式未完成' in warning for warning in result['warnings'])
    assert f['command'](f['app'], 'rev-parse', 'HEAD') == latest
    assert (f['app'] / 'source.py').read_text() == 'value = 2\n'
    assert f['execute'].call_count == 1
    receipt = json.loads((f['app'] / '.git/coating-installer-update.json').read_text(encoding='utf-8'))
    assert receipt['state'] == 'success' and receipt['new_commit'] == latest
    assert receipt['shortcut_status'] == 'failed'
    f['preserved']()


def test_shortcut_keyboard_interrupt_is_not_swallowed_by_update(installation):
    f = installation
    f['newer']()
    f['desktop'].side_effect = KeyboardInterrupt
    # BaseException is never converted into a shortcut failure by the boundary.
    with pytest.raises(KeyboardInterrupt):
        f['update']()


def test_shared_operation_lock_blocks_duplicate_update(installation):
    f = installation
    with project_operation(f['container']):
        result = f['update']()
        assert result['status'] == 'failed' and '已有安装或更新任务' in str(result['errors'])
        assert (f['container'] / '.app.coating-installer.lock').exists()
    assert not (f['container'] / '.app.coating-installer.lock').exists()


def test_unknown_origin_and_nonempty_unknown_target_are_preserved(installation, tmp_path):
    f = installation
    f['command'](f['app'], 'remote', 'set-url', 'origin', 'https://example.invalid/other.git')
    assert f['update']()['status'] == 'failed'
    assert not any('fetch' in call for call in f['calls'])
    f['preserved']()
    unknown = tmp_path / 'unknown'
    (unknown / 'app').mkdir(parents=True)
    (unknown / 'app/keep').write_bytes(b'unknown')
    assert updater.update_project(unknown)['status'] == 'failed'
    assert (unknown / 'app/keep').read_bytes() == b'unknown'


@pytest.fixture
def desktop_install(tmp_path, monkeypatch):
    root = tmp_path / '项目 space & 测试'
    (root / '.git').mkdir(parents=True)
    desktop = tmp_path / 'OneDrive' / '重定向桌面'
    desktop.mkdir(parents=True)
    icon = root / 'coat_sim/ui/assets/icons/logolite.ico'
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b'taskbar icon v1')
    base = {'status': 'ready', 'root': str(root), 'python': str(root / '.venv/Scripts/python.exe'),
            'icon': str(icon), 'icon_sha256': hashlib.sha256(icon.read_bytes()).hexdigest(),
            'script': 'owned script'}
    monkeypatch.setattr(shortcuts.sys, 'platform', 'win32')
    monkeypatch.setattr(shortcuts.launchers, 'plan_launch_entries', lambda *a, **k: base)
    known = MagicMock(return_value=desktop)
    monkeypatch.setattr(shortcuts.launchers, '_known_folder', known)
    writer = MagicMock(side_effect=lambda path, plan: path.write_bytes(
        (plan['icon'] + ':' + plan['icon_sha256']).encode().ljust(100, b'.')))
    monkeypatch.setattr(shortcuts.launchers, '_write_shortcut', writer)
    return root, desktop, writer, known, base


def test_redirected_desktop_shortcut_is_created_once_and_reused(desktop_install):
    root, desktop, writer, known, base = desktop_install
    result = shortcuts.ensure_desktop_shortcut(root)
    assert result['status'] == 'success', result
    original = Path(result['path']).read_bytes()
    second = shortcuts.ensure_desktop_shortcut(root)
    assert second['status'] == 'success' and second['reused'] is True
    writer.assert_called_once()
    assert Path(result['path']).read_bytes() == original and len(list(desktop.glob('*.lnk'))) == 1
    assert known.call_args.args == ('desktop',)
    plan = writer.call_args.args[1]
    assert '.venv' in plan['python'] and plan['icon'].endswith('logolite.ico')
    receipt = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert receipt['icon_sha256'] == base['icon_sha256']


def test_unrelated_same_name_shortcut_uses_one_deterministic_alternate(desktop_install):
    root, desktop, writer, known, base = desktop_install
    primary = desktop / 'Coating Sim.lnk'
    primary.write_bytes(b'unrelated shortcut')
    first = shortcuts.ensure_desktop_shortcut(root)
    second = shortcuts.ensure_desktop_shortcut(root)
    assert first['status'] == second['status'] == 'success'
    assert first['path'] == second['path'] and Path(first['path']) != primary
    assert primary.read_bytes() == b'unrelated shortcut' and len(list(desktop.glob('*.lnk'))) == 2
    writer.assert_called_once()


def test_modified_owned_shortcut_and_unknown_receipt_are_not_overwritten(desktop_install):
    root, desktop, writer, known, base = desktop_install
    first = shortcuts.ensure_desktop_shortcut(root)
    target = Path(first['path'])
    target.write_bytes(b'user changed')
    base['icon_sha256'] = 'icon-v2'
    assert shortcuts.ensure_desktop_shortcut(root)['status'] == 'failed'
    assert target.read_bytes() == b'user changed' and len(list(desktop.glob('*.lnk'))) == 1
    writer.assert_called_once()
    marker = root / '.git/coating-installer-desktop.json'
    marker.write_text('{"unrelated":true}')
    assert shortcuts.ensure_desktop_shortcut(root)['status'] == 'failed'
    assert marker.read_text() == '{"unrelated":true}'


def test_assume_unchanged_source_cannot_be_silently_overwritten(installation):
    f = installation
    f["newer"]()
    f["command"](f["app"], "update-index", "--assume-unchanged", "source.py")
    (f["app"] / "source.py").write_text("hidden local edit\n")
    result = f["update"]()
    assert result["status"] == "failed" and "索引标记" in str(result["errors"])
    assert (f["app"] / "source.py").read_text() == "hidden local edit\n"
    assert not any("fetch" in call for call in f["calls"])


def test_owned_shortcut_is_reused_after_desktop_redirect(desktop_install, tmp_path):
    root, desktop, writer, known, base = desktop_install
    first = shortcuts.ensure_desktop_shortcut(root)
    relocated = tmp_path / "新的 OneDrive 桌面"
    relocated.mkdir()
    old_path = Path(first["path"])
    new_path = relocated / old_path.name
    old_path.rename(new_path)
    known.return_value = relocated
    second = shortcuts.ensure_desktop_shortcut(root)
    assert second["status"] == "success" and second["reused"] is True
    assert Path(second["path"]) == new_path
    assert len(list(relocated.glob("*.lnk"))) == 1
    writer.assert_called_once()


def test_owned_shortcut_refreshes_when_application_icon_path_changes(desktop_install):
    root, desktop, writer, known, base = desktop_install
    # Simulate a receipt created by the prior installer, which selected launcher.ico.
    old_icon = root / 'coat_sim/ui/assets/icons/launcher.ico'
    old_icon.write_bytes(b'old launcher icon')
    base['icon'] = str(old_icon)
    base['icon_sha256'] = hashlib.sha256(old_icon.read_bytes()).hexdigest()
    first = shortcuts.ensure_desktop_shortcut(root)
    target = Path(first['path'])
    old_data = target.read_bytes()

    base['icon'] = str(root / 'coat_sim/ui/assets/icons/logolite.ico')
    base['icon_sha256'] = hashlib.sha256(Path(base['icon']).read_bytes()).hexdigest()
    refreshed = shortcuts.ensure_desktop_shortcut(root)
    assert refreshed['status'] == 'success' and refreshed['reused'] is False
    assert Path(refreshed['path']) == target and target.read_bytes() != old_data
    receipt = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert receipt['icon'] == base['icon']
    assert receipt['icon_sha256'] == base['icon_sha256']
    assert writer.call_count == 2

    reused = shortcuts.ensure_desktop_shortcut(root)
    assert reused['status'] == 'success' and reused['reused'] is True
    assert writer.call_count == 2 and len(list(desktop.glob('*.lnk'))) == 1


def test_owned_shortcut_refreshes_when_same_icon_path_bytes_change(desktop_install):
    root, desktop, writer, known, base = desktop_install
    first = shortcuts.ensure_desktop_shortcut(root)
    target = Path(first['path'])
    old_data = target.read_bytes()

    icon = Path(base['icon'])
    icon.write_bytes(b'taskbar icon v2')
    base['icon_sha256'] = hashlib.sha256(icon.read_bytes()).hexdigest()
    refreshed = shortcuts.ensure_desktop_shortcut(root)
    assert refreshed['status'] == 'success' and refreshed['reused'] is False
    assert Path(refreshed['path']) == target and target.read_bytes() != old_data
    receipt = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert receipt['icon'] == base['icon']
    assert receipt['icon_sha256'] == base['icon_sha256']
    assert writer.call_count == 2


def test_shortcut_refresh_recovers_if_atomic_replace_fails(desktop_install, monkeypatch):
    root, desktop, writer, known, base = desktop_install
    first = shortcuts.ensure_desktop_shortcut(root)
    target = Path(first['path'])
    old_data = target.read_bytes()
    icon = Path(base['icon'])
    icon.write_bytes(b'taskbar icon v2')
    base['icon_sha256'] = hashlib.sha256(icon.read_bytes()).hexdigest()
    real_replace = shortcuts.os.replace

    def fail_shortcut_replace(source, destination):
        if Path(destination) == target:
            raise OSError('replace blocked')
        return real_replace(source, destination)

    monkeypatch.setattr(shortcuts.os, 'replace', fail_shortcut_replace)

    failed = shortcuts.ensure_desktop_shortcut(root)
    assert failed['status'] == 'failed'
    assert target.read_bytes() == old_data
    pending = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert pending['state'] == 'refreshing' and pending['previous_sha256']

    monkeypatch.setattr(shortcuts.os, 'replace', real_replace)
    recovered = shortcuts.ensure_desktop_shortcut(root)
    assert recovered['status'] == 'success' and recovered['reused'] is False
    assert target.read_bytes() != old_data
    receipt = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert receipt['state'] == 'created' and 'previous_sha256' not in receipt
    assert writer.call_count == 3


def test_shortcut_refresh_recovers_if_receipt_finalization_fails(desktop_install, monkeypatch):
    root, desktop, writer, known, base = desktop_install
    first = shortcuts.ensure_desktop_shortcut(root)
    target = Path(first['path'])
    icon = Path(base['icon'])
    icon.write_bytes(b'taskbar icon v2')
    base['icon_sha256'] = hashlib.sha256(icon.read_bytes()).hexdigest()
    save_record = shortcuts.save_record

    def fail_created_receipt(path, data, **kwargs):
        if data.get('state') == 'created' and data.get('icon_sha256') == base['icon_sha256']:
            raise OSError('receipt finalization blocked')
        return save_record(path, data, **kwargs)

    monkeypatch.setattr(shortcuts, 'save_record', fail_created_receipt)
    failed = shortcuts.ensure_desktop_shortcut(root)
    assert failed['status'] == 'failed'
    replacement_data = target.read_bytes()
    pending = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert pending['state'] == 'refreshing' and 'previous_sha256' in pending

    monkeypatch.setattr(shortcuts, 'save_record', save_record)
    recovered = shortcuts.ensure_desktop_shortcut(root)
    assert recovered['status'] == 'success' and recovered['reused'] is True
    assert target.read_bytes() == replacement_data
    receipt = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert receipt['state'] == 'created' and 'previous_sha256' not in receipt
    assert writer.call_count == 2


def test_shortcut_refresh_preserves_link_mutated_while_staging(desktop_install, monkeypatch):
    root, desktop, writer, known, base = desktop_install
    first = shortcuts.ensure_desktop_shortcut(root)
    target = Path(first['path'])
    user_data = b'user changed shortcut during icon refresh'
    icon = Path(base['icon'])
    icon.write_bytes(b'taskbar icon v2')
    base['icon_sha256'] = hashlib.sha256(icon.read_bytes()).hexdigest()
    real_write = shortcuts.launchers._write_shortcut

    def mutate_then_stage(path, plan):
        target.write_bytes(user_data)
        return real_write(path, plan)

    monkeypatch.setattr(shortcuts.launchers, '_write_shortcut', mutate_then_stage)
    result = shortcuts.ensure_desktop_shortcut(root)
    assert result['status'] == 'failed'
    assert target.read_bytes() == user_data
    receipt = json.loads((root / '.git/coating-installer-desktop.json').read_text())
    assert receipt['state'] == 'created'
    assert writer.call_count == 2
