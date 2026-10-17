"""Standalone GUI executable entry; diagnostic modes never install anything."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import ssl
import struct
import sys
import threading
from pathlib import Path

from tools.dependency_manager.app_info import VERSION, default_installation_directory, icon_path
from tools.dependency_manager.git_runtime import git_runtime_manifest
from tools.dependency_manager.runtime import runtime_manifest
from tools.dependency_manager.runtime_paths import unlinked_local_path


def self_check():
    result = {"operation": "installer_self_check", "version": VERSION,
              "frozen": bool(getattr(sys, "frozen", False)), "status": "failed",
              "python": platform.python_version(), "architecture_bits": struct.calcsize("P") * 8,
              "default_project": str(default_installation_directory()), "tls": ssl.OPENSSL_VERSION,
              "installer_executed": False, "network_used": False, "project_python": "project-private CPython 3.12.10 from official NuGet; never this frozen EXE"}
    root = None
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        result.update(tcl=str(root.tk.call("info", "patchlevel")), tk=str(root.tk.call("package", "provide", "Tk")))
        result["runtime_manifest"] = runtime_manifest()["version"]
        result["git_manifest"] = git_runtime_manifest()["version"]
        result["icon_sha256"] = hashlib.sha256(icon_path().read_bytes()).hexdigest()
        result["default_target_allowed"] = unlinked_local_path(default_installation_directory())
        if not result["default_target_allowed"]:
            raise ValueError("Default installation target is not a safe writable-data location")
        if getattr(sys, "frozen", False):
            bundle = Path(sys._MEIPASS)
            result["extraction_write_rejected"] = not unlinked_local_path(bundle / "should-not-be-written")
            if not result["extraction_write_rejected"]:
                raise ValueError("Frozen extraction directory was accepted as mutable target")
        result["status"] = "success"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if root is not None:
            root.destroy()
    return result


def write_report(path, value):
    if path:
        destination = Path(path).absolute()
        if not unlinked_local_path(destination):
            raise ValueError("Diagnostic output must be outside the onefile extraction directory and symlinks")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif sys.stdout is not None:
        print(json.dumps(value, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Coating Sim standalone online installer")
    parser.add_argument("--project", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true", help="Offline Tk/resource self-check; never install")
    parser.add_argument("--self-check-output", type=Path)
    parser.add_argument("--smoke-gui", action="store_true", help="Open GUI briefly without invoking installation")
    parser.add_argument("--verify-private-runtime", action="store_true", help="Explicit diagnostic: deploy/reuse private Python at the explicitly selected project")
    parser.add_argument("--verify-venv", type=Path, help="Explicit diagnostic: create a new no-pip venv at this selected path")
    parser.add_argument("--python", type=Path, help="Normal external CPython for --verify-venv")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if args.verify_private_runtime:
        from tools.dependency_manager.mirrors import ProxyConfig
        from tools.dependency_manager.runtime import execute_runtime, plan_runtime
        if args.project is None:
            raise ValueError("--verify-private-runtime requires an explicit --project")
        proxy = ProxyConfig("env")
        plan = plan_runtime(project=args.project, proxy=proxy, runtime_mirror=True)
        result = execute_runtime(plan, True, cancel_event=threading.Event(), proxy=proxy)
        result["frozen"] = bool(getattr(sys, "frozen", False))
        write_report(args.self_check_output, result)
        return 0 if result["status"] == "success" else 2
    if args.verify_venv:
        from tools.dependency_manager.external_program import run
        from tools.dependency_manager.manager import clean_environment
        from tools.dependency_manager.python_discovery import discover_python
        target = args.verify_venv.absolute()
        if target.exists() or not unlinked_local_path(target) or not args.python:
            raise ValueError("Select an explicit normal Python and a new unlinked venv directory")
        found = discover_python("cpu", explicit=args.python, project=target.parent, cancel_event=threading.Event())
        if found["status"] != "ready":
            raise ValueError("Selected external Python is not a compatible normal CPython")
        run([found["selected_python"], "-I", "-m", "venv", "--without-pip", str(target)], check=True, timeout=60, env=clean_environment())
        python = target / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        probe = run([str(python), "-I", "-c", "import json,sys;print(json.dumps({'prefix':sys.prefix,'base':sys.base_prefix}))"], check=True, timeout=10, env=clean_environment(), capture_output=True, text=True)
        identity = json.loads(probe.stdout)
        valid = Path(identity["prefix"]).resolve() == target.resolve() and identity["prefix"] != identity["base"]
        write_report(args.self_check_output, {"operation": "frozen_external_python_venv", "status": "success" if valid else "failed", "frozen": bool(getattr(sys, "frozen", False)), "creation_python": found["selected_python"], "tool_executable": sys.executable, "venv": str(target), "identity": identity, "network_used": False, "packages_installed": False})
        return 0 if valid else 2
    if args.self_check:
        result = self_check()
        write_report(args.self_check_output, result)
        return 0 if result["status"] == "success" else 2
    from tools.dependency_manager.gui import DependencyManagerWindow, tk
    project = args.project or default_installation_directory()
    if not unlinked_local_path(project):
        raise ValueError("Choose a normal project directory outside the onefile extraction")
    root = tk.Tk()
    app = DependencyManagerWindow(root, project)
    visible = {}
    if args.smoke_gui:
        def finish_smoke():
            visible.update(operation="installer_gui_smoke", version=VERSION,
                           visible=bool(root.winfo_viewable()), geometry=root.geometry(),
                           source=app.source.get(), channel=app.update_channel.get(),
                           project=str(project), worker_started=app.busy)
            app.close()
        root.after(1800, finish_smoke)
    root.mainloop()
    if args.smoke_gui:
        visible["status"] = "success" if visible.get("visible") and not visible.get("worker_started") else "failed"
        write_report(args.self_check_output, visible)
        return 0 if visible["status"] == "success" else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
