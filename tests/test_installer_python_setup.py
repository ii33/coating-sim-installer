import json
import subprocess
import sys
import threading
from pathlib import Path

from tools.dependency_manager import python_discovery, python_setup
from tools.dependency_manager.mirrors import ProxyConfig


def test_registered_compatible_python_is_reused_without_install_plan(monkeypatch,tmp_path):
    monkeypatch.setattr(python_setup,"plan_runtime",lambda **k:{"status":"ready","action":"reuse"})
    monkeypatch.setattr(python_setup,"execute_runtime",lambda *a:{"status":"success","reused":True,"installer_executed":False})
    code,detail,summary=python_setup.prepare_python(tmp_path,ProxyConfig("none"),threading.Event(),lambda p:None,lambda m:None,lambda k: (_ for _ in ()).throw(AssertionError("no confirmation needed for read-only owned reuse")))
    assert code==0 and json.loads(detail)["reused"] is True
    assert not list(tmp_path.iterdir())


def test_unusable_registration_remains_blocked_with_actionable_reason(monkeypatch,tmp_path):
    monkeypatch.setattr(python_setup,"plan_runtime",lambda **k:{"status":"blocked","errors":["unknown project runtime; preserved"]})
    calls=[]
    monkeypatch.setattr(python_setup,"execute_runtime",lambda *a:calls.append(a))
    code,detail,summary=python_setup.prepare_python(tmp_path,ProxyConfig("none"),threading.Event(),lambda p:None,lambda m:None,lambda k:"y")
    assert code==2 and "preserved" in detail and not calls


def test_frozen_runtime_never_becomes_discovery_candidate(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(python_discovery.shutil, "which", lambda *a: None)
    monkeypatch.setattr("tools.dependency_manager.runtime._registered_python_paths", lambda *a: [])
    assert all(source != "manager_base" and path != sys.executable for path, source in python_discovery._candidate_paths(tmp_path))


def test_existing_normal_interpreter_creates_isolated_venv(tmp_path):
    # This intentionally exercises venv creation, never network/pip installation.
    # The host environment and interpreter are read-only; only tmp_path is used.
    base = getattr(sys, "_base_executable", sys.executable)
    found = python_discovery.discover_python("cpu", explicit=base, project=tmp_path)
    assert found["status"] == "ready" and found["selected_python"]
    target = tmp_path / "app/.venv"
    subprocess.run([found["selected_python"], "-I", "-m", "venv", "--without-pip", str(target)], check=True, timeout=40)
    python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    report = json.loads(subprocess.check_output([str(python), "-I", "-c", "import sys,json;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"], text=True, timeout=10))
    assert Path(report["prefix"]).resolve() == target.resolve()
    assert report["prefix"] != report["base"]
    assert "include-system-site-packages = false" in (target / "pyvenv.cfg").read_text().lower()
