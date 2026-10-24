"""Default source-first orchestration, with inert runtimes and mocked I/O."""
import os
import shutil
import subprocess
import threading
from unittest.mock import MagicMock

import pytest

from tools.dependency_manager import default_install as install
from tools.dependency_manager import manager, python_discovery
from tools.dependency_manager.mirrors import ProxyConfig


@pytest.fixture
def flow(tmp_path, monkeypatch):
    root = tmp_path / "光学 仿真 安装"
    app = root / "app"
    calls, order, messages, events, reviews, phases = {}, [], [], [], [], []
    monkeypatch.setattr(install.sys, "platform", "win32")
    monkeypatch.setattr(python_discovery, "discover_python", lambda *a, **k: pytest.fail("external Python discovery"))
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: pytest.fail("unverified interpreter probe"))
    def source(*args, **kwargs):
        order.append("source")
        (app / "coat_sim").mkdir(parents=True)
        (app / ".git").mkdir()
        for name in ("requirements.txt", "requirements-test.txt", "launcher.py", "coat_sim/compute_runtime_manifest.py"):
            (app / name).write_text("# inert\n", encoding="utf-8")
        return {"status": "ready", "source": {"commit": "a" * 40}}
    state = {"repository": "ii33/coating-sim", "provider": "cnb", "url": install.CNB_URL,
             "commit": "a" * 40, "source_inputs": []}
    def checkout(*args):
        order.append("source_auth")
        return dict(state)
    def runtime_plan(*args, **kwargs):
        order.append("python_plan")
        assert kwargs["project"] == app
        return {"status": "ready", "action": "extract", "plan_id": "private"}
    def runtime(*args):
        order.append("python_install")
        (app / ".runtime/python").mkdir(parents=True, exist_ok=True)
        return {"status": "success", "python": str(app / ".runtime/python/python.exe")}
    def base(path):
        order.append("base_auth")
        assert path == app
        return {}, {}
    def roles_plan(path, **kwargs):
        order.append("roles_plan")
        assert path == app and "base_auth" in order
        selected = "cuda13" if kwargs["cuda_backend"] == "auto" else (
            kwargs["cuda_backend"] if kwargs["cuda_backend"].startswith("cuda") else None)
        roles = {"app": "app", "deposition_cpu": "app", "deposition_directml": "deposition_directml", "appearance": "appearance"}
        if selected:
            roles["deposition_cuda"] = "deposition_cuda"
        return {"status": "ready", "plan_id": "roles", "selected_cuda": selected, "roles": roles}
    def roles_execute(plan, confirmed, emit, cancel, proxy):
        order.append("roles_install")
        assert confirmed
        (app / ".venv/Scripts").mkdir(parents=True, exist_ok=True)
        (app / ".venv/Scripts/python.exe").write_bytes(b"inert redirector")
        (app / ".venv/pyvenv.cfg").write_text(f"home = {app / '.runtime/python'}\n"
            f"executable = {app / '.runtime/python/python.exe'}\ninclude-system-site-packages = false\n", encoding="utf-8")
        return {"status": "success", "roles": plan["roles"], "components": [
            {"id": "deposition_directml", "result": {"compute_validation": {"backend_usable": {"directml": True}}}},
            {"id": "deposition_cuda", "result": {"compute_validation": {"backend_usable": {"cuda13": True, "cuda12": True}}}}]}
    values = {
        "discover_git": "/private/git", "plan_git_runtime": {"status": "ready"},
        "execute_git_runtime": {"status": "completed", "git": "/private/git"},
        "plan_runtime": runtime_plan, "execute_runtime": runtime, "_private_base": base,
        "_checkout_state": checkout, "plan_runtime_roles": roles_plan, "execute_runtime_roles": roles_execute,
        "_authenticate_environment": lambda *a: order.append("app_auth"),
        "plan_launch_entries": {"status": "ready"}, "execute_launch_entries": {"status": "success"},
        "ensure_desktop_shortcut": {"status": "success", "path": "verified desktop"},
    }
    for name, value in values.items():
        calls[name] = MagicMock(side_effect=value) if callable(value) else MagicMock(return_value=value)
        monkeypatch.setattr(install, name, calls[name])
    calls["prepare_project_source"] = MagicMock(side_effect=source)
    monkeypatch.setattr(install.source_tools, "prepare_project_source", calls["prepare_project_source"])
    def run(**kwargs):
        return install.run_default_installation(root, output=messages.append, prompt_fn=lambda key: "y",
            phase_fn=phases.append, plan_fn=reviews.append, event_fn=events.append, **kwargs)
    return root, app, calls, order, messages, events, reviews, phases, run


def test_default_installs_all_roles_source_first_under_private_app_base(flow):
    root, app, calls, order, messages, events, reviews, phases, run = flow
    assert run() == 0
    assert order[:3] == ["source", "source_auth", "python_plan"]
    assert order.index("base_auth") < order.index("roles_plan") < order.index("roles_install")
    assert calls["plan_runtime"].call_args.kwargs["project"] == app
    assert not (root / ".runtime/python").exists()
    assert calls["prepare_project_source"].call_args.kwargs["source"] == "cnb"
    assert calls["plan_runtime_roles"].call_args.kwargs["cuda_backend"] == "auto"
    completed = [event for event in events if event.get("event") == "completion"]
    assert len(completed) == 1
    roles = completed[0]["runtime_roles"]
    assert roles["deposition_cpu"] == "app" and "deposition_directml" in roles and "deposition_cuda" in roles
    assert completed[0]["compute_route"]["backend_usable"]["cuda13"] is True
    assert phases == ["source", "python", "recommend", "dependencies", "verify", "launch"]
    assert any("完整原生依赖闭包" in text for text in messages)
    assert next(x for x in reviews if x.get("operation") == "installation_review")["installation_plan"]["plan_id"] == "roles"


@pytest.mark.parametrize("requested,expected", [("cpu", "cpu"), ("directml", "cpu"), ("cuda12", "cuda12"), ("cuda13", "cuda13")])
def test_manual_backend_never_removes_independent_directml(flow, requested, expected):
    _, _, calls, _, _, events, _, _, run = flow
    assert run(backend=requested) == 0
    assert calls["plan_runtime_roles"].call_args.kwargs["cuda_backend"] == expected
    assert "deposition_directml" in events[-1]["runtime_roles"]


def test_source_choice_and_development_are_immutable_arguments(flow):
    _, _, calls, _, _, _, _, _, run = flow
    assert run(source="github", development=True, mirror="pypi") == 0
    assert calls["prepare_project_source"].call_args.kwargs["repository_url"] is None
    assert calls["plan_runtime_roles"].call_args.kwargs["profile"] == "development"
    assert calls["plan_runtime_roles"].call_args.kwargs["mirror"] == "pypi"
    assert calls["plan_runtime"].call_args.kwargs["runtime_mirror"] is False


def test_frozen_frontend_is_not_used_as_application_python(flow, monkeypatch):
    _, app, calls, _, _, _, _, _, run = flow
    monkeypatch.setattr(install.sys, "frozen", True, raising=False)
    monkeypatch.setattr(install.sys, "executable", "/outside/Installer.exe")
    assert run() == 0
    assert calls["plan_runtime"].call_args.kwargs["project"] == app
    assert calls["plan_runtime_roles"].call_args.args == (app,)


def test_role_failure_is_actionable_and_retry_preserves_prepared_source(flow):
    _, app, calls, _, messages, events, _, _, run = flow
    original = calls["execute_runtime_roles"].side_effect
    calls["execute_runtime_roles"].side_effect = None
    calls["execute_runtime_roles"].return_value = {"status": "failed", "errors": ["role failed"], "components": [
        {"id": "deposition_directml", "result": {"errors": ["pip resolver unavailable"]}}]}
    assert run() == 2
    assert not events or not any(x.get("event") == "completion" for x in events)
    assert any("deposition_directml: pip resolver unavailable" in x for x in messages)
    calls["plan_launch_entries"].assert_not_called()
    calls["execute_runtime_roles"].side_effect = original
    assert run() == 0
    assert app.is_dir() and calls["prepare_project_source"].call_count == 1


@pytest.mark.parametrize("cancel_stage", ["source", "python", "roles"])
def test_cancellation_never_reports_success_or_starts_next_writer(flow, cancel_stage):
    _, _, calls, _, messages, events, _, _, run = flow
    cancel = threading.Event()
    name = {"source": "prepare_project_source", "python": "execute_runtime", "roles": "execute_runtime_roles"}[cancel_stage]
    original = calls[name].side_effect
    def cancelled(*args, **kwargs):
        result = original(*args, **kwargs)
        cancel.set()
        return result
    calls[name].side_effect = cancelled
    assert run(cancel_event=cancel) == 130
    assert not any(x.get("event") == "completion" for x in events)
    calls["plan_launch_entries"].assert_not_called()
    assert not any(x.startswith("安装完成：") for x in messages)
    if cancel_stage != "roles":
        calls["execute_runtime_roles"].assert_not_called()


def test_emitter_does_not_unwind_a_live_subprocess_on_cancel(flow):
    _, _, calls, _, _, _, _, _, run = flow
    cancel = threading.Event()
    def execute(plan, confirmed, emit, event, proxy):
        event.set()
        emit({"event": "progress", "phase": "pip_install", "text": "runner is reaping its child"})
        return {"status": "cancelled"}
    calls["execute_runtime_roles"].side_effect = execute
    assert run(cancel_event=cancel) == 130


def test_installed_gpu_packages_never_become_readiness_evidence(flow):
    _, _, calls, _, messages, events, _, _, run = flow
    original = calls["execute_runtime_roles"].side_effect
    def installed_only(*args):
        result = original(*args)
        result["components"] = []
        return result
    calls["execute_runtime_roles"].side_effect = installed_only
    assert run() == 0
    assert events[-1]["compute_route"]["backend_usable"] == {}
    assert "未确认 GPU" in events[-1]["compute_route"]["usable_backend"]
    assert run(backend="cuda13") == 2
    assert any("手选 cuda13" in text for text in messages)


def test_foreign_sibling_base_is_rejected_before_python_or_role_probe(flow):
    root, app, calls, _, messages, _, _, _, run = flow
    calls["prepare_project_source"](str(app))
    (app / ".venv").mkdir()
    (app / ".venv/pyvenv.cfg").write_text(f"home = {root / '.runtime/python'}\n"
        f"executable = {root / '.runtime/python/python.exe'}\ninclude-system-site-packages = false\n", encoding="utf-8")
    calls["prepare_project_source"].reset_mock()
    assert run() == 2
    calls["plan_runtime"].assert_not_called()
    calls["plan_runtime_roles"].assert_not_called()
    assert any("旧同级 Python" in text and "新安装目录" in text for text in messages)


def test_source_changes_after_base_auth_block_dependency_writer(flow):
    _, _, calls, _, messages, _, _, _, run = flow
    initial = {"commit": "a" * 40}
    calls["_checkout_state"].side_effect = [initial, {"commit": "b" * 40}]
    assert run() == 2
    calls["plan_runtime_roles"].assert_not_called()
    assert any("发生变化" in text for text in messages)


def test_default_path_has_no_legacy_dependency_fallback(flow, monkeypatch):
    _, _, calls, _, _, _, _, _, run = flow
    monkeypatch.setattr(manager, "plan_environment", lambda *a, **k: pytest.fail("legacy one-env planning"))
    calls["plan_runtime_roles"].side_effect = None
    calls["plan_runtime_roles"].return_value = {"status": "blocked", "errors": ["CUDA candidate is unsupported"]}
    assert run() == 2
    calls["execute_runtime_roles"].assert_not_called()


def test_readonly_checkout_auth_rejects_unknown_origin_before_interpreter(tmp_path, monkeypatch):
    app = tmp_path / "源码 空格"
    (app / ".git").mkdir(parents=True)
    calls = []
    def git(executable, arguments, **kwargs):
        calls.append(arguments)
        return "https://unrelated.example/repository.git\n"
    monkeypatch.setattr(install.source_tools, "_run_git", git)
    with pytest.raises(ValueError, match="现有源码来源"):
        install._checkout_state(app, "/git", None, threading.Event(), lambda event: None)
    assert calls == [["remote", "get-url", "origin"]]


def test_real_git_checkout_auth_is_readonly_and_rejects_tracked_mutation(tmp_path):
    git = shutil.which("git")
    if not git:
        pytest.skip("Git is required for this read-only checkout boundary test")
    app = tmp_path / "真实 源码 检查"
    app.mkdir()
    def run(*args):
        return subprocess.run([git, "-c", "core.hooksPath=" + os.devnull,
            "-c", "commit.gpgsign=false", *args], cwd=app, capture_output=True, text=True, check=True)
    run("init", "--quiet", "--template=")
    run("remote", "add", "origin", install.source_tools.REPOSITORY_URL)
    (app / "coat_sim").mkdir()
    for relative in ("requirements.txt", "requirements-test.txt", "launcher.py", "coat_sim/compute_runtime_manifest.py"):
        (app / relative).write_text("# inert source; never imported\n", encoding="utf-8", newline="\n")
    run("add", ".")
    run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "inert source")
    (app / "保留 用户数据.txt").write_text("not executable", encoding="utf-8")
    before = {p.relative_to(app).as_posix(): p.read_bytes() for p in app.rglob("*") if p.is_file() and ".git" not in p.relative_to(app).parts}
    state = install._checkout_state(app, git, ProxyConfig("none"), threading.Event(), lambda event: None)
    assert state["commit"] == run("rev-parse", "HEAD").stdout.strip()
    assert {p.relative_to(app).as_posix(): p.read_bytes() for p in app.rglob("*") if p.is_file() and ".git" not in p.relative_to(app).parts} == before
    (app / "launcher.py").write_text("# changed source\n", encoding="utf-8")
    with pytest.raises(ValueError, match="本地源码修改"):
        install._checkout_state(app, git, ProxyConfig("none"), threading.Event(), lambda event: None)
    run("update-index", "--assume-unchanged", "launcher.py")
    with pytest.raises(ValueError, match="隐藏源码修改检查"):
        install._checkout_state(app, git, ProxyConfig("none"), threading.Event(), lambda event: None)


def test_readonly_status_private_exclusion_is_explicit_and_filters_stay_disabled():
    commands = []
    def run(arguments):
        commands.append(arguments)
        return "filter.fixture.clean\0" if arguments[0] == "config" else ""
    install.source_tools._readonly_status(run, exclude_private_runtimes=True)
    status = commands[-1]
    assert "filter.fixture.clean=" in status
    assert status[-4:] == ["--", ".", ":(top,exclude).runtime", ":(top,exclude).venv"]
    assert "--untracked-files=all" in status
