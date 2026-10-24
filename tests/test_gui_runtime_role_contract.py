"""Headless GUI controller contracts, not native Tk/rendering acceptance.

These exercise the real worker, queue, cancellation, retry and result methods.
Only widgets/variables are test doubles, so they run on hosts without a display.
"""

import ast
import threading
import time
from pathlib import Path

import pytest

from tools.dependency_manager import default_install, gui, interactive


class MainThreadValue:
    def __init__(self, value=""):
        self.value = value
        self.thread = threading.get_ident()

    def get(self):
        assert threading.get_ident() == self.thread, "worker touched a GUI variable"
        return self.value

    def set(self, value):
        assert threading.get_ident() == self.thread, "worker touched a GUI variable"
        self.value = value


class HeadlessWidget:
    def __init__(self):
        self.options = {}
        self.visible = False
        self.text = ""

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def grid(self):
        self.visible = True

    def grid_remove(self):
        self.visible = False

    def start(self, interval):
        self.options["running"] = True

    def stop(self):
        self.options["running"] = False

    def insert(self, index, text):
        self.text += text

    def delete(self, start, end):
        self.text = ""

    def index(self, index):
        return str(len(self.text.splitlines()) + 1) + ".0"

    def see(self, index):
        pass


class HeadlessWindow:
    def __init__(self):
        self.destroyed = False
        self.callbacks = []

    def after(self, delay, callback):
        self.callbacks.append(callback)

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def controller(tmp_path):
    """Construct controller state without creating or pretending to render Tk."""
    app = gui.DependencyManagerWindow.__new__(gui.DependencyManagerWindow)
    app.window = HeadlessWindow()
    for name, value in {
        "directory": str(tmp_path / "selected"), "source": "cnb", "mirror": True,
        "development": False, "backend": "auto", "backend_hint": "",
        "status": "", "log_path": "", "progress_detail": "", "result_title": "", "result_detail": "",
    }.items():
        setattr(app, name, MainThreadValue(value))
    for name in ("result_panel", "result_actions", "result_heading", "result_body", "exit_button",
                 "retry_button", "shortcut_button", "cancel_button", "progress_frame", "progress_bar",
                 "log", "log_button", "backend_control", "update_button", "install_button"):
        setattr(app, name, HeadlessWidget())
    app._resize = lambda: None
    app.explicit_mirror = "tuna"
    app.mirror_changed = False
    app.session = app.worker = app.confirmation = None
    app.closing = False
    app.controls = [app.install_button, app.backend_control]
    app.last_code = None
    app.current_phase = "waiting"
    app.current_plan = ""
    app.log_file = None
    app.download_progress = gui.DownloadProgress()
    app.operation_started = app.operation_finished = None
    app.completed_phases = set()
    app.active_phase = None
    app.total_phases = len(gui.PHASES)
    app.operation_name = ""
    app.current_target = app.directory.get()
    app.terminal_state = "idle"
    app.update_page = None
    yield app
    if app.worker is not None:
        app.session.cancel()
        app.worker.join(timeout=4)
        assert not app.worker.is_alive(), "headless test leaked an install worker"


def drain_until(app, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app._drain()
        time.sleep(0.005)
    assert predicate()


def complete(kwargs, project, **extra):
    kwargs["event_fn"]({"event": "completion", "operation": "install", "target": str(project),
                        "shortcut": {"status": "success"}, **extra})


def test_gui_has_static_coordinator_import_and_no_legacy_dispatch():
    tree = ast.parse(Path(gui.__file__).read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.ImportFrom) and node.module == "default_install" and
               any(name.name == "run_default_installation" for name in node.names)
               for node in tree.body)
    assert gui.run_default_installation is default_install.run_default_installation
    coordinator = ast.parse(Path(default_install.__file__).read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.ImportFrom) and node.module == "runtime_roles" and
               {"plan_runtime_roles", "execute_runtime_roles"}.issubset({name.name for name in node.names})
               for node in coordinator.body)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                   node.func.id in {"run_interactive", "execute_plan", "execute_project_deployment"}
                   for node in ast.walk(tree))


def test_default_dispatch_snapshots_ui_and_runs_only_coordinator(controller, monkeypatch):
    app = controller
    entered, release = threading.Event(), threading.Event()
    received = []
    main = threading.get_ident()

    def install(project, **kwargs):
        entered.set()
        assert release.wait(3)
        received.append((project, threading.get_ident(), kwargs))
        assert all(kwargs["prompt_fn"](key) == "y" for key in ("python", "git", "install"))
        kwargs["plan_fn"]({"operation": "runtime_roles_plan", "roles": ["app", "deposition_cpu",
            "deposition_directml", "deposition_cuda", "appearance"]})
        complete(kwargs, project)
        return 0

    monkeypatch.setattr(gui, "run_default_installation", install)
    monkeypatch.setattr(interactive, "run_interactive", lambda *a, **k: pytest.fail("legacy fallback"))
    app.source.set("github")
    app.development.set(True)
    app.backend.set("cuda12")
    app.mirror.set(False)
    app.mirror_changed = True
    root = Path(app.directory.get())
    app.start()
    assert entered.wait(2)
    app.source.set("cnb")
    app.development.set(False)
    app.backend.set("cpu")
    app.mirror.set(True)
    app.directory.set(str(root / "changed"))
    app.start()  # Repeated clicks cannot start a second transaction.
    release.set()
    drain_until(app, lambda: not app.busy)
    assert len(received) == 1
    project, worker, kwargs = received[0]
    assert project == root and worker != main
    assert kwargs["source"] == "github" and kwargs["development"] is True
    assert kwargs["backend"] == "cuda12" and kwargs["mirror"] == "pypi"
    assert "pipeline" not in kwargs
    assert app.last_code == 0 and app.terminal_state == "success"
    assert "deposition_directml" in app.current_plan and "deposition_cuda" in app.current_plan
    assert app.install_button.options["state"] == "normal"


def test_coordinator_failure_is_redacted_visible_and_retryable(controller, monkeypatch):
    app = controller
    calls = []

    def install(project, **kwargs):
        calls.append(project)
        if len(calls) == 1:
            raise ValueError("role dependency failed https://user:secret@example.test/a?token=private")
        complete(kwargs, project)
        return 0

    monkeypatch.setattr(gui, "run_default_installation", install)
    app.start()
    drain_until(app, lambda: not app.busy)
    assert app.last_code == 2 and app.terminal_state == "failed"
    assert "role dependency failed" in app.result_detail.get() and app.retry_button.visible
    saved = Path(app.log_file).read_text(encoding="utf-8")
    assert "role dependency failed" in saved and "secret" not in saved and "private" not in saved
    assert "secret" not in app.result_detail.get() and "private" not in app.result_detail.get()
    app.retry()
    drain_until(app, lambda: not app.busy)
    assert len(calls) == 2 and app.terminal_state == "success" and not app.retry_button.visible


def test_close_waits_for_role_cleanup_and_cancel_beats_completion(controller, monkeypatch):
    app = controller
    entered, cleaning, release = threading.Event(), threading.Event(), threading.Event()

    def install(project, **kwargs):
        entered.set()
        assert kwargs["cancel_event"].wait(3)
        cleaning.set()
        assert release.wait(3)
        complete(kwargs, project)
        return 0

    monkeypatch.setattr(gui, "run_default_installation", install)
    app.start()
    assert entered.wait(2)
    session = app.session
    app.close()
    assert cleaning.wait(2)
    assert session.cancel_event.is_set() and not app.window.destroyed and app.busy
    assert "正在安全退出" in app.status.get()
    release.set()
    drain_until(app, lambda: not app.busy)
    assert app.window.destroyed and app.last_code == 130 and app.terminal_state == "cancelled"


def test_installed_gpu_roles_do_not_claim_verified_gpu_readiness(controller, monkeypatch):
    app = controller

    def install(project, **kwargs):
        kwargs["phase_fn"]("verify")
        complete(kwargs, project, compute_route={"requested_backend": "auto", "planned_backend": "cuda12",
            "usable_backend": None, "reason": "Roles installed; device probes not accepted"})
        return 0

    monkeypatch.setattr(gui, "run_default_installation", install)
    app.start()
    drain_until(app, lambda: not app.busy)
    detail = app.result_detail.get()
    assert "已验证可用：未确认" in detail and "已安装不等于 GPU 已验证可用" in detail
    assert "DirectML" in detail and "CUDA" in detail
    assert "已安装不等于 GPU 已验证可用" in app.log.text


def test_managed_update_retains_probe_evidence_without_inventing_readiness(controller, monkeypatch):
    app = controller
    def update(project, provider, **kwargs):
        return {"status": "success", "up_to_date": True, "current_version": "fixture",
                "shortcut": {"status": "success"}, "runtime_roles": {"deposition_cuda": "deposition_cuda"},
                "manifest": "private binding", "compute_route": {"requested_backend": "auto",
                    "planned_backend": "CPU + DirectML + CUDA13", "usable_backend": "未确认",
                    "backend_usable": {}, "reason": "没有通过的数值探测，不宣称 GPU 就绪"}}
    monkeypatch.setattr(gui, "update_project", update)
    app.start("project-update")
    drain_until(app, lambda: not app.busy)
    assert app.last_code == 0
    assert "CUDA13" in app.result_detail.get() and "已验证可用：未确认" in app.result_detail.get()
    assert "不宣称 GPU 就绪" in app.result_detail.get()


@pytest.mark.parametrize("operation", ["project-update", "python"])
def test_explicit_noninstall_operations_never_dispatch_role_install(controller, monkeypatch, operation):
    app = controller
    seen = []
    monkeypatch.setattr(gui, "run_default_installation", lambda *a, **k: pytest.fail("not an install"))
    if operation == "project-update":
        def update(project, provider, **kwargs):
            seen.append((project, provider, kwargs["development"]))
            return {"status": "success", "up_to_date": True, "current_version": "fixture"}
        monkeypatch.setattr(gui, "update_project", update)
    else:
        def python(project, *args, **kwargs):
            seen.append((project, kwargs["mirror"]))
            return 0, "private Python retained", "ready"
        monkeypatch.setattr(gui, "prepare_python", python)
    app.start(operation)
    drain_until(app, lambda: not app.busy)
    assert len(seen) == 1 and app.last_code == 0
