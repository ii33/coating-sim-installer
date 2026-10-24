"""Optional cross-repository producer/consumer contracts, with inert files only.

Set COAT_RUNTIME_CONSUMER_ROOT to a current coating-sim checkout. Nothing is
installed or executed from the fake Windows runtimes. The consumer fixture's
independent base pins are test-local, not a configurable production trust input.
"""
import importlib.util
import os
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.dependency_manager import compute_recommendation, manager
from tools.dependency_manager import runtime_roles as roles

CONSUMER_ROOT = os.environ.get("COAT_RUNTIME_CONSUMER_ROOT")
pytestmark = pytest.mark.skipif(not CONSUMER_ROOT, reason="Explicit current main-repository checkout required")


@pytest.fixture
def contract(tmp_path):
    project = tmp_path / "光学仿真 合同 项目"
    project.mkdir()
    source = Path(CONSUMER_ROOT).resolve(strict=True)
    fixture_path = source / "tests/test_compute_runtime_manifest.py"
    sys.path.insert(0, str(source))
    try:
        spec = importlib.util.spec_from_file_location("_coat_runtime_contract_fixture", fixture_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fixture = module.RuntimeFixture(project)
        try:
            for relative in ("requirements.txt", "requirements-test.txt", "launcher.py", "coat_sim/compute_runtime_manifest.py"):
                destination = project / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, destination)
            pin = deepcopy(roles.runtime_manifest())
            pin["artifacts"]["x86_64"]["inventory_sha256"] = fixture.data["base"]["inventory_sha256"]

            def probe(python, rows=(), **kwargs):
                python = Path(python)
                if python == fixture.base / "python.exe":
                    directory, packages = fixture.base, {}
                else:
                    directory = python.parent.parent
                    record = next(record for record in fixture.data["environments"].values()
                                  if project / record["root"] == directory)
                    packages = dict(record["packages"])
                return {"executable": str(python), "prefix": str(directory), "base_prefix": str(fixture.base),
                        "python": "3.12.10", "platform": "win32", "machine": "AMD64",
                        "implementation": "CPython", "inventory": packages,
                        "checks": {row["name"]: True for row in rows}}

            def inspect(root, venv, groups, backend, profile):
                _, rows = manager.catalog(root, groups, backend, profile)
                return {"errors": [], "packages": [dict(row, status="satisfied") for row in rows],
                        "target": dict(probe(Path(venv) / "Scripts/python.exe"), valid=True)}

            def plan(root, venv, groups, backend, mirror, action, python, proxy, profile):
                _, rows = manager.catalog(root, groups, backend, profile)
                return {"status": "ready", "errors": [], "packages": rows,
                        "venv": str(venv), "backend": backend}

            devices = [{"source": "nvidia_smi", "index": "0", "compute_capability": "6.1", "driver": "580.88"}]
            with patch.object(roles, "runtime_manifest", return_value=pin), \
                    patch.object(manager.sys, "platform", "win32"), \
                    patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "", "CUDA_DEVICE_ORDER": ""}), \
                    patch.object(manager, "probe_python", side_effect=probe), \
                    patch.object(manager, "inspect_environment", side_effect=inspect), \
                    patch.object(manager, "plan_environment", side_effect=plan), \
                    patch.object(compute_recommendation, "_visible_gpus", return_value=(devices, [])):
                yield fixture, module.runtime
        finally:
            fixture.close()
    finally:
        sys.path.remove(str(source))


def test_published_writer_output_is_accepted_by_strict_consumer(contract):
    fixture, runtime = contract
    plan = roles.plan_runtime_roles(fixture.root)
    assert plan["status"] == "ready", plan["errors"]
    assert plan["selected_cuda"] == "cuda12"
    with patch.object(manager, "execute_plan", return_value={"status": "success", "errors": []}):
        result = roles.execute_runtime_roles(plan, True)
    assert result["status"] == "success", result["errors"]
    bound = runtime.load_compute_runtime_manifest(fixture.root)
    assert len(bound.environments) == 4
    assert bound.select_python("app") == bound.select_python("deposition_cpu")
    assert bound.select_python("deposition_directml") != bound.select_python("deposition_cuda")
    assert bound.select_python("appearance") != bound.select_python("app")
    assert "ready" not in bound.environments["appearance"].__dict__


def test_app_repair_then_dml_failure_keeps_old_file_but_cannot_use_stale_binding(contract):
    fixture, runtime = contract
    plan = roles.plan_runtime_roles(fixture.root)
    assert plan["status"] == "ready", plan["errors"]
    path = fixture.root / roles.MANIFEST_RELATIVE
    previous = path.read_bytes()
    calls = []
    def repair_then_fail(nested, *args):
        calls.append(nested["backend"])
        if len(calls) == 1:
            fixture.data["environments"]["app"]["packages"]["numpy"] = "2.2.7"
            fixture.write_metadata("app")
            return {"status": "success", "errors": []}
        return {"status": "failed", "errors": ["DirectML fixture failure"]}
    with patch.object(manager, "execute_plan", side_effect=repair_then_fail):
        result = roles.execute_runtime_roles(plan, True)
    assert result["status"] == "failed"
    assert calls == ["cpu", "directml"]
    assert path.read_bytes() == previous
    assert (fixture.root / ".venv/Scripts/python.exe").is_file()
    with pytest.raises(runtime.RuntimeManifestError):
        runtime.select_runtime_python(fixture.root, "app")
