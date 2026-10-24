"""Offline role/layout and transaction tests, never real package/driver installs."""
import hashlib
import json
import threading
from pathlib import Path

import pytest

from tools.dependency_manager import cli, compute_recommendation, manager
from tools.dependency_manager import runtime_roles as roles
from tools.dependency_manager.runtime import runtime_manifest


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "光学仿真 项目"
    root.mkdir()
    (root / "coat_sim").mkdir()
    (root / "coat_sim/compute_runtime_manifest.py").write_text("# contract\n", encoding="utf-8")
    (root / "launcher.py").write_text("# entry\n", encoding="utf-8")
    (root / "requirements.txt").write_text(
        "numpy>=1.24\nscipy>=1.16,<2\nmatplotlib>=3.7\nPillow>=10\npydantic>=2.12,<3\n"
        "PySide6-Essentials>=6.8,<7\nkeyring>=25,<26\nmcp>=2,<3\n", encoding="utf-8")
    (root / "requirements-test.txt").write_text("pytest>=9.1,<10\nmpmath>=1.3,<2\nmitsuba==3.9.0\ndrjit==1.4.0\n", encoding="utf-8")
    (root / ".runtime/python").mkdir(parents=True)
    (root / ".runtime/python/python.exe").write_bytes(b"private-python")
    monkeypatch.setattr(roles, "inspect_owned", lambda *a, **k: {"files": {
        "python.exe": {"sha256": hashlib.sha256(b"private-python").hexdigest()},
        "Lib/venv/scripts/nt/python.exe": {"sha256": hashlib.sha256(b"python-launcher").hexdigest()}}})
    monkeypatch.setattr(manager.sys, "platform", "win32")
    def private_base(path):
        return {"platform": "win32", "machine": "AMD64", "implementation": "CPython",
                "python": "3.12.10"}, runtime_manifest()
    monkeypatch.setattr(roles, "_private_base", private_base)
    monkeypatch.setattr(compute_recommendation, "_visible_gpus", lambda *a: ([], []))
    return root


def test_role_layout_keeps_directml_with_cuda_and_aliases_only_cpu(project):
    roles_plan = roles.plan_runtime_roles(project, cuda_backend="cpu")
    assert roles_plan["status"] == "ready", roles_plan["errors"]
    assert roles_plan["roles"]["deposition_cpu"] == "app"
    envs = {row["id"]: row for row in roles_plan["environments"]}
    assert set(envs) == {"app", "appearance", "deposition_directml"}
    assert not any("torch" in req for req in envs["app"]["requirements"])
    assert "torch-directml==0.2.5.dev240914" in envs["deposition_directml"]["requirements"]
    assert "mitsuba==3.9.0" in envs["appearance"]["requirements"]
    assert "drjit==1.4.0" in envs["appearance"]["requirements"]
    assert not any(req.startswith("pytest") for req in envs["appearance"]["requirements"])
    layout = roles.role_layout("cuda13")
    assert "deposition_directml" in layout and "deposition_cuda" in layout
    assert len({r[0] for r in layout.values()}) == 4


def test_auto_cuda_is_hardware_adapted_without_removing_directml(project, monkeypatch):
    monkeypatch.setattr(compute_recommendation, "_visible_gpus", lambda *a: ([{
        "source": "nvidia_smi", "index": "0", "compute_capability": "8.9", "driver": "580.88"}], []))
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("CUDA_DEVICE_ORDER", raising=False)
    plan = roles.plan_runtime_roles(project)
    assert plan["status"] == "ready", plan["errors"]
    assert plan["selected_cuda"] == "cuda13"
    envs = {row["id"]: row for row in plan["environments"]}
    assert "deposition_directml" in envs
    assert "cupy-cuda13x[ctk]==14.2.0" in envs["deposition_cuda"]["requirements"]
    assert "cuda-toolkit==13.0.0" in envs["deposition_cuda"]["requirements"]
    assert not any("torch" in req for req in envs["deposition_cuda"]["requirements"])


def test_explicit_unproved_cuda_is_blocked(project):
    assert roles.plan_runtime_roles(project, cuda_backend="cuda13")["status"] == "blocked"


def test_unsupported_platform_and_missing_private_runtime_block(project, monkeypatch):
    monkeypatch.setattr(manager.sys, "platform", "linux")
    assert roles.plan_runtime_roles(project)["status"] == "blocked"
    monkeypatch.setattr(manager.sys, "platform", "win32")
    monkeypatch.setattr(roles, "_private_base", lambda p: (_ for _ in ()).throw(ValueError("not owned")))
    assert "not owned" in roles.plan_runtime_roles(project)["errors"]


def test_planning_never_creates_env_dirs_or_installs(project, monkeypatch):
    monkeypatch.setattr(manager, "execute_plan", lambda *a, **k: pytest.fail("must not execute"))
    before = set(project.rglob("*"))
    assert roles.plan_runtime_roles(project)["status"] == "ready"
    assert set(project.rglob("*")) == before


def test_unknown_manifest_and_linked_env_parent_are_preserved(project):
    marker = project / roles.MANIFEST_RELATIVE
    marker.write_text('{"unrelated":true}', encoding="utf-8")
    assert roles.plan_runtime_roles(project)["status"] == "blocked"
    assert json.loads(marker.read_text(encoding="utf-8")) == {"unrelated": True}
    marker.unlink()
    external = project.parent / "external"
    external.mkdir()
    (project / ".runtime/envs").symlink_to(external, target_is_directory=True)
    assert roles.plan_runtime_roles(project)["status"] == "blocked"


def _fake_installs(root, monkeypatch, *, fail=None, mutate_source=False):
    def inspect(path, venv, groups, backend, profile):
        _, rows = manager.catalog(path, groups, backend, profile)
        return {"errors": [], "packages": [dict(row, status="satisfied") for row in rows],
                "target": {"valid": True, "base_prefix": str(root / ".runtime/python"),
                           "platform": "win32", "python": "3.12.10"}}
    monkeypatch.setattr(manager, "inspect_environment", inspect)
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: {"inventory": {"numpy": "2.2.6", "pip": "25.0"}})
    def plan(path, venv, groups, backend, mirror, action, python, proxy, profile):
        _, rows = manager.catalog(path, groups, backend, profile)
        return {"status": "ready", "packages": rows, "errors": [], "venv": str(venv), "backend": backend}
    monkeypatch.setattr(manager, "plan_environment", plan)
    calls = []
    def execute(plan, *args):
        calls.append(plan["backend"])
        if fail == len(calls):
            return {"status": "failed", "errors": ["fixture failure"]}
        target = Path(plan["venv"])
        (target / "Scripts").mkdir(parents=True)
        (target / "Scripts/python.exe").write_bytes(b"python-launcher")
        (target / "pyvenv.cfg").write_text(f"home = {root / '.runtime/python'}\ninclude-system-site-packages = false\n"
            f"version = 3.12.10\nexecutable = {root / '.runtime/python/python.exe'}\n", encoding="utf-8")
        if mutate_source and len(calls) == 1:
            (root / "requirements.txt").write_text("numpy>=2\n", encoding="utf-8")
        return {"status": "success", "errors": []}
    monkeypatch.setattr(manager, "execute_plan", execute)
    return calls


def test_confirmed_execution_runs_separate_envs_and_publishes_once(project, monkeypatch):
    plan = roles.plan_runtime_roles(project, cuda_backend="cpu")
    calls = _fake_installs(project, monkeypatch)
    result = roles.execute_runtime_roles(plan, True)
    assert result["status"] == "success", result["errors"]
    assert calls == ["cpu", "directml", "cpu"]
    data = json.loads((project / roles.MANIFEST_RELATIVE).read_text(encoding="utf-8"))
    assert project.name == "光学仿真 项目"
    config = (project / ".venv/pyvenv.cfg").read_text(encoding="utf-8")
    assert f"home = {project / '.runtime/python'}\n" in config
    assert f"executable = {project / '.runtime/python/python.exe'}\n" in config
    assert data["schema_version"] == 1
    assert data["roles"]["deposition_cpu"] == "app"
    assert len(data["environments"]) == 3
    assert data["environments"]["appearance"]["root"] == ".runtime/envs/appearance"
    assert data["environments"]["app"]["packages_sha256"] == roles.package_inventory_digest({"numpy": "2.2.6", "pip": "25.0"})
    assert not (project / ".runtime/compute-runtime-install.lock").exists()


@pytest.mark.parametrize("fail", [1, 2, 3])
def test_component_failure_preserves_other_envs_without_publishing(project, monkeypatch, fail):
    plan = roles.plan_runtime_roles(project, cuda_backend="cpu")
    calls = _fake_installs(project, monkeypatch, fail=fail)
    result = roles.execute_runtime_roles(plan, True)
    assert result["status"] == "failed"
    assert len(calls) == fail
    assert not (project / roles.MANIFEST_RELATIVE).exists()
    if fail > 1:
        assert (project / ".venv/Scripts/python.exe").is_file()


def test_missing_approval_stale_plan_and_cancel_do_not_install(project, monkeypatch):
    plan = roles.plan_runtime_roles(project)
    calls = _fake_installs(project, monkeypatch)
    assert roles.execute_runtime_roles(plan)["status"] == "blocked"
    cancelled = threading.Event()
    cancelled.set()
    assert roles.execute_runtime_roles(plan, True, cancel_event=cancelled)["status"] == "cancelled"
    (project / "requirements.txt").write_text("numpy>=2\n", encoding="utf-8")
    assert roles.execute_runtime_roles(plan, True)["status"] == "failed"
    assert calls == []


def test_source_mutation_midway_blocks_remaining_transactions(project, monkeypatch):
    plan = roles.plan_runtime_roles(project)
    calls = _fake_installs(project, monkeypatch, mutate_source=True)
    assert roles.execute_runtime_roles(plan, True)["status"] == "failed"
    assert calls == ["cpu"]
    assert not (project / roles.MANIFEST_RELATIVE).exists()


def test_competing_operation_lock_is_not_removed(project, monkeypatch):
    plan = roles.plan_runtime_roles(project)
    _fake_installs(project, monkeypatch)
    lock = project / ".runtime/compute-runtime-install.lock"
    lock.write_text("someone else", encoding="utf-8")
    assert roles.execute_runtime_roles(plan, True)["status"] == "failed"
    assert lock.read_text(encoding="utf-8") == "someone else"


def test_manifest_race_preserves_new_unknown_contents(project):
    marker = project / roles.MANIFEST_RELATIVE
    marker.write_text('{"unrelated":"preserve"}', encoding="utf-8")
    with pytest.raises(ValueError):
        roles._publish_manifest(project, {}, None)
    assert json.loads(marker.read_text(encoding="utf-8")) == {"unrelated": "preserve"}


@pytest.mark.parametrize("corruption", ["executable", "external_config", "system_site"])
def test_existing_role_is_authenticated_before_target_probe(project, monkeypatch, corruption):
    target = project / ".venv"
    (target / "Scripts").mkdir(parents=True)
    (target / "Scripts/python.exe").write_bytes(b"python-launcher" if corruption != "executable" else b"arbitrary exe")
    (target / "pyvenv.cfg").write_text(f"home = {project / '.runtime/python' if corruption != 'external_config' else '/external'}\n"
        f"include-system-site-packages = {'true' if corruption == 'system_site' else 'false'}\n"
        f"version = 3.12.10\nexecutable = {project / '.runtime/python/python.exe'}\n", encoding="utf-8")
    monkeypatch.setattr(manager, "inspect_environment", lambda *a, **k: pytest.fail("Untrusted role was probed"))
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: pytest.fail("Untrusted role was executed"))
    plan = roles.plan_runtime_roles(project)
    assert plan["status"] == "blocked"
    assert any("preserved without execution" in error for error in plan["errors"])


@pytest.mark.parametrize("backend,packages", [
    ("directml", {"cupy-cuda12x": "14.2.0"}),
    ("directml", {"cupy-cuda99x": "99.0"}),
    ("cuda12", {"torch-directml": "0.2.5.dev240914"}),
    ("cuda13", {"cupy-cuda12x": "14.2.0"}),
])
def test_producer_gpu_conflicts_match_strict_consumer(backend, packages):
    with pytest.raises(ValueError, match="Conflicting GPU"):
        roles._check_gpu_roots(packages, backend)


def _install_existing_manifest(project, monkeypatch):
    plan = roles.plan_runtime_roles(project)
    _fake_installs(project, monkeypatch)
    result = roles.execute_runtime_roles(plan, True)
    assert result["status"] == "success", result["errors"]
    return project / roles.MANIFEST_RELATIVE


def test_late_unknown_writer_before_backup_is_restored_not_overwritten(project, monkeypatch):
    marker = _install_existing_manifest(project, monkeypatch)
    previous = hashlib.sha256(marker.read_bytes()).hexdigest()
    original_rename = Path.rename
    def race(path, destination):
        if path == marker:
            path.write_text('{"unrelated":"late writer"}', encoding="utf-8")
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", race)
    with pytest.raises(ValueError, match="changed during publication"):
        roles._publish_manifest(project, {"new": "data"}, previous)
    assert json.loads(marker.read_text(encoding="utf-8")) == {"unrelated": "late writer"}


def test_late_unknown_writer_after_backup_preserves_both_copies(project, monkeypatch):
    marker = _install_existing_manifest(project, monkeypatch)
    old = marker.read_bytes()
    previous = hashlib.sha256(old).hexdigest()
    publish = roles._publish_exclusive
    def race(source, destination):
        if str(source.name).startswith(".compute-runtimes-"):
            destination.write_text('{"unrelated":"new writer"}', encoding="utf-8")
        return publish(source, destination)
    monkeypatch.setattr(roles, "_publish_exclusive", race)
    with pytest.raises(ValueError, match="recovery copy retained"):
        roles._publish_manifest(project, {"new": "data"}, previous)
    assert json.loads(marker.read_text(encoding="utf-8")) == {"unrelated": "new writer"}
    backups = list((project / ".runtime").glob(".compute-runtimes-recovery-*/compute-runtimes.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == old


def test_inventory_digest_contract():
    assert roles.package_inventory_digest({"z": "1", "a": "2"}) == hashlib.sha256(b"a\x002\nz\x001\n").hexdigest()


def test_cli_role_options_and_external_overrides_are_rejected(project, capsys):
    args = cli.parser().parse_args(["runtime-roles-plan", "--project", str(project)])
    assert args.runtime_cuda == "auto"
    assert cli.main(["runtime-roles-plan", "--project", str(project), "--python", "external", "--json"]) == 2
    assert "overrides are forbidden" in capsys.readouterr().out
