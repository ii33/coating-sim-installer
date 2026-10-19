"""Terminal flow contracts: all external operations are mocked."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tools.dependency_manager import interactive as subject
from tools.dependency_manager.cli import parser


@pytest.fixture
def flow(monkeypatch, tmp_path):
    root = tmp_path / "install"
    identity = {"executable": "/python", "python": "3.12.10", "implementation": "CPython", "valid": True, "base_prefix": str(root / ".runtime/python")}
    calls = {}
    values = {
        "discover_git": "/git",
        "recommend_environment": {"status": "success", "selected_backend": "cpu"},
        "plan_project_deployment": {"status": "ready", "plan_id": "source"},
        "plan_environment": {"status": "ready", "plan_id": "dependencies"},
        "execute_project_deployment": {"status": "success"},
        "execute_plan": {"status": "success"},
        "plan_runtime": {"status": "ready", "action": "reuse", "plan_id": "python"},
        "execute_runtime": {"status": "success", "python": "/python", "identity": identity},
        "plan_git_runtime": {"status": "ready", "plan_id": "git"},
        "execute_git_runtime": {"status": "completed", "git": "/git"},
        "inspect_environment": {"status": "success", "target": identity},
        "plan_launch_entries": {"status": "ready"},
        "execute_launch_entries": {"status": "success"},
        "ensure_desktop_shortcut": {"status": "success", "path": "isolated QA desktop"},
    }
    for name, value in values.items():
        calls[name] = MagicMock(return_value=value)
        monkeypatch.setattr(subject, name, calls[name])

    def run(answers):
        responses = iter(answers)
        return subject.run_interactive(
            root, input_fn=lambda prompt: next(responses), output=lambda text: None
        )

    return root, calls, run


def make_project(path):
    path.mkdir(parents=True)
    (path / "requirements.txt").write_text("numpy\n")
    (path / "launcher.py").write_text("")
    (path / "coat_sim").mkdir()


def test_default_entry_is_terminal_without_tk():
    assert parser().parse_args([]).operation == "interactive"
    assert "tkinter" not in Path(subject.__file__).read_text(encoding="utf-8")


@pytest.mark.parametrize("cancel_in", ["recommend", "launch"])
def test_late_cancellation_never_creates_next_step_or_reports_success(flow, cancel_in):
    import threading

    root, calls, _ = flow
    cancel = threading.Event()
    identity = {"valid": True, "executable": "/python"}
    calls["inspect_environment"].return_value = {"target": identity}
    if cancel_in == "recommend":
        def recommend(*args, **kwargs):
            if "manual_backend" in kwargs:
                cancel.set()
                return {"status": "cancelled"}
            return {"status": "success", "selected_backend": "cpu"}

        calls["recommend_environment"].side_effect = recommend
    else:
        def launch(*args, **kwargs):
            cancel.set()
            return {"status": "success"}

        calls["execute_launch_entries"].side_effect = launch
    answers = iter(["", "", "", "", "y"])
    result = subject.run_interactive(root, input_fn=lambda prompt: next(answers),
                                     output=lambda text: None, cancel_event=cancel)
    assert result == 130
    if cancel_in == "recommend":
        calls["plan_launch_entries"].assert_not_called()
        calls["execute_launch_entries"].assert_not_called()


def test_default_cnb_full_runtime_one_install_confirmation(flow):
    root, calls, run = flow
    assert run(["", "", "", "", "y"]) == 0
    plan = calls["plan_project_deployment"].call_args
    assert plan.args == (str(root / "app"),)
    assert plan.kwargs["source"] == "cnb"
    assert plan.kwargs["repository_url"] == subject.CNB_URL
    assert plan.kwargs["groups"] == ("desktop", "appearance")
    assert plan.kwargs["profile"] == "runtime"
    assert plan.kwargs["mirror"] == "tuna"
    calls["execute_project_deployment"].assert_called_once()


@pytest.mark.parametrize("cancel_kind", ["event_only", "status_only"])
def test_second_compute_probe_cancellation_stops_before_launch(flow, cancel_kind):
    import threading

    root, calls, _ = flow
    cancel = threading.Event()
    actual = {"valid": True, "executable": "/python"}
    calls["inspect_environment"].return_value = {"target": actual}

    def recommend(identity, **kwargs):
        if "manual_backend" not in kwargs:
            return {"status": "success", "selected_backend": "cuda12"}
        assert identity is actual
        assert kwargs["manual_backend"] == "cuda12"
        assert kwargs["cancel_event"] is cancel
        if cancel_kind == "event_only":
            cancel.set()
            return {"status": "success", "selected_backend": "cuda12"}
        assert not cancel.is_set()
        return {"status": "cancelled"}

    calls["recommend_environment"].side_effect = recommend
    answers = iter(["", "", "", "", "y"])
    messages = []
    result = subject.run_interactive(
        root, input_fn=lambda prompt: next(answers), output=messages.append,
        cancel_event=cancel,
    )
    assert result == 130
    assert calls["recommend_environment"].call_count == 2
    calls["plan_launch_entries"].assert_not_called()
    calls["execute_launch_entries"].assert_not_called()
    assert not any(message.startswith("安装完成：") for message in messages)


def test_gui_review_includes_launch_intent_without_changing_executed_plan(flow):
    root, calls, _ = flow
    backend_plan = calls["plan_project_deployment"].return_value
    reviews = []
    answers = iter(["", "", "", "", "y"])
    assert subject.run_interactive(root, input_fn=lambda prompt: next(answers),
                                   output=lambda text: None, plan_fn=reviews.append) == 0
    review = next(row for row in reviews if row.get("operation") == "installation_review")
    assert review["installation_plan"] is backend_plan
    assert review["launch_entry_intent"] == {
        "path": str(root / "app" / ("launch-coating.cmd" if subject.sys.platform == "win32" else "launch-coating.sh")),
        "overwrite": False,
        "desktop_shortcuts": subject.sys.platform == "win32",
    }
    assert "launch_entry_intent" not in backend_plan
    assert calls["execute_project_deployment"].call_args.args[0] is backend_plan


def test_github_acceleration_and_development_are_independent(flow):
    root, calls, run = flow
    assert run(["", "2", "y", "y", "y"]) == 0
    plan = calls["plan_project_deployment"].call_args.kwargs
    assert (plan["source"], plan["repository_url"], plan["mirror"], plan["profile"]) == (
        "github",
        None,
        "tuna",
        "development",
    )


def test_python_first_preserves_runtime_and_clones_only_new_app(flow):
    root, calls, run = flow
    runtime = root / ".runtime" / "python"
    runtime.mkdir(parents=True)
    sentinel = runtime / "preserve"
    sentinel.write_text("owned runtime")
    assert run(["", "", "", "", "y"]) == 0
    assert sentinel.read_text(encoding="utf-8") == "owned runtime"
    assert calls["plan_project_deployment"].call_args.args[0] == str(root / "app")


@pytest.mark.parametrize("at_root", [True, False])
def test_existing_project_installs_dependencies_without_source_overwrite(flow, at_root):
    root, calls, run = flow
    make_project(root if at_root else root / "app")
    assert run(["", "", "", "", "y"]) == 0
    calls["plan_project_deployment"].assert_not_called()
    calls["execute_plan"].assert_called_once()


def test_unrelated_app_is_rejected_without_discovery_or_writes(flow):
    root, calls, run = flow
    (root / "app").mkdir(parents=True)
    assert run([""]) == 2
    calls["plan_runtime"].assert_not_called()


def test_declined_plan_never_installs_dependencies(flow):
    root, calls, run = flow
    assert run(["", "", "", "", "", ""]) == 130
    calls["execute_project_deployment"].assert_not_called()


def test_network_failure_never_executes_plan(flow):
    root, calls, run = flow
    calls["plan_project_deployment"].return_value = {"status": "failed", "errors": ["offline"]}
    assert run(["", "", "", "", ""]) == 2
    calls["execute_project_deployment"].assert_not_called()


def test_python_install_confirmation_and_project_boundary(flow):
    root, calls, run = flow
    calls["plan_runtime"].return_value["action"] = "extract"
    assert run(["", "", "", "", "y", "y"]) == 0
    assert calls["plan_runtime"].call_args.kwargs["project"] == root
    calls["execute_runtime"].assert_called_once()


def test_no_git_installs_only_after_explicit_tool_plan_confirmation(flow):
    root, calls, run = flow
    calls["discover_git"].return_value = None
    assert run(["", "", "", "", "y", "y"]) == 0
    assert calls["plan_git_runtime"].call_args.kwargs["project"] == root
    calls["execute_git_runtime"].assert_called_once()


def test_ctrl_c_returns_cancelled_without_installing(flow):
    root, calls, run = flow

    def interrupt(prompt):
        raise KeyboardInterrupt

    assert subject.run_interactive(root, input_fn=interrupt, output=lambda text: None) == 130
    calls["execute_project_deployment"].assert_not_called()


def test_missing_python_never_starts_source_or_dependency_install(flow):
    root, calls, run = flow
    calls["plan_runtime"].return_value = {"status": "blocked", "errors": ["unsupported private target"]}
    assert run(["", "", "", "", ""]) == 2
    calls["plan_project_deployment"].assert_not_called()
    calls["execute_plan"].assert_not_called()


def test_ctrl_c_during_execution_sets_core_cancellation_event(flow):
    import signal

    root, calls, run = flow
    previous = signal.getsignal(signal.SIGINT)

    def cancelled(*args, **kwargs):
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)
        assert kwargs["cancel_event"].is_set()
        return {"status": "cancelled"}

    calls["execute_project_deployment"].side_effect = cancelled
    assert run(["", "", "", "", "y"]) == 130
    assert signal.getsignal(signal.SIGINT) == previous


def test_empty_existing_install_root_is_valid(flow):
    root, calls, run = flow
    root.mkdir()
    assert run(["", "", "", "", "y"]) == 0
    assert calls["plan_project_deployment"].call_args.args[0] == str(root / "app")


def test_existing_venv_uses_compatible_compute_recommendation_without_python_replacement(flow):
    root, calls, run = flow
    make_project(root / "app")
    (root / "app" / ".venv").mkdir()
    calls["recommend_environment"].return_value = {
        "status": "success",
        "selected_backend": "cuda12",
        "backend_usable": {"cuda12": True},
    }
    assert run(["", "", "", "", "y"]) == 0
    plan = calls["plan_environment"].call_args.args
    assert plan[3] == "cuda12" and plan[6] == "/python"
    calls["execute_runtime"].assert_called_once()


def test_launch_script_creation_is_included_and_existing_files_are_preserved(flow):
    root, calls, run = flow
    calls["plan_launch_entries"].return_value = {
        "status": "blocked",
        "errors": ["existing script preserved"],
    }
    assert run(["", "", "", "", "y"]) == 2
    calls["execute_launch_entries"].assert_not_called()
    calls["plan_launch_entries"].assert_called_once_with(root / "app", name="launch-coating")


def test_human_output_is_concise_and_json_details_are_project_local(flow):
    root, calls, run = flow
    answers = iter(["", "", "", "", "y"])
    lines: list[str] = []
    assert (
        subject.run_interactive(root, input_fn=lambda prompt: next(answers), output=lines.append)
        == 0
    )
    assert not any(line.startswith("{\n") for line in lines)
    assert list((root / ".dependency-manager" / "logs").glob("installer-*.json"))
    assert any("启动脚本" in line for line in lines)


def test_post_install_verifies_actual_target_and_does_not_reinstall_on_gpu_failure(flow):
    root, calls, run = flow
    actual = {
        "valid": True,
        "executable": str(root / "app" / ".venv" / "bin" / "python"),
        "python": "3.12.9",
    }
    calls["inspect_environment"].return_value = {"status": "success", "target": actual}
    calls["recommend_environment"].side_effect = [
        {"status": "success", "selected_backend": "cuda12"},
        {"status": "success", "selected_backend": "cuda12", "backend_usable": {"cuda12": False}},
    ]
    assert run(["", "", "", "", "y"]) == 0
    assert calls["recommend_environment"].call_args.args == (actual,)
    assert calls["recommend_environment"].call_args.kwargs["manual_backend"] == "cuda12"
    calls["execute_project_deployment"].assert_called_once()


@pytest.mark.parametrize("approve_git", [False, True])
def test_real_windows_git_plan_source_string_is_displayed_without_crashing(flow, approve_git):
    # This is the real read-only plan, including its vendor source string,
    # official artifact/hash/license and parent identity. Never download Git.
    from tools.dependency_manager.git_runtime import plan_git_runtime

    root, calls, run = flow
    plan = plan_git_runtime(project=root, platform_name="win32", machine="AMD64")
    assert plan["status"] == "ready"
    assert isinstance(plan["source"], str)
    calls["discover_git"].return_value = None
    calls["plan_git_runtime"].return_value = plan
    answers = iter(["", "", "", "", "y" if approve_git else "", "y"])
    lines: list[str] = []
    code = subject.run_interactive(root, input_fn=lambda prompt: next(answers), output=lines.append)
    assert code == (0 if approve_git else 130)
    assert "工具来源：" + plan["source"] in lines
    assert any(plan["artifact"]["sha256"] in line for line in lines)
    assert any(plan["license"] in line for line in lines)
    assert calls["execute_git_runtime"].call_count == int(approve_git)
    if approve_git:
        assert calls["execute_git_runtime"].call_args.args[0] == plan
        calls["execute_project_deployment"].assert_called_once()
    else:
        calls["execute_project_deployment"].assert_not_called()


def test_project_source_mapping_still_displays_commit(flow):
    root, calls, run = flow
    source = {"url": subject.CNB_URL, "commit": "a" * 40}
    calls["plan_project_deployment"].return_value = {"status": "ready", "source": source}
    answers = iter(["", "", "", "", "y"])
    lines: list[str] = []
    assert (
        subject.run_interactive(root, input_fn=lambda prompt: next(answers), output=lines.append)
        == 0
    )
    assert f"源码：{source['url']} @ {source['commit']}" in lines


def test_optional_display_fields_do_not_assume_mapping_or_list(flow):
    root, calls, run = flow
    calls["recommend_environment"].return_value = {
        "status": "success",
        "selected_backend": "cpu",
        "python": "/python",
        "visible_gpus": ["unclassified device", {"name": "example GPU"}],
        "errors": None,
        "warnings": "single warning",
    }
    calls["plan_project_deployment"].return_value = {
        "status": "blocked",
        "dependency_plan": "not a nested plan",
        "target": "/target",
        "artifact": "not an artifact mapping",
        "actions": None,
        "packages": None,
        "errors": "blocked reason",
        "warnings": None,
    }
    answers = iter(["", "", "", "", ""])
    lines: list[str] = []
    assert (
        subject.run_interactive(root, input_fn=lambda prompt: next(answers), output=lines.append)
        == 2
    )
    assert "single warning" in lines and "blocked reason" in lines
    assert any("unclassified device" in line and "example GPU" in line for line in lines)
    calls["execute_project_deployment"].assert_not_called()


def test_repeat_install_reuses_identical_launcher_only(flow):
    root, calls, run = flow
    make_project(root / "app")
    script = root / "app" / ("launch-coating.cmd" if subject.sys.platform == "win32" else "launch-coating.sh")
    script.write_bytes(b"known script")
    calls["plan_launch_entries"].return_value = {"status": "blocked", "script": "known script",
        "errors": ["Existing launch entry is preserved; choose another name: " + str(script)]}
    assert run(["", "", "", "", "y"]) == 0
    assert script.read_bytes() == b"known script"
    calls["execute_launch_entries"].assert_not_called()
    script.write_bytes(b"user changed")
    assert run(["", "", "", "", "y"]) == 2
    assert script.read_bytes() == b"user changed"


def test_failed_launch_creation_is_not_reported_as_install_success(flow):
    root, calls, run = flow
    calls["execute_launch_entries"].return_value = {"status": "failed", "errors": ["disk full"]}
    assert run(["", "", "", "", "y"]) == 2


def test_verified_python_extract_overlaps_git_and_source_waits(flow):
    import threading
    root, calls, _ = flow
    git_started = threading.Event()
    python_done = threading.Event()
    calls["discover_git"].return_value = None
    original_python = calls["execute_runtime"].return_value

    def python(plan, confirmed, emit, cancel, proxy):
        assert not git_started.is_set()
        emit({"event": "phase", "phase": "runtime_extract"})
        assert git_started.wait(2)
        python_done.set()
        return original_python

    def git(plan, confirmed, emit, cancel, proxy):
        git_started.set()
        assert python_done.wait(2)
        return {"status": "completed", "git": "/git"}

    def source(*args, **kwargs):
        assert python_done.is_set() and git_started.is_set()
        assert kwargs["git_path"] == "/git"
        return {"status": "ready"}

    calls["execute_runtime"].side_effect = python
    calls["execute_git_runtime"].side_effect = git
    calls["plan_project_deployment"].side_effect = source
    choices = {"directory": str(root), "source": "1", "mirror": "y", "development": "n", "python": "y", "git": "y", "install": "y"}
    assert subject.run_interactive(root, prompt_fn=lambda key,*unused: choices[key], output=lambda value: None, pipeline=True) == 0


def test_pipeline_failure_joins_owned_worker_without_turning_failure_into_cancel(flow):
    import threading
    root, calls, _ = flow
    started, stopped = threading.Event(), threading.Event()
    cancel = threading.Event()
    calls["discover_git"].return_value = None

    def python(plan, confirmed, emit, cancel, proxy):
        emit({"event": "phase", "phase": "runtime_extract"})
        assert started.wait(2)
        return {"status": "failed", "errors": ["bad runtime archive"]}

    def git(plan, confirmed, emit, local_cancel, proxy):
        started.set()
        for _ in range(200):
            if local_cancel.is_set():
                stopped.set()
                return {"status": "cancelled"}
            stopped.wait(0.01)
        raise AssertionError("pipeline worker leaked")

    calls["execute_runtime"].side_effect = python
    calls["execute_git_runtime"].side_effect = git
    choices = {"directory": str(root), "source": "1", "mirror": "y", "development": "n", "python": "y", "git": "y", "install": "y"}
    assert subject.run_interactive(root, prompt_fn=lambda key,*unused: choices[key], output=lambda value: None, pipeline=True, cancel_event=cancel) == 2
    assert stopped.is_set() and not cancel.is_set()
    calls["plan_project_deployment"].assert_not_called()


def test_install_shortcut_failure_is_noncritical_success_with_warning(flow, monkeypatch):
    # A desktop shortcut failure must not fail or undo a completed installation.
    root, calls, _ = flow
    monkeypatch.setattr(subject.sys, "platform", "win32")
    calls["ensure_desktop_shortcut"].return_value = {"status": "failed", "errors": ["桌面写入被拒绝"]}
    events, messages = [], []
    answers = iter(["", "", "", "", "y"])
    assert subject.run_interactive(root, input_fn=lambda p: next(answers),
                                   output=messages.append, event_fn=events.append) == 0
    done = next(row for row in events if row.get("event") == "completion")
    assert done["shortcut"]["status"] == "failed"
    assert "桌面写入被拒绝" in done["shortcut"]["errors"]
    assert any("安装完成" in message for message in messages)
    assert any("桌面快捷方式未完成" in message and "桌面写入被拒绝" in message for message in messages)
    calls["execute_project_deployment"].assert_called_once()


def test_raising_shortcut_is_noncritical_on_completed_install(flow, monkeypatch):
    # An unexpected ordinary Exception (e.g. an injected Windows API error) from
    # the shortcut step must not fail the completed installation; the boundary
    # converts it into a redacted failed payload and the install still succeeds.
    root, calls, _ = flow
    monkeypatch.setattr(subject.sys, "platform", "win32")
    calls["ensure_desktop_shortcut"].side_effect = OSError(
        "desktop API failed at https://user:pass@example.invalid/desktop")
    events, messages = [], []
    answers = iter(["", "", "", "", "y"])
    assert subject.run_interactive(root, input_fn=lambda prompt: next(answers),
                                   output=messages.append, event_fn=events.append) == 0
    done = next(row for row in events if row.get("event") == "completion")
    assert done["shortcut"]["status"] == "failed"
    error = "; ".join(done["shortcut"]["errors"])
    assert "OSError" in error and "desktop API failed" in error
    assert "example.invalid" in error and "user:pass" not in error
    assert any("安装完成" in message for message in messages)
    assert any("桌面快捷方式未完成" in message for message in messages)
    calls["execute_project_deployment"].assert_called_once()


def test_shortcut_keyboard_interrupt_is_not_converted_into_install_success(flow, monkeypatch):
    # BaseException is not swallowed by the noncritical boundary: cancellation wins.
    root, calls, _ = flow
    monkeypatch.setattr(subject.sys, "platform", "win32")
    calls["ensure_desktop_shortcut"].side_effect = KeyboardInterrupt
    answers = iter(["", "", "", "", "y"])
    assert subject.run_interactive(root, input_fn=lambda prompt: next(answers),
                                   output=lambda value: None) == 130


def test_install_completion_event_contains_verified_desktop_status(flow):
    root, calls, _ = flow
    calls["ensure_desktop_shortcut"].return_value = {"status": "success", "path": "test-desktop/link.lnk", "reused": True}
    events = []
    answers = iter(["", "", "", "", "y"])
    assert subject.run_interactive(root, input_fn=lambda p: next(answers), output=lambda v: None, event_fn=events.append) == 0
    done = next(row for row in events if row.get("event") == "completion")
    assert done["target"] == str(root / "app")
    if subject.sys.platform == "win32":
        assert done["shortcut"]["reused"] is True


def test_auto_cuda_base_plan_and_optional_plan_are_distinct(flow):
    root, calls, _ = flow
    calls["recommend_environment"].return_value = {"selected_backend": "cuda13"}
    calls["execute_plan"].return_value = {"status": "success", "compute_validation": {
        "status": "success", "recommended_backend": "cuda13", "backend_usable": {"cuda13": True}}}
    reviews, events = [], []
    answers = iter(["", "", "", "", "y"])
    assert subject.run_interactive(root, input_fn=lambda _: next(answers), output=lambda _: None,
        plan_fn=reviews.append, event_fn=events.append) == 0
    assert calls["plan_project_deployment"].call_args.kwargs["backend"] == "cpu"
    assert calls["plan_environment"].call_args.args[3] == "cuda13"
    route = next(v for v in events if v["event"] == "completion")["compute_route"]
    assert route["requested_backend"] == "auto" and route["planned_backend"] == "cuda13"
    assert route["usable_backend"] == "cuda13"
    assert any(v.get("operation") == "compute_route" and "auto" in v["summary"] for v in reviews)


def test_auto_late_device_rejection_keeps_base_without_executing_cuda(flow):
    root, calls, _ = flow
    calls["recommend_environment"].side_effect = [
        {"selected_backend": "cuda13"},
        {"status": "success", "recommended_backend": "cpu", "backend_usable": {"cuda13": False}}]
    calls["plan_environment"].return_value = {"status": "blocked", "errors": ["driver became unavailable"]}
    events=[]
    answers=iter(["", "", "", "", "y"])
    assert subject.run_interactive(root,input_fn=lambda _:next(answers),output=lambda _:None,event_fn=events.append)==0
    calls["execute_project_deployment"].assert_called_once()
    calls["execute_plan"].assert_not_called()
    route=next(v for v in events if v["event"]=="completion")["compute_route"]
    assert route["planned_backend"]=="cpu" and route["usable_backend"]=="cpu"
    assert "driver became unavailable" in route["fallback_reason"]


@pytest.mark.parametrize("base_valid,expected", [(True,0),(False,2)])
def test_auto_cuda_writer_failure_requires_verified_intact_base(flow,base_valid,expected):
    root,calls,_=flow
    calls["recommend_environment"].side_effect=[{"selected_backend":"cuda13"},
        {"status":"success","recommended_backend":"cpu","backend_usable":{"cuda13":False}}]
    calls["execute_plan"].return_value={"status":"failed","errors":["CUDA resolver unavailable"]}
    calls["inspect_environment"].return_value={"target":{"valid":True,"executable":"/python"},
        "errors":[],"packages":[{"name":"numpy","status":"satisfied" if base_valid else "missing"}]}
    answers=iter(["", "", "", "", "y"])
    assert subject.run_interactive(root,input_fn=lambda _:next(answers),output=lambda _:None)==expected
    if not base_valid:
        calls["execute_launch_entries"].assert_not_called()


@pytest.mark.parametrize("identity_valid", [True, False])
def test_manual_cuda_does_not_fallback_or_claim_failed_gpu_success(flow, identity_valid):
    root,calls,_=flow
    calls["recommend_environment"].side_effect=[{"selected_backend":"cuda13"},
        {"status":"success","recommended_backend":"cpu","backend_usable":{"cuda13":False}}]
    if not identity_valid:
        calls["inspect_environment"].return_value={"target":{"valid":False},"errors":["identity unavailable"]}
    messages=[]
    answers=iter(["", "", "", "", "y"])
    assert subject.run_interactive(root,backend="cuda13",input_fn=lambda _:next(answers),output=messages.append)==2
    assert calls["plan_project_deployment"].call_args.kwargs["backend"]=="cuda13"
    calls["plan_environment"].assert_not_called()
    calls["execute_launch_entries"].assert_not_called()
    assert any("auto" in text and "cpu" in text for text in messages)


def test_optional_cuda_cancellation_is_never_cpu_success(flow):
    root,calls,_=flow
    calls["recommend_environment"].return_value={"selected_backend":"cuda13"}
    calls["execute_plan"].return_value={"status":"cancelled"}
    answers=iter(["", "", "", "", "y"])
    assert subject.run_interactive(root,input_fn=lambda _:next(answers),output=lambda _:None)==130
    calls["execute_launch_entries"].assert_not_called()
