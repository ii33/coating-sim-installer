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
    monkeypatch.setattr(python_setup, "plan_runtime", lambda **k: {"status": "ready", "action": "reuse"})
    monkeypatch.setattr(python_setup, "execute_runtime", lambda *a: {"status": "success", "reused": True, "installer_executed": False})
    app = gui.DependencyManagerWindow(window, tmp_path / "selected")
    app.start("python")
    pump(window, lambda: not app.busy)
    assert app.last_code == 0
    assert "私有 Python" in app.status.get()
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


def test_close_cancels_install_worker_without_confirmation(window, monkeypatch, tmp_path):
    entered = threading.Event()

    def fake_flow(project, **kwargs):
        assert kwargs["prompt_fn"]("install") == "y"
        entered.set()
        assert kwargs["cancel_event"].wait(3)
        return 130

    monkeypatch.setattr(gui, "run_interactive", fake_flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "new-install")
    app.start()
    assert entered.wait(2)
    session = app.session
    assert session.pending is None and app.confirmation is None
    app.close()
    assert session.cancel_event.is_set()
    app.worker.join(timeout=2)
    assert not app.worker.is_alive()
    app.closing = False
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


def test_click_install_retains_full_plan_without_confirmation(window, monkeypatch, tmp_path):
    packages = [f"package-{index:05d}==1.0" for index in range(2000)]

    def flow(project, **kwargs):
        kwargs["plan_fn"]({"status": "ready", "packages": packages})
        kwargs["output"]("progress " * 5000)
        assert all(kwargs["prompt_fn"](key) == "y" for key in ("python", "git", "install"))
        return 0

    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "review")
    app.install_button.invoke()
    pump(window, lambda: not app.busy)
    assert packages[0] in app.current_plan and packages[-1] in app.current_plan
    assert len(app.current_plan) > 32000
    assert app.last_code == 0 and app.confirmation is None


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
    assert "正在安全退出" in app.status.get()
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

    app = gui.DependencyManagerWindow(window, tmp_path / "layout")
    app.toggle_details()
    window.geometry("800x680")
    window.deiconify()
    window.update()
    widgets = list(descendants(window))
    canvas = next(child for child in widgets if isinstance(child, tk.Canvas))
    hint = next(child for child in widgets if isinstance(child, gui.ttk.Label)
                and child is app.runtime_notice_label)
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
    app.toggle_details()
    window.deiconify()
    window.update()
    label = app.runtime_notice_label
    assert label.winfo_viewable()
    assert label.winfo_rooty() >= window.winfo_rooty()
    assert label.winfo_rooty() + label.winfo_height() <= window.winfo_rooty() + window.winfo_height()
    notice = label.cget("text")
    assert runtime_manifest()["version"] in notice
    assert "官方 NuGet" in notice
    assert "非最新安全补丁" in notice
    assert "不扫描或复用系统 Python" in notice
    assert not app.busy and not (tmp_path / "uncreated").exists()


def test_default_visible_controls_and_details_toggle(window, tmp_path):
    app = gui.DependencyManagerWindow(window, tmp_path / "simple")
    window.deiconify()
    window.update()
    assert app.path_entry.winfo_viewable() and app.browse_button.winfo_viewable()
    assert app.install_button.winfo_viewable() and app.details_button.winfo_viewable()
    assert not app.details_frame.winfo_viewable() and not app.progress_frame.winfo_viewable()
    assert not app.python_button.winfo_viewable() and not app.backend_control.winfo_viewable()
    assert app.source.get() == "cnb" and app.mirror.get() is True
    assert app.development.get() is False and app.backend.get() == "auto"
    app.details_button.invoke()
    window.update()
    assert app.details_frame.winfo_viewable() and app.backend_control.winfo_viewable()
    app.source.set("github")
    app.development.set(True)
    app.details_button.invoke()
    window.update()
    assert not app.details_frame.winfo_viewable()
    assert app.source.get() == "github" and app.development.get() is True
    assert not (tmp_path / "simple").exists()


def test_one_click_full_flow_failure_retry_and_nonempty_container(window, monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from tools.dependency_manager import interactive

    root = tmp_path / "nonempty"
    root.mkdir()
    sentinel = root / "my-data.txt"
    sentinel.write_bytes(b"must survive")
    identity = {"valid": True, "executable": "/python", "python": "3.12.10"}
    values = {
        "plan_runtime": {"status": "ready", "action": "extract"},
        "execute_runtime": {"status": "success", "python": "/python", "identity": identity},
        "recommend_environment": {"status": "success", "selected_backend": "cpu"},
        "discover_git": None,
        "plan_git_runtime": {"status": "ready"},
        "execute_git_runtime": {"status": "completed", "git": "/git"},
        "plan_project_deployment": {"status": "ready"},
        "execute_project_deployment": {"status": "success"},
        "inspect_environment": {"target": identity},
        "plan_launch_entries": {"status": "ready"},
        "execute_launch_entries": {"status": "success"},
        "ensure_desktop_shortcut": {"status": "success", "path": "isolated QA desktop"},
    }
    calls = {}
    for name, value in values.items():
        calls[name] = MagicMock(return_value=value)
        monkeypatch.setattr(interactive, name, calls[name])
    calls["execute_project_deployment"].side_effect = [
        {"status": "failed", "errors": ["dependency network unavailable"]},
        {"status": "success"},
    ]
    app = gui.DependencyManagerWindow(window, root)
    app.install_button.invoke()
    pump(window, lambda: not app.busy)
    assert app.last_code == 2 and app.confirmation is None
    assert "dependency network unavailable" in app.log.get("1.0", "end")
    assert sentinel.read_bytes() == b"must survive"
    app.install_button.invoke()
    pump(window, lambda: not app.busy)
    assert app.last_code == 0 and app.confirmation is None
    assert sentinel.read_bytes() == b"must survive"
    assert calls["execute_runtime"].call_args.args[1] is True
    assert calls["execute_git_runtime"].call_args.args[1] is True
    assert calls["execute_project_deployment"].call_args.kwargs["confirmed"] is True
    plan = calls["plan_project_deployment"].call_args.kwargs
    assert plan["source"] == "cnb" and plan["mirror"] == "tuna"
    assert plan["profile"] == "runtime" and plan["groups"] == ("desktop", "appearance")
    assert calls["plan_runtime"].call_args.kwargs["runtime_mirror"] is True
    assert calls["plan_git_runtime"].call_args.kwargs["runtime_mirror"] is True
    calls["execute_launch_entries"].assert_called_once()
    assert not app.busy and str(app.install_button.cget("state")) == "normal"


def test_one_click_cancel_without_any_confirmations(window, monkeypatch, tmp_path):
    entered = threading.Event()

    def flow(project, **kwargs):
        assert kwargs["prompt_fn"]("python") == "y"
        entered.set()
        assert kwargs["cancel_event"].wait(3)
        return 130

    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "cancel")
    app.install_button.invoke()
    assert entered.wait(2)
    assert app.confirmation is None
    app.cancel_button.invoke()
    pump(window, lambda: not app.busy)
    assert app.last_code == 130
    assert str(app.install_button.cget("state")) == "normal"


def test_backend_snapshot_reaches_install_flow(window, monkeypatch, tmp_path):
    received = []

    def flow(project, **kwargs):
        received.append(kwargs["backend"])
        return 0

    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "backend")
    app.backend.set("cpu")
    app.install_button.invoke()
    app.backend.set("cuda13")
    pump(window, lambda: not app.busy)
    assert received == ["cpu"]


def test_live_progress_and_logs_are_automatic_and_speeds_clear_on_finish(window, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def flow(project, **kwargs):
        assert kwargs["pipeline"] is True
        kwargs["phase_fn"]("python")
        kwargs["event_fn"]({"component": "Python", "phase": "runtime_download", "download_bytes": 1024, "total_bytes": 4096})
        kwargs["output"]("[Python] test https://user:secret@example.test/path?token=private")
        entered.set()
        assert release.wait(3)
        kwargs["phase_fn"]("source")
        return 0

    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "progress")
    window.deiconify()
    window.update()
    assert not app.progress_frame.winfo_viewable()
    app.install_button.invoke()
    assert entered.wait(2)
    pump(window, lambda: "1.0 KiB" in app.progress_detail.get())
    assert app.progress_frame.winfo_viewable() and app.progress_bar.winfo_viewable()
    assert app.log.winfo_viewable() and app.progress_label.winfo_viewable()
    assert not app.details_frame.winfo_viewable()
    assert "已用" in app.progress_detail.get() and "4.0 KiB" in app.progress_detail.get()
    assert "%" not in app.progress_detail.get()
    displayed = app.log.get("1.0", "end")
    assert "secret" not in displayed and "private" not in displayed
    app.copy_log()
    assert "secret" not in window.clipboard_get()
    destination = tmp_path / "saved.log"
    monkeypatch.setattr(gui.filedialog, "asksaveasfilename", lambda **kwargs: str(destination))
    app.save_log()
    assert "secret" not in destination.read_text(encoding="utf-8")
    release.set()
    pump(window, lambda: not app.busy)
    assert app.last_code == 0 and app.download_progress.snapshot()["speed"] == 0
    assert "总下载速度" not in app.progress_detail.get()


def test_progress_queue_is_nonblocking_and_bounded():
    events = queue.Queue(maxsize=1)
    events.put(("log", "retain"))
    session = gui.InstallerSession({}, events)
    session.send("progress", {"download_bytes": 1})
    assert events.qsize() == 1 and events.get_nowait() == ("log", "retain")


def test_cancel_ignores_queued_late_download_progress(window, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()
    def flow(project, **kwargs):
        entered.set()
        assert release.wait(3)
        return 130
    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "late-progress")
    app.start()
    assert entered.wait(2)
    app.cancel()
    app.session.events.put_nowait(("progress", {"component": "Git", "download_bytes": 1000, "total_bytes": 2000}))
    window.update()
    pump(window, lambda: app.session.events.empty())
    assert app.download_progress.snapshot()["components"] == {}
    release.set()
    pump(window, lambda: not app.busy)
    assert app.last_code == 130 and app.download_progress.snapshot()["speed"] == 0


def test_default_update_button_updates_main_source_and_not_installer(window, monkeypatch, tmp_path):
    calls = []
    def update(root, provider, **kwargs):
        calls.append((root, provider, kwargs))
        kwargs["phase_fn"]("检查主项目最新源码")
        kwargs["output"]("[主项目] latest main checked")
        return {"status": "success", "up_to_date": False, "current_version": "dev.20261017.99"}
    monkeypatch.setattr(gui, "update_project", update)
    monkeypatch.setattr(gui, "check_installer_updates", lambda *a, **k: pytest.fail("not installer self-update"))
    app = gui.DependencyManagerWindow(window, tmp_path / "selected")
    window.deiconify()
    window.update()
    assert app.project_update_button.winfo_viewable() and not app.details_frame.winfo_viewable()
    app.project_update_button.invoke()
    app.project_update_button.invoke()
    pump(window, lambda: not app.busy)
    assert len(calls) == 1 and calls[0][1] == "cnb"
    assert calls[0][2]["mirror"] == "tuna" and app.last_code == 0
    assert "dev.20261017.99" in app.status.get()
    assert "latest main checked" in app.log.get("1.0", "end")


def test_project_update_noop_failure_and_cancel_are_clear(window, monkeypatch, tmp_path):
    outcomes = iter([
        {"status": "success", "up_to_date": True, "current_version": "dev.20261017.1"},
        {"status": "failed", "errors": ["local source changes preserved"]},
        {"status": "cancelled", "errors": ["cancelled"]},
    ])
    monkeypatch.setattr(gui, "update_project", lambda *a, **k: next(outcomes))
    app = gui.DependencyManagerWindow(window, tmp_path / "update-results")
    app.start("project-update")
    pump(window, lambda: not app.busy)
    assert app.last_code == 0 and "最新" in app.status.get()
    app.start("project-update")
    pump(window, lambda: not app.busy)
    assert app.last_code == 2 and "local source changes preserved" in app.status.get()
    app.start("project-update")
    pump(window, lambda: not app.busy)
    assert app.last_code == 130 and "取消" in app.status.get()


def test_success_banner_contains_target_shortcut_and_exit_closes(window, monkeypatch, tmp_path):
    from unittest.mock import MagicMock
    target = tmp_path / "installed" / "app"
    def flow(project, **kwargs):
        kwargs["event_fn"]({"event": "completion", "operation": "install", "target": str(target),
                            "shortcut": {"status": "success", "reused": False}})
        return 0
    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "installed")
    window.deiconify()
    app.start()
    pump(window, lambda: not app.busy)
    assert app.terminal_state == "success" and "安装完成" in app.result_title.get()
    assert str(target) in app.result_detail.get() and "已创建" in app.result_detail.get()
    assert app.result_panel.winfo_viewable() and app.exit_button.winfo_viewable()
    assert str(app.exit_button.cget("state")) == "normal"
    assert not app.retry_button.winfo_viewable() and not app.details_frame.winfo_viewable()
    destroy = MagicMock()
    with monkeypatch.context() as patch:
        patch.setattr(window, "destroy", destroy)
        app.exit_button.invoke()
        destroy.assert_called_once()
    assert app.worker is None and app.session is None


@pytest.mark.parametrize("current,title", [(False, "更新完成"), (True, "已是最新版")])
def test_update_success_and_already_current_have_distinct_banners(window, monkeypatch, tmp_path, current, title):
    monkeypatch.setattr(gui, "update_project", lambda *a, **k: {"status": "success", "up_to_date": current,
        "current_version": "dev.20261017.99", "shortcut": {"status": "success", "reused": True}})
    app = gui.DependencyManagerWindow(window, tmp_path / "updated")
    app.start("project-update")
    pump(window, lambda: not app.busy)
    assert title in app.result_title.get() and app.terminal_state == "success"
    assert "已存在并验证" in app.result_detail.get() and "dev.20261017.99" in app.result_detail.get()


def test_failure_retry_clears_terminal_state_and_can_succeed(window, monkeypatch, tmp_path):
    attempts = []
    def flow(project, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("没有写入权限，请选择可写目录")
        kwargs["event_fn"]({"event": "completion", "target": str(project), "shortcut": {"status": "success"}})
        return 0
    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "retry")
    window.deiconify()
    app.start()
    pump(window, lambda: not app.busy)
    assert app.terminal_state == "failed" and "权限" in app.result_detail.get()
    assert app.retry_button.winfo_viewable()
    app.retry_button.invoke()
    pump(window, lambda: not app.busy)
    assert len(attempts) == 2 and app.terminal_state == "success"
    assert not app.retry_button.winfo_viewable()


def test_shortcut_failure_cannot_show_all_success_and_cancel_wins_over_stale_success(window, monkeypatch, tmp_path):
    def flow(project, **kwargs):
        kwargs["event_fn"]({"event": "completion", "target": str(project),
            "shortcut": {"status": "failed", "errors": ["Desktop is read-only"]}})
        return 0
    monkeypatch.setattr(gui, "run_interactive", flow)
    app = gui.DependencyManagerWindow(window, tmp_path / "shortcut-failed")
    app.start()
    pump(window, lambda: not app.busy)
    assert app.last_code == 2 and app.terminal_state == "failed"
    assert "Desktop is read-only" in app.result_detail.get() and "安装完成" not in app.result_title.get()
    def cancelled(project, **kwargs):
        kwargs["cancel_event"].set()
        kwargs["event_fn"]({"event": "completion", "shortcut": {"status": "success"}})
        return 0
    monkeypatch.setattr(gui, "run_interactive", cancelled)
    app.start()
    pump(window, lambda: not app.busy)
    assert app.last_code == 130 and app.terminal_state == "cancelled" and "已取消" in app.result_title.get()


def test_queued_success_cannot_override_a_cancel_request(window, tmp_path):
    app = gui.DependencyManagerWindow(window, tmp_path / "queued-success")
    app.operation_name = "install"
    app._show_progress()
    app.session = gui.InstallerSession({})
    app.session.events.put_nowait(("done", {"code": 0, "details": "", "summary": "安装成功",
        "completion": {"shortcut": {"status": "success"}}}))
    app.session.cancel()
    app._drain()
    assert app.last_code == 130 and app.terminal_state == "cancelled"
    assert "已取消" in app.result_title.get() and "安装成功" not in app.status.get()
