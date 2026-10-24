"""Safe managed-role updates use the default coordinator, never legacy groups."""
import base64
import json
import os
import shutil
import subprocess
import threading
from unittest.mock import MagicMock

import pytest

from tools.dependency_manager import manager as legacy_manager
from tools.dependency_manager import project_update as updater
from tools.dependency_manager.mirrors import ProxyConfig
from tools.dependency_manager.project_operations import save_record


@pytest.fixture
def managed(tmp_path, monkeypatch):
    git = os.environ.get("COATING_TEST_GIT") or shutil.which("git")
    if not git:
        pytest.skip("Git unavailable")
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    def command(directory, *arguments):
        return subprocess.check_output([git, "-c", "user.name=Installer QA",
            "-c", "user.email=qa@example.invalid", "-c", "commit.gpgsign=false",
            "-C", str(directory), *arguments], env=environment, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8").strip()
    remote = tmp_path / "受控 remote 源码"
    remote.mkdir()
    command(remote, "init", "--quiet", "--initial-branch=main", "--template=")
    (remote / "coat_sim").mkdir()
    files = {"coat_sim/version.txt": "dev.20261024.1\n", "launcher.py": "# inert fixture\n",
        "requirements.txt": "", "requirements-test.txt": "",
        "coat_sim/compute_runtime_manifest.py": "# inert contract fixture\n", "source.py": "value = 1\n",
        ".gitignore": ".venv/\n.runtime/\nconfig/\ndata/\nlaunch-coating.cmd\n"}
    for relative, text in files.items():
        (remote / relative).write_text(text, encoding="utf-8")
    command(remote, "add", ".")
    command(remote, "commit", "--quiet", "-m", "initial synthetic source")
    container = tmp_path / "光学 仿真 隔离安装"
    container.mkdir()
    app = container / "app"
    command(container, "clone", "--quiet", "--no-hardlinks", str(remote), str(app))
    command(app, "remote", "set-url", "origin", updater.deploy.REPOSITORY_URL)
    (app / ".runtime/python").mkdir(parents=True)
    (app / ".venv").mkdir()
    (app / "config").mkdir()
    (app / "data").mkdir()
    kept = {".runtime/python/keep.bin": b"private-base", ".venv/keep.bin": b"private-app",
        "config/settings.json": "用户配置".encode("utf-8"), "data/result.bin": b"user-result"}
    for relative, content in kept.items():
        (app / relative).write_bytes(content)
    before = command(app, "rev-parse", "HEAD")
    journal = app / ".git/coating-installer-update.json"
    calls, events = [], []
    real_run = updater.deploy._run_git
    def transport(executable, arguments, **kwargs):
        arguments = list(arguments)
        calls.append(arguments)
        if "--get-url" in arguments:
            return arguments[-1] + "\n"
        mapped = [str(remote) if value in (updater.deploy.REPOSITORY_URL, updater.CNB_URL)
                  else value for value in arguments]
        if "fetch" in arguments or "ls-remote" in arguments:
            mapped = ["-c", "protocol.file.allow=always", *mapped]
        return real_run(executable, mapped, **kwargs)
    monkeypatch.setattr(updater.deploy, "_run_git", transport)
    monkeypatch.setattr(updater, "discover_git", lambda **kwargs: git)
    def legacy(*args, **kwargs):
        pytest.fail("managed installation must not use legacy END_USER_GROUPS or one-env probes")
    for method in ("inspect_environment", "plan_environment", "execute_plan"):
        monkeypatch.setattr(legacy_manager, method, legacy)
    desktop = MagicMock(side_effect=lambda *a, **k: pytest.fail("coordinator already prepares shortcut"))
    monkeypatch.setattr(updater.default_install, "ensure_desktop_shortcut", desktop)
    completion = {"event": "completion", "operation": "install", "target": str(app),
        "compute_route": {"operation": "compute_route", "requested_backend": "auto",
            "usable_backend": "未确认 GPU；应用/CPU 依赖已核验", "backend_usable": {}},
        "runtime_roles": {"app": "app", "deposition_cpu": "app",
            "deposition_directml": "deposition_directml", "appearance": "appearance",
            "deposition_cuda": "deposition_cuda"},
        "manifest": str(app / ".runtime/compute-runtimes.json"),
        "shortcut": {"status": "success", "path": "隔离 桌面/Coating Sim.lnk"}}
    def install(root, **kwargs):
        assert root == container
        assert (container / ".app.coating-installer.lock").is_file()
        receipt = json.loads(journal.read_text(encoding="utf-8"))
        assert receipt["state"] == "applying" and receipt["runtime_layout"] == "managed_roles"
        for key in ("python", "git", "install"):
            assert kwargs["prompt_fn"](key) == "y"
        kwargs["plan_fn"](completion["compute_route"])
        kwargs["event_fn"](dict(completion))
        return 0
    real_coordinator = updater.default_install.run_default_installation
    coordinator = MagicMock(side_effect=install)
    monkeypatch.setattr(updater.default_install, "run_default_installation", coordinator)
    def newer(extra=None):
        (remote / "source.py").write_text("value = 2\n", encoding="utf-8")
        (remote / "coat_sim/version.txt").write_text("dev.20261024.2\n", encoding="utf-8")
        if extra:
            path = remote / extra
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("remote content\n", encoding="utf-8")
            command(remote, "add", "-f", extra)
        command(remote, "add", ".")
        command(remote, "commit", "--quiet", "-m", "new synthetic source")
        return command(remote, "rev-parse", "HEAD")
    def update(**kwargs):
        return updater.update_project(container, proxy=ProxyConfig("none"), emit=events.append, **kwargs)
    def preserved():
        for relative, content in kept.items():
            assert (app / relative).read_bytes() == content
    return locals()


def test_forward_managed_update_uses_shared_coordinator_and_current_gpu_evidence(managed):
    f = managed
    latest = f["newer"]()
    cancel = threading.Event()
    result = f["update"](provider="github", mirror="pypi", backend="cuda13", development=True,
        cancel_event=cancel)
    assert result["status"] == "success" and result["current_commit"] == latest, result
    assert result["runtime_layout"] == "managed_roles"
    kwargs = f["coordinator"].call_args.kwargs
    assert kwargs["source"] == "github" and kwargs["mirror"] == "pypi"
    assert kwargs["backend"] == "cuda13" and kwargs["development"] is True
    assert kwargs["cancel_event"] is cancel
    assert result["runtime_roles"] == f["completion"]["runtime_roles"]
    assert result["manifest"] == f["completion"]["manifest"]
    assert result["compute_route"]["backend_usable"] == {}
    assert "未确认 GPU" in result["compute_route"]["usable_backend"]
    assert not any(event.get("event") == "completion" for event in f["events"])
    assert json.loads(f["journal"].read_text(encoding="utf-8"))["runtime_layout"] == "managed_roles"
    assert not any("reset" in call or "clean" in call or "--force" in call for call in f["calls"])
    f["desktop"].assert_not_called()
    f["preserved"]()


def test_same_head_managed_update_repairs_roles_instead_of_skipping_partial_install(managed):
    f = managed
    first = f["update"]()
    second = f["update"]()
    assert first["status"] == second["status"] == "success"
    assert first["up_to_date"] is second["up_to_date"] is True
    assert first["source_changed"] is second["source_changed"] is False
    assert f["coordinator"].call_count == 2
    assert not any("checkout" in call for call in f["calls"])
    f["preserved"]()


@pytest.mark.parametrize("cancelled", [False, True])
def test_managed_role_failure_or_cancellation_rolls_back_source_and_keeps_roles_for_retry(managed, cancelled):
    f = managed
    latest = f["newer"]()
    cancel = threading.Event()
    def fail(root, **kwargs):
        role = f["app"] / ".runtime/envs/deposition-directml"
        role.mkdir(parents=True)
        (role / "partial.bin").write_bytes(b"keep-for-retry")
        kwargs["output"]("安装失败：角色依赖解析失败")
        if cancelled:
            cancel.set()
        return 130 if cancelled else 2
    f["coordinator"].side_effect = fail
    result = f["update"](cancel_event=cancel)
    assert result["status"] == ("cancelled" if cancelled else "failed")
    assert result["source_rolled_back"] is True
    assert f["command"](f["app"], "rev-parse", "HEAD") == f["before"]
    assert f["command"](f["app"], "rev-parse", "--abbrev-ref", "HEAD") == "main"
    receipt = json.loads(f["journal"].read_text(encoding="utf-8"))
    assert receipt["state"] == "failed" and receipt["runtime_layout"] == "managed_roles"
    assert (f["app"] / ".runtime/envs/deposition-directml/partial.bin").read_bytes() == b"keep-for-retry"
    f["coordinator"].side_effect = f["install"]
    retry = f["update"]()
    assert retry["status"] == "success" and retry["current_commit"] == latest
    f["preserved"]()


def test_same_head_failed_repair_journal_remains_managed_when_layout_marker_is_missing(managed):
    f = managed
    f["coordinator"].side_effect = lambda *a, **k: 2
    failed = f["update"]()
    assert failed["status"] == "failed" and "source_rolled_back" not in failed
    receipt = json.loads(f["journal"].read_text(encoding="utf-8"))
    assert receipt["state"] == "failed" and receipt["runtime_layout"] == "managed_roles"
    # Simulate a subsequently lost partial marker; updater itself deletes nothing.
    shutil.rmtree(f["app"] / ".runtime")
    f["coordinator"].side_effect = f["install"]
    assert f["update"]()["status"] == "success"
    assert f["coordinator"].call_count == 2


@pytest.mark.parametrize("marker", [".runtime", ".runtime/envs", ".runtime/compute-runtimes.json"])
def test_partial_or_unknown_managed_markers_never_fall_back_to_legacy(managed, marker):
    f = managed
    shutil.rmtree(f["app"] / ".runtime")
    path = f["app"] / marker
    path.parent.mkdir(parents=True, exist_ok=True)
    if marker.endswith(".json"):
        path.write_text('{"unknown": "保留"}', encoding="utf-8")
    else:
        path.mkdir()
    f["coordinator"].side_effect = lambda *a, **k: 2
    assert f["update"]()["status"] == "failed"
    f["coordinator"].assert_called_once()
    assert path.exists()


def test_missing_private_base_with_managed_venv_config_never_uses_legacy_probe(managed):
    f = managed
    shutil.rmtree(f["app"] / ".runtime")
    (f["app"] / ".venv/pyvenv.cfg").write_text(
        f"home = {f['app'] / '.runtime/python'}\ninclude-system-site-packages = false\n", encoding="utf-8")
    f["coordinator"].side_effect = lambda *a, **k: 2
    assert f["update"]()["status"] == "failed"
    f["coordinator"].assert_called_once()


@pytest.mark.parametrize("code", [0, 2, 130])
def test_managed_success_needs_coordinator_completion_and_success_exit(managed, code):
    f = managed
    f["newer"]()
    def incomplete(root, **kwargs):
        if code != 0:
            kwargs["event_fn"](dict(f["completion"]))
        return code
    f["coordinator"].side_effect = incomplete
    result = f["update"]()
    assert result["status"] == ("cancelled" if code == 130 else "failed")
    assert result["source_rolled_back"] is True
    assert "runtime_roles" not in result and "manifest" not in result
    f["preserved"]()


def test_cancellation_from_live_emitter_is_not_raised_into_coordinator(managed):
    f = managed
    f["newer"]()
    cancel = threading.Event()
    def cancelling(root, **kwargs):
        cancel.set()
        kwargs["event_fn"]({"event": "progress", "text": "runner reaps child before returning"})
        return 130
    f["coordinator"].side_effect = cancelling
    result = f["update"](cancel_event=cancel)
    assert result["status"] == "cancelled" and result["source_rolled_back"] is True
    assert any(event.get("text") == "runner reaps child before returning" for event in f["events"])


def test_coordinator_shortcut_failure_is_noncritical_and_never_repeated(managed):
    f = managed
    latest = f["newer"]()
    f["completion"]["shortcut"] = {"status": "failed", "errors": ["桌面权限不足"]}
    result = f["update"]()
    assert result["status"] == "success" and result["current_commit"] == latest
    assert "source_rolled_back" not in result
    assert any("桌面权限不足" in warning for warning in result["warnings"])
    f["desktop"].assert_not_called()
    f["preserved"]()


@pytest.mark.parametrize("guard", ["dirty", "collision", "origin", "diverged", "recovery", "index"])
def test_source_safety_guards_still_precede_managed_coordinator(managed, guard):
    f = managed
    f["newer"](extra="config/settings.json" if guard == "collision" else None)
    expected = f["before"]
    if guard == "dirty":
        (f["app"] / "source.py").write_text("user changes\n", encoding="utf-8")
    elif guard == "origin":
        f["command"](f["app"], "remote", "set-url", "origin", "https://example.invalid/other.git")
    elif guard == "diverged":
        (f["app"] / "local.py").write_text("# local source\n", encoding="utf-8")
        f["command"](f["app"], "add", "local.py")
        f["command"](f["app"], "commit", "--quiet", "-m", "user commit")
        expected = f["command"](f["app"], "rev-parse", "HEAD")
    elif guard == "recovery":
        save_record(f["journal"], {"state": "recovery_required"}, root=f["app"], schema=updater.SCHEMA)
    elif guard == "index":
        f["command"](f["app"], "update-index", "--assume-unchanged", "source.py")
    result = f["update"]()
    assert result["status"] == "failed"
    f["coordinator"].assert_not_called()
    assert f["command"](f["app"], "rev-parse", "HEAD") == expected
    f["preserved"]()


def test_prior_applying_at_new_head_reruns_managed_coordinator(managed):
    f = managed
    save_record(f["journal"], {"state": "applying", "old_commit": "a" * 40,
        "new_commit": f["before"], "runtime_layout": "managed_roles"}, root=f["app"], schema=updater.SCHEMA)
    result = f["update"]()
    assert result["status"] == "success" and result["up_to_date"] is True
    f["coordinator"].assert_called_once()
    f["preserved"]()


@pytest.mark.parametrize("linked", [".runtime", ".venv/pyvenv.cfg"])
def test_linked_managed_marker_or_config_never_enters_legacy_probe(managed, linked):
    f = managed
    shutil.rmtree(f["app"] / ".runtime")
    outside = f["container"] / "保留 outside"
    if linked == ".runtime":
        outside.mkdir()
        (outside / "keep.bin").write_bytes(b"do-not-read-or-change")
    else:
        outside.write_text("untrusted external config\n", encoding="utf-8")
        # An existing legacy sibling base must not override a linked config.
        (f["container"] / ".runtime/python").mkdir(parents=True)
    try:
        (f["app"] / linked).symlink_to(outside, target_is_directory=linked == ".runtime")
    except OSError:
        pytest.skip("symlink creation unavailable")
    f["coordinator"].side_effect = lambda *a, **k: 2
    assert f["update"]()["status"] == "failed"
    f["coordinator"].assert_called_once()
    assert (f["app"] / linked).is_symlink()
    assert outside.exists()


def test_unknown_venv_missing_config_and_private_base_has_no_legacy_probe(managed):
    f = managed
    shutil.rmtree(f["app"] / ".runtime")
    f["coordinator"].side_effect = lambda *a, **k: 2
    assert f["update"]()["status"] == "failed"
    f["coordinator"].assert_called_once()
    assert (f["app"] / ".venv/keep.bin").read_bytes() == b"private-app"


@pytest.mark.parametrize("during_fetch", [False, True])
def test_runtime_role_writer_lock_blocks_before_source_checkout_and_is_not_stolen(managed, during_fetch):
    f = managed
    f["newer"]()
    lock = f["app"] / ".runtime/compute-runtime-install.lock"
    if during_fetch:
        original = updater.deploy._run_git
        def arriving_lock(executable, arguments, **kwargs):
            value = original(executable, arguments, **kwargs)
            if "fetch" in arguments:
                lock.write_bytes(b"active-role-writer")
            return value
        updater.deploy._run_git = arriving_lock
        try:
            result = f["update"]()
        finally:
            updater.deploy._run_git = original
    else:
        lock.write_bytes(b"active-role-writer")
        result = f["update"]()
    assert result["status"] == "failed"
    assert lock.read_bytes() == b"active-role-writer"
    assert f["command"](f["app"], "rev-parse", "HEAD") == f["before"]
    assert not any("checkout" in call for call in f["calls"])
    f["coordinator"].assert_not_called()
    f["preserved"]()


def test_source_only_partial_install_repairs_through_managed_coordinator(managed):
    f = managed
    shutil.rmtree(f["app"] / ".runtime")
    shutil.rmtree(f["app"] / ".venv")
    result = f["update"]()
    assert result["status"] == "success" and result["up_to_date"] is True
    f["coordinator"].assert_called_once()


def test_managed_readonly_version_check_never_enters_coordinator_or_writes_files(managed):
    from tools.dependency_manager import updates
    f = managed
    latest = f["newer"]()
    before_files = {path.relative_to(f["app"]): path.read_bytes()
                    for path in f["app"].rglob("*") if path.is_file()}
    def fetch(path, *args):
        if "/commits/" in path:
            return {"sha": latest}
        assert path.endswith("ref=" + latest)
        raw = b"dev.20261024.2"
        return {"encoding": "base64", "size": len(raw), "content": base64.b64encode(raw).decode("ascii")}
    result = updates.check_project_updates(f["container"], "github", proxy=ProxyConfig("none"),
        fetch=fetch, git_path=f["git"])
    assert result["status"] == "update_available" and result["latest_commit"] == latest
    assert result["local_commit"] == f["before"]
    f["coordinator"].assert_not_called()
    assert not any("fetch" in call or "checkout" in call for call in f["calls"])
    statuses = [call for call in f["calls"] if "status" in call]
    assert statuses and all("--untracked-files=no" in call for call in statuses)
    assert "tracked parent worktree" in result["dirty_scope"]
    assert {path.relative_to(f["app"]): path.read_bytes()
            for path in f["app"].rglob("*") if path.is_file()} == before_files


@pytest.mark.parametrize("receipt_kind", ["source", "partial", "broken_link"])
def test_source_receipt_keeps_partial_new_install_managed_despite_legacy_sibling_base(managed, receipt_kind):
    f = managed
    shutil.rmtree(f["app"] / ".runtime")
    shutil.rmtree(f["app"] / ".venv")
    sibling = f["container"] / ".runtime/python"
    sibling.mkdir(parents=True)
    (sibling / "keep.bin").write_bytes(b"preexisting-prepare-python-base")
    receipt = f["app"] / ".git" / updater.deploy._STATE / "source.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    if receipt_kind == "broken_link":
        try:
            receipt.symlink_to(f["container"] / "absent source receipt")
        except OSError:
            pytest.skip("symlink creation unavailable")
        before_receipt = receipt.readlink()
    else:
        receipt.write_text('{"source": "部分准备"}' if receipt_kind == "source" else '{"partial": ',
            encoding="utf-8")
        before_receipt = receipt.read_bytes()
    result = f["update"]()
    assert result["status"] == "success" and result["up_to_date"] is True
    assert result["runtime_layout"] == "managed_roles"
    f["coordinator"].assert_called_once()
    assert (sibling / "keep.bin").read_bytes() == b"preexisting-prepare-python-base"
    assert (receipt.readlink() if receipt_kind == "broken_link" else receipt.read_bytes()) == before_receipt


def test_long_role_transaction_gets_fresh_bounded_final_git_verification(managed, monkeypatch):
    f = managed
    latest = f["newer"]()
    real_monotonic = updater.time.monotonic
    elapsed = [0.0]
    monkeypatch.setattr(updater.time, "monotonic", lambda: real_monotonic() + elapsed[0])
    transport = updater.deploy._run_git
    final_budgets = []
    def checked_transport(executable, arguments, **kwargs):
        if elapsed[0]:
            final_budgets.append(kwargs["deadline"] - updater.time.monotonic())
        return transport(executable, arguments, **kwargs)
    monkeypatch.setattr(updater.deploy, "_run_git", checked_transport)
    def long_install(root, **kwargs):
        code = f["install"](root, **kwargs)
        elapsed[0] = 601.0  # No sleep: only role work advances beyond the Git fetch budget.
        return code
    f["coordinator"].side_effect = long_install
    result = f["update"]()
    assert result["status"] == "success", result
    assert result["current_commit"] == latest and "source_rolled_back" not in result
    assert f["command"](f["app"], "rev-parse", "HEAD") == latest
    assert final_budgets and all(0 < budget <= 30 for budget in final_budgets)
    f["preserved"]()


@pytest.mark.parametrize("config_kind", ["missing", "external", "sibling", "malformed", "oversized", "system-sites"])
def test_unmarked_existing_venv_is_rejected_without_any_interpreter_probe(managed, monkeypatch, config_kind):
    f = managed
    f["newer"]()
    shutil.rmtree(f["app"] / ".runtime")
    sibling = f["container"] / ".runtime/python"
    sibling.mkdir(parents=True)
    (sibling / "keep.bin").write_bytes(b"preexisting-sibling-base-preserved")
    config = f["app"] / ".venv/pyvenv.cfg"
    base = sibling if config_kind == "sibling" else f["container"] / "foreign Python"
    if config_kind != "missing":
        contents = (f"home = {base}\nexecutable = {base / 'python.exe'}\n"
                    "include-system-site-packages = false\n")
        if config_kind == "malformed":
            contents = "not a venv config\n"
        elif config_kind == "oversized":
            contents = "x" * 65537
        elif config_kind == "system-sites":
            contents = contents.replace("false", "true")
        config.write_text(contents, encoding="utf-8")
    interpreter = f["app"] / ".venv/Scripts/python.exe"
    interpreter.parent.mkdir()
    interpreter.write_bytes(b"inert-unknown-interpreter-never-executed")
    protected = {path: path.read_bytes() for directory in (f["app"] / ".venv", sibling)
                 for path in directory.rglob("*") if path.is_file()}
    f["coordinator"].side_effect = f["real_coordinator"]
    monkeypatch.setattr(updater.default_install.sys, "platform", "win32")
    monkeypatch.setattr(updater.default_install, "discover_git", lambda **kwargs: f["git"])
    runtime_plan = MagicMock(side_effect=lambda *a, **k: pytest.fail("unverified layout reached Python planning"))
    monkeypatch.setattr(updater.default_install, "plan_runtime", runtime_plan)
    probe = MagicMock(side_effect=lambda *a, **k: pytest.fail("unverified interpreter executed"))
    monkeypatch.setattr(legacy_manager, "probe_python", probe)
    result = f["update"]()
    assert result["status"] == "failed" and "新安装目录" in str(result["errors"]), result
    assert result["source_rolled_back"] is True
    assert f["command"](f["app"], "rev-parse", "HEAD") == f["before"]
    assert all(path.read_bytes() == content for path, content in protected.items())
    assert (f["app"] / "config/settings.json").read_bytes() == "用户配置".encode("utf-8")
    assert (f["app"] / "data/result.bin").read_bytes() == b"user-result"
    assert not config.exists() if config_kind == "missing" else config.read_bytes() == protected[config]
    f["coordinator"].assert_called_once()
    runtime_plan.assert_not_called()
    probe.assert_not_called()
