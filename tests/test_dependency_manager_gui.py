"""Offline contracts for the stdlib GUI and its worker/confirmation boundary."""

import gc
import queue
import threading
import time
from pathlib import Path

import pytest

from tools.dependency_manager import gui, python_setup
from tools.dependency_manager.cli import parser
from tools.dependency_manager.updates import validated_release_page

tk = gui.tk


def test_update_action_is_read_only_and_page_requires_click(window, monkeypatch, tmp_path):
    tag = "v0.2.0"
    page = validated_release_page(tag)
    monkeypatch.setattr(gui, "check_installer_updates", lambda *a, **k: {"status": "update_available", "local_version": "0.1.0-alpha.1", "latest_version": tag, "source": "official fixture", "update_available": True, "download_page": page})
    monkeypatch.setattr(gui, "check_project_updates", lambda *a, **k: {"status": "not_installed", "source": "cnb", "update_available": None})
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda value: opened.append(value))
    target = tmp_path / "uncreated"
    app = gui.DependencyManagerWindow(window, target)
    app.check_updates()
    pump(window, lambda: not app.busy)
    assert not target.exists() and not opened
    assert app.update_page == (tag, page)
    app.open_update_page()
    assert opened == [page]


def test_python_reuse_is_shown_as_success_in_gui(window, monkeypatch, tmp_path):
    monkeypatch.setattr(python_setup, "discover_python", lambda *a, **k: {"status": "ready", "selected_python": "normal/python.exe"})
    monkeypatch.setattr(python_setup, "execute_runtime", lambda *a: pytest.fail("reuse must never install"))
    app = gui.DependencyManagerWindow(window, tmp_path / "selected")
    app.start("python")
    pump(window, lambda: not app.busy)
    assert app.last_code == 0
    assert "无需重装" in app.status.get()
    assert "reused" in app.log.get("1.0", "end")


def test_gui_is_explicit_and_terminal_stays_default():
    assert parser().parse_args([]).operation == "interactive"
    assert parser().parse_args(["gui"]).operation == "gui"
    source = Path(gui.__file__).read_text(encoding="utf-8")
    assert "PySide" not in source and "coat_sim" not in source


def test_selection_snapshot_and_unknown_prompt_fail_closed():
    choices = {"source": "1", "directory": "original"}
    session = gui.InstallerSession(choices)
    choices["directory"] = "changed"
    assert session.prompt("directory") == "original"
    assert session.prompt("source") == "1"
    with pytest.raises(RuntimeError, match="Unknown"):
        session.prompt("unreviewed-install")


@pytest.mark.parametrize("accepted", [True, False])
def test_confirmation_requires_exact_current_request(accepted):
    session = gui.InstallerSession({})
    responses = []
    worker = threading.Thread(target=lambda: responses.append(session.prompt("install")))
    worker.start()
    kind, request = session.events.get(timeout=2)
    assert kind == "confirm"
    assert not session.respond(gui.Confirmation("install"), True)
    assert session.respond(request, accepted)
    assert not session.respond(request, not accepted)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert responses == ["y" if accepted else "n"]


def test_cancel_releases_pending_confirmation_and_rejects_late_approval():
    session = gui.InstallerSession({})
    outcomes = []

    def work():
        try:
            session.prompt("python")
        except KeyboardInterrupt:
            outcomes.append("cancelled")

    worker = threading.Thread(target=work)
    worker.start()
    _, request = session.events.get(timeout=2)
    session.cancel()
    assert not session.respond(request, True)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert outcomes == ["cancelled"]
    with pytest.raises(KeyboardInterrupt):
        session.prompt("source")


def test_cancel_unblocks_full_output_queue():
    events = queue.Queue(maxsize=1)
    events.put(("log", "first"))
    session = gui.InstallerSession({}, events)
    worker = threading.Thread(target=lambda: session.send("log", "second"))
    worker.start()
    session.cancel()
    worker.join(timeout=2)
    assert not worker.is_alive()


def test_cancel_keeps_available_cleanup_diagnostics():
    session = gui.InstallerSession({})
    session.cancel()
    session.send("log", "Partial installation retained; inspect before retry")
    assert session.events.get_nowait() == ("log", "Partial installation retained; inspect before retry")


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(python_setup, 'discover_python', lambda *a, **k: {'status': 'missing', 'selected_python': None})
    if tk is None:
        pytest.skip("Tcl/Tk not installed")
    # Repeated test roots leave Python/Tcl cycles. Reclaim them on the Tk thread,
    # never during a later worker's automatic cyclic-GC allocation.
    gc.collect()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("Interactive Tk desktop not available: " + str(exc))
    root.withdraw()
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()
        gc.collect()
        if gc_enabled:
            gc.enable()


def pump(window, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        window.update()
        time.sleep(0.01)
    assert predicate()


def test_worker_uses_current_flow_snapshot_and_redacts_logs(window, monkeypatch, tmp_path):
    calls = []
    main_thread = threading.get_ident()

    def fake_flow(project, **kwargs):
        calls.append((project, threading.get_ident(), kwargs))
        assert kwargs["prompt_fn"]("source") == "2"
        assert kwargs["prompt_fn"]("mirror") == "y"
        assert kwargs["prompt_fn"]("development") == "y"
        kwargs["output"]("https://user:secret@example.test/file?token=private")
        raise RuntimeError("failure from current backend")

    monkeypatch.setattr(gui, "run_interactive", fake_flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "new-install")
    assert app.source.get() == "cnb"
    app.source.set("github")
    app.mirror.set(True)
    app.development.set(True)
    app.start()
    pump(window, lambda: not app.busy)
    assert calls[0][1] != main_thread
    assert app.last_code == 2
    assert "failure from current backend" in app.log.get("1.0", "end")
    saved = Path(app.log_file).read_text(encoding="utf-8")
    assert "secret" not in saved and "private" not in saved
    assert "failure from current backend" in saved
    assert not (tmp_path / "new-install" / "app").exists()


def test_close_cancels_waiting_worker_without_late_confirmation(window, monkeypatch, tmp_path):
    def fake_flow(project, **kwargs):
        return 0 if kwargs["prompt_fn"]("install") == "y" else 130

    monkeypatch.setattr(gui, "run_interactive", fake_flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "new-install")
    app.start()
    pump(window, lambda: app.confirmation is not None)
    session = app.session
    request = session.pending
    app.close()
    assert session.cancel_event.is_set()
    assert request.answered.is_set()
    assert not session.respond(request, True)
    # Wait for the owned worker without touching other windows/processes.
    app.worker.join(timeout=2)
    assert not app.worker.is_alive()
    assert app.closing
    app.closing = False  # Keep fixture root alive for its normal cleanup.
    pump(window, lambda: not app.busy)
    assert app.last_code == 130


def test_existing_unknown_app_is_rejected_before_worker(window, tmp_path):
    target = tmp_path / "existing"
    (target / "app").mkdir(parents=True)
    marker = target / "app" / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    app = gui.DependencyManagerWindow(window, target)
    app.start()
    assert not app.busy
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert "无法开始" in app.status.get()


def test_python_button_only_uses_runtime_transaction(window, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(python_setup, "plan_runtime", lambda **kwargs: {"status": "ready", "directory": str(tmp_path / ".runtime")})

    def execute(plan, confirmed, emit, cancel, proxy):
        calls.append((plan, confirmed, cancel, proxy))
        return {"status": "success", "python": "owned/python.exe"}

    monkeypatch.setattr(python_setup, "execute_runtime", execute)
    monkeypatch.setattr(gui, "run_interactive", lambda *a, **k: pytest.fail("not the Python-only operation"))
    app = gui.DependencyManagerWindow(window, tmp_path / "new-install")
    app.start("python")
    pump(window, lambda: app.confirmation is not None)
    app.session.respond(app.session.pending, True)
    app.confirmation.destroy()
    app.confirmation = None
    pump(window, lambda: not app.busy)
    assert app.last_code == 0 and calls[0][1] is True


def test_tk_initialization_failure_is_clear_and_nonzero(monkeypatch, capsys):
    if tk is None:
        pytest.skip("Tcl/Tk not installed")
    def fail():
        raise tk.TclError("display unavailable")

    monkeypatch.setattr(gui.tk, "Tk", fail)
    assert gui.run_gui("unused") == 2
    assert "install.bat / install.sh" in capsys.readouterr().err


def test_missing_tk_is_clear_and_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(gui, "tk", None)
    assert gui.run_gui("unused") == 2
    assert "full Python with Tcl/Tk" in capsys.readouterr().err


def test_confirmation_displays_complete_plan_independently_of_bounded_log(window, monkeypatch, tmp_path):
    packages = [f"package-{index:05d}==1.0" for index in range(2000)]

    def flow(project, **kwargs):
        kwargs["plan_fn"]({"status": "ready", "packages": packages})
        kwargs["output"]("progress " * 5000)
        return 0 if kwargs["prompt_fn"]("install") == "y" else 130

    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "review")
    app.start()
    pump(window, lambda: app.confirmation is not None)
    frame = app.confirmation.winfo_children()[0]
    details = next(child for child in frame.winfo_children() if isinstance(child, tk.Text))
    displayed = details.get("1.0", "end")
    assert packages[0] in displayed and packages[-1] in displayed
    assert len(displayed) > 32000
    app.cancel()
    pump(window, lambda: not app.busy)


def test_close_waits_for_owned_worker_cleanup(window, monkeypatch, tmp_path):
    cleaning = threading.Event()
    release = threading.Event()

    def flow(project, **kwargs):
        assert kwargs["cancel_event"].wait(3)
        cleaning.set()
        assert release.wait(3)
        return 130

    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "slow-cleanup")
    app.start()
    app.close()
    assert cleaning.wait(2)
    window.update()
    assert window.winfo_exists() and app.busy
    assert "正在取消" in app.status.get()
    # Keep the fixture root available after observing the delayed close request.
    from unittest.mock import MagicMock
    destroy = MagicMock()
    monkeypatch.setattr(window, "destroy", destroy)
    try:
        release.set()
        pump(window, lambda: not app.busy)
        destroy.assert_called_once()
        assert app.last_code == 130
    finally:
        release.set()
        monkeypatch.undo()


def test_cancelled_runtime_diagnostics_are_visible_and_redacted(window, monkeypatch, tmp_path):
    monkeypatch.setattr(python_setup, "plan_runtime", lambda **kwargs: {"status": "ready"})

    def execute(plan, confirmed, emit, cancel, proxy):
        cancel.set()
        return {"status": "cancelled", "warnings": [
            "Partial installation retained; inspect https://user:secret@example.test/log?token=private"
        ]}

    monkeypatch.setattr(python_setup, "execute_runtime", execute)
    app = gui.DependencyManagerWindow(window, tmp_path / "cancelled-runtime")
    app.start("python")
    pump(window, lambda: app.confirmation is not None)
    app.session.respond(app.session.pending, True)
    app.confirmation.destroy()
    app.confirmation = None
    pump(window, lambda: not app.busy)
    assert app.last_code == 130
    saved = Path(app.log_file).read_text(encoding="utf-8")
    assert "Partial installation retained" in saved
    assert "secret" not in saved and "private" not in saved


def test_first_layout_wraps_using_actual_canvas_width(window, tmp_path):
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)

    gui.DependencyManagerWindow(window, tmp_path / "layout")
    window.geometry("800x680")
    window.deiconify()
    window.update()
    widgets = list(descendants(window))
    canvas = next(child for child in widgets if isinstance(child, tk.Canvas))
    hint = next(child for child in widgets if isinstance(child, gui.ttk.Label)
                and str(child.cget("text")).startswith("新安装"))
    assert 600 < int(hint.cget("wraplength")) <= canvas.winfo_width()
    window.geometry("460x500")
    window.update()
    assert 150 <= int(hint.cget("wraplength")) <= canvas.winfo_width()


def test_cancelled_runtime_keeps_final_recovery_details_with_full_queue(window, monkeypatch, tmp_path):
    monkeypatch.setattr(python_setup, "plan_runtime", lambda **kwargs: {"status": "ready"})
    filled = threading.Event()

    def execute(plan, confirmed, emit, cancel, proxy):
        for _ in range(app.session.events.maxsize):
            app.session.events.put_nowait(("log", "queued progress"))
        cancel.set()
        filled.set()
        return {"status": "cancelled", "warnings": ["x" * 40000,
                "https://user:secret@example.test/path?token=private"],
                "installer_log": "retained-installer.log", "retained_download": "retained-download"}

    monkeypatch.setattr(python_setup, "execute_runtime", execute)
    app = gui.DependencyManagerWindow(window, tmp_path / "full-queue")
    app.start("python")
    pump(window, lambda: app.confirmation is not None)
    app.session.respond(app.session.pending, True)
    app.confirmation.destroy()
    app.confirmation = None
    assert filled.wait(2)
    # Completion may wait for the UI; cancellation must not discard its result.
    pump(window, lambda: not app.busy, timeout=8)
    assert app.last_code == 130
    saved = Path(app.log_file).read_text(encoding="utf-8")
    assert "retained-installer.log" in saved and "retained-download" in saved
    assert "secret" not in saved and "private" not in saved


def test_managed_runtime_patch_status_is_visible_before_install(window, tmp_path):
    from tools.dependency_manager.runtime import runtime_manifest
    app = gui.DependencyManagerWindow(window, tmp_path / "uncreated")
    window.deiconify()
    window.update()
    label = app.runtime_notice_label
    assert label.winfo_viewable()
    assert label.winfo_rooty() >= window.winfo_rooty()
    assert label.winfo_rooty() + label.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    notice = label.cget("text")
    assert runtime_manifest()["version"] in notice
    assert "最后传统官方完整安装包" in notice
    assert "非最新安全补丁" in notice
    assert "优先复用" in notice
    assert not app.busy and not (tmp_path / "uncreated").exists()
