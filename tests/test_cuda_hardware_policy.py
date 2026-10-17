"""Rule and transaction boundary tests; no network, driver or package installs."""
import subprocess
from pathlib import Path

import pytest

from tools.dependency_manager import compute_recommendation as cr
from tools.dependency_manager import cuda_policy as policy
from tools.dependency_manager import manager, python_discovery, runtime


def identity(**changes):
    result = dict(platform="win32", python="3.12.10", machine="AMD64",
                  implementation="CPython", executable="test-python")
    result.update(changes)
    return result


def gpu(cap="8.9", driver="580.88", index="0"):
    return dict(source="nvidia_smi", name="NVIDIA", vendor="nvidia", index=index,
                compute_capability=cap, driver=driver)


@pytest.fixture(autouse=True)
def no_device_remapping(monkeypatch):
    for key in ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("cap,driver,expected", [
    ("5.2", "580.88", ["cuda12"]), ("6.1", "580.88", ["cuda12"]),
    ("7.0", "580.88", ["cuda12"]), ("7.5", "580.88", ["cuda13", "cuda12"]),
    ("8.9", "537.42", ["cuda12"]), ("8.9", "580.88", ["cuda13", "cuda12"]),
    ("12.0", "580.88", ["cuda13"]), ("12.0", "575.0", []),
    ("3.5", "580.88", []), ("13.0", "580.88", []), ("N/A", "580.88", []),
    ("8.9", "N/A", []), ("8.9", "527.41", []),
])
def test_architecture_driver_matrix(cap, driver, expected):
    assert policy.candidates([gpu(cap, driver)], identity())[0] == expected


@pytest.mark.parametrize("target", [identity(platform="darwin"), identity(machine="ARM64"),
                                     identity(python="3.14.0"), identity(implementation="PyPy")])
def test_unknown_platform_python_and_wheel_architecture_keep_cpu(target):
    assert policy.candidates([gpu()], target)[0] == []


def test_default_gpu_identity_and_remapping(monkeypatch):
    assert policy.candidates([gpu("6.1", index="0"), gpu(index="1")], identity())[0] == ["cuda12"]
    assert policy.candidates([gpu(index="1")], identity())[0] == []
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert policy.candidates([gpu()], identity())[0] == []


def test_cim_name_and_smi_max_cuda_string_are_not_capability_evidence():
    assert policy.candidates([{"source": "windows_cim", "name": "RTX 5090", "cuda_version": "13.0"}], identity())[0] == []


def test_live_wheel_tags_yanked_and_runtime_are_required(monkeypatch):
    wheel = dict(filename="cupy_cuda13x-14.2.0-cp312-cp312-win_amd64.whl",
                 packagetype="bdist_wheel", yanked=False)
    runtime_wheel = dict(filename="cuda_toolkit-13.0.0-py2.py3-none-any.whl", yanked=False)
    monkeypatch.setattr(policy, "_metadata", lambda name, *a: {"urls": [wheel if name.startswith("cupy") else runtime_wheel]})
    assert policy.wheel_evidence("cuda13", identity())["available"]
    assert not policy.wheel_evidence("cuda13", identity(python="3.11.9"))["available"]
    wheel["yanked"] = True
    assert not policy.wheel_evidence("cuda13", identity())["available"]


def test_registry_failure_never_claims_available(monkeypatch):
    monkeypatch.setattr(policy, "_metadata", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    assert not policy.wheel_evidence("cuda13", identity())["available"]


def test_malformed_registry_is_unknown(monkeypatch):
    monkeypatch.setattr(policy, "_metadata", lambda *a: ["invalid"])
    assert not policy.wheel_evidence("cuda13", identity())["available"]


def test_wheel_failure_uses_compatible_older_profile_and_cpu(monkeypatch):
    monkeypatch.setattr(cr.sys, "platform", "win32")
    monkeypatch.setattr(cr, "_visible_gpus", lambda *a: ([gpu()], []))
    monkeypatch.setattr(cr, "_runtime_probe", lambda *a: {"available": False})
    monkeypatch.setattr(cr, "wheel_evidence", lambda b, *a: {"available": b == "cuda12", "reason": "fixture"})
    result = cr.recommend_environment(identity())
    assert result["selected_backend"] == "cuda12"
    assert result["backend_usable"]["cuda12"] is False
    monkeypatch.setattr(cr, "wheel_evidence", lambda b, *a: {"available": False, "reason": "offline"})
    assert cr.recommend_environment(identity())["selected_backend"] == "cpu"


def test_cuda_preference_keeps_default_directml_install_separate(monkeypatch):
    monkeypatch.setattr(cr.sys, "platform", "win32")
    monkeypatch.setattr(cr, "_visible_gpus", lambda *a: ([gpu()], []))
    monkeypatch.setattr(cr, "wheel_evidence", lambda *a: {"available": True, "reason": "fixture"})
    monkeypatch.setattr(cr, "_runtime_probe", lambda *a: {"available": True, "cuda_runtime_major": 13})
    result = cr.recommend_environment(identity())
    assert result["selected_backend"] == "cuda13" and result["backend_usable"]["cuda13"]
    root = Path(__file__).resolve().parents[1]
    _, rows = manager.catalog(root, ("desktop", "appearance"), "cuda13")
    requirements = {r["requirement"] for r in rows}
    assert "torch-directml==0.2.5.dev240914" in requirements
    assert "cupy-cuda13x[ctk]==14.2.0" in requirements
    assert "cuda-toolkit==13.0.0" in requirements
    assert not any(r.startswith("torch==") for r in requirements)


def test_linux_default_skips_directml(monkeypatch):
    monkeypatch.setattr(manager.sys, "platform", "linux")
    root = Path(__file__).resolve().parents[1]
    assert "torch-directml" not in {r["name"] for r in manager.catalog(root)[1]}


def test_native_access_violation_is_reported_without_parent_gpu_import(monkeypatch):
    monkeypatch.setattr(cr, "external_run", lambda *a, **k: subprocess.CompletedProcess(a, 0xC0000005, "", ""))
    result = cr._runtime_probe("test-python", "directml")
    assert not result["available"] and "0xC0000005" in result["reason"]
    assert "native access violation" in result["reason"]


def test_timeout_and_invalid_result_are_not_available(monkeypatch):
    monkeypatch.setattr(cr, "external_run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(a, 20)))
    assert "timed out" in cr._runtime_probe("test-python", "cuda13")["reason"]
    monkeypatch.setattr(cr, "external_run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "garbage", ""))
    assert not cr._runtime_probe("test-python", "directml")["available"]
    monkeypatch.setattr(cr, "external_run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '__COAT_SIM_DEVICE_PROBE__=true', ""))
    assert not cr._runtime_probe("test-python", "directml")["available"]


def test_postinstall_failed_cuda_falls_back_without_erasing_directml(monkeypatch):
    calls = []
    def probe(exe, backend):
        calls.append(backend)
        return {"available": backend == "directml", "reason": "fixture"}
    monkeypatch.setattr(cr, "_runtime_probe", probe)
    result = cr.verify_installed_backends("target", "cuda13", True)
    assert calls == ["cuda13", "directml"]
    assert result["recommended_backend"] == "directml"
    assert result["backend_usable"]["cuda13"] is False
    assert result["backend_usable"]["directml"] is True


def test_postinstall_cuda_major_mismatch_and_total_failure_keep_cpu(monkeypatch):
    monkeypatch.setattr(cr, "_runtime_probe", lambda *a: {"available": True, "cuda_runtime_major": 12})
    result = cr.verify_installed_backends("target", "cuda13")
    assert result["recommended_backend"] == "cpu"
    assert "major differs" in result["probe_diagnostics"]["cuda13"]["reason"]
    monkeypatch.setattr(cr, "_runtime_probe", lambda *a: {"available": False})
    assert cr.verify_installed_backends("target", "cuda12", True)["recommended_backend"] == "cpu"


def test_cancellation_inside_last_device_probe_cannot_report_success(monkeypatch):
    import threading
    event = threading.Event()
    def cancel(*args):
        event.set()
        return {"available": True}
    monkeypatch.setattr(cr, "_runtime_probe", cancel)
    assert cr.verify_installed_backends("target", "cpu", True, event)["status"] == "cancelled"


def test_default_windows_existing_313_is_blocked_without_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(manager.sys, "platform", "win32")
    (tmp_path / "requirements.txt").write_text("numpy>=1.24\n")
    target = tmp_path / ".venv"
    (target / "Scripts").mkdir(parents=True)
    (target / "Scripts/python.exe").touch()
    (target / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    observed = dict(identity(python="3.13.16"), prefix=str(target), base_prefix="base", pip="25.0", inventory={})
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: observed)
    plan = manager.plan_environment(tmp_path)
    assert plan["status"] == "blocked"
    assert any("DirectML requires" in error and "new deployment directory" in error for error in plan["errors"])
    assert not any(a["kind"] == "create_venv" for a in plan["actions"])


def test_existing_modern_torch_is_never_silently_downgraded(monkeypatch, tmp_path):
    monkeypatch.setattr(manager.sys, "platform", "win32")
    (tmp_path / "requirements.txt").write_text("numpy\n")
    target = tmp_path / ".venv"
    (target / "Scripts").mkdir(parents=True)
    (target / "Scripts/python.exe").touch()
    (target / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    observed = dict(identity(), prefix=str(target), base_prefix="base", pip="25.0", inventory={"torch": "2.13.0"})
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: observed)
    assert any("separate environment" in error for error in manager.plan_environment(tmp_path)["errors"])


def test_transient_identity_probe_cannot_bypass_default_directml_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(manager.sys, "platform", "win32")
    (tmp_path / "requirements.txt").write_text("numpy\n")
    calls = iter([ValueError("initial identity unavailable"),
                  dict(identity(python="3.13.16"), has_venv=True, has_ensurepip=True)])
    def probe(*args, **kwargs):
        row = next(calls)
        if isinstance(row, Exception):
            raise row
        return row
    monkeypatch.setattr(manager, "probe_python", probe)
    result = manager.plan_environment(tmp_path, python="existing-313")
    assert result["status"] == "blocked"
    assert any("DirectML requires" in error for error in result["errors"])
    assert not any(action["kind"] == "create_venv" for action in result["actions"])


def test_manual_cuda13_on_pascal_is_blocked_before_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(manager.sys, "platform", "win32")
    (tmp_path / "requirements.txt").write_text("numpy\n")
    observed = dict(identity(), prefix="base", base_prefix="base", has_venv=True,
                    has_ensurepip=True, bundled_pip="25.0")
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: observed)
    monkeypatch.setattr(cr, "_visible_gpus", lambda *a: ([gpu("6.1")], []))
    monkeypatch.setattr(manager, "run_command", lambda *a, **k: pytest.fail("blocked profile must not run venv/pip"))
    plan = manager.plan_environment(tmp_path, backend="cuda13", python="base-python")
    assert plan["status"] == "blocked"
    assert any("not a reviewed hardware/driver" in e for e in plan["errors"])
    result = manager.execute_plan(plan, confirmed=True)
    assert result["status"] == "blocked" and not (tmp_path / ".venv").exists()


def test_conflicting_cupy_distribution_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(manager.sys, "platform", "win32")
    (tmp_path / "requirements.txt").write_text("numpy\n")
    target = tmp_path / ".venv"
    (target / "Scripts").mkdir(parents=True)
    (target / "Scripts/python.exe").touch()
    (target / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
    observed = dict(identity(), prefix=str(target), base_prefix="base", pip="25.0",
                    inventory={"cupy-cuda12x": "13.6.0"})
    monkeypatch.setattr(manager, "probe_python", lambda *a, **k: observed)
    monkeypatch.setattr(cr, "_visible_gpus", lambda *a: ([gpu()], []))
    plan = manager.plan_environment(tmp_path, backend="cuda13")
    assert plan["status"] == "blocked" and any("Conflicting CuPy" in e for e in plan["errors"])


def test_runtime_defaults_to_compatible_312_and_registration_guard_remains(monkeypatch, tmp_path):
    assert runtime.runtime_manifest()["version"] == "3.12.10"
    monkeypatch.setattr(runtime, "_registered_python_paths", lambda *a: ["existing-python"])
    plan = runtime.plan_runtime(project=tmp_path, platform_name="win32", machine="AMD64")
    assert plan["status"] == "blocked" and any("already registered" in e for e in plan["errors"])


def test_discovery_prefers_312_and_rejects_313_for_default_directml(monkeypatch, tmp_path):
    paths = [str(tmp_path / "311.exe"), str(tmp_path / "312.exe"), str(tmp_path / "313.exe")]
    def probe(path, **kwargs):
        return dict(identity(python={paths[0]: "3.11.9", paths[1]: "3.12.10", paths[2]: "3.13.16"}[path]),
                    has_venv=True, has_ensurepip=True, prefix="base", base_prefix="base", bundled_pip="25.0")
    for path in paths:
        Path(path).touch()
    monkeypatch.setattr(python_discovery, "_candidate_paths", lambda *a: ((p, "path") for p in paths))
    monkeypatch.setattr(python_discovery, "probe_python", probe)
    found = python_discovery.discover_python("directml", preferred_minor="3.12")
    assert found["selected_python"] == paths[1]
    assert not found["candidates"][2]["compatible"]


def test_newer_existing_312_patch_is_preferred_over_managed_31210(monkeypatch, tmp_path):
    paths = [str(tmp_path / "managed.exe"), str(tmp_path / "newer.exe")]
    for path in paths:
        Path(path).touch()
    def probe(path, **kwargs):
        return dict(identity(python="3.12.10" if path == paths[0] else "3.12.14"),
                    has_venv=True, has_ensurepip=True, prefix="base", base_prefix="base", bundled_pip="25.0")
    monkeypatch.setattr(python_discovery, "_candidate_paths", lambda *a: ((p, "path") for p in paths))
    monkeypatch.setattr(python_discovery, "probe_python", probe)
    assert python_discovery.discover_python("directml", preferred_minor="3.12")["selected_python"] == paths[1]
    assert python_discovery.discover_python("directml", explicit=paths[0], preferred_minor="3.12")["selected_python"] == paths[0]
