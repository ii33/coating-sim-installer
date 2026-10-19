"""Explicit-Python source entry used when the packaged Windows EXE is absent."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Script-free source dependency manager")
    parser.add_argument("-Cli", action="store_true")
    parser.add_argument("-Gui", action="store_true")
    parser.add_argument("-CheckOnly", action="store_true")
    parser.add_argument("-BootstrapProjectRoot", type=Path)
    parser.add_argument("-BootstrapProxyMode", choices=("env", "none", "http"), default="env")
    parser.add_argument("-BootstrapProxy", default="")
    parser.add_argument("-BootstrapPython", type=Path)
    options, remaining = parser.parse_known_args(argv)
    if options.Cli and options.Gui:
        parser.error("-Cli and -Gui are mutually exclusive")
    if options.BootstrapPython and options.BootstrapPython.resolve() != Path(sys.executable).resolve():
        parser.error("Set COAT_DEPENDENCY_PYTHON to the selected interpreter before starting the CMD entry")
    if options.CheckOnly:
        available = True
        if options.Gui:
            try:
                import tkinter  # noqa: F401
            except ImportError:
                available = False
        print(json.dumps({"operation": "bootstrap_check", "status": "ready" if available else "missing",
                          "python": sys.executable, "installed": False}))
        return 0 if available else 2
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from tools.dependency_manager.cli import main as manager_main

    if remaining and remaining[0].startswith("-"):
        operation = "gui" if options.Gui else "inspect" if options.Cli else "interactive"
    elif remaining:
        operation = remaining.pop(0)
    else:
        operation = "gui" if options.Gui else "inspect" if options.Cli else "interactive"
    if options.Gui and operation != "gui":
        parser.error("-Gui only accepts manager options")
    arguments = [operation, *remaining]
    if options.BootstrapProjectRoot and "--project" not in arguments:
        arguments.extend(("--project", str(options.BootstrapProjectRoot)))
    if options.BootstrapProxyMode != "env":
        arguments.extend(("--proxy-mode", options.BootstrapProxyMode))
    if options.BootstrapProxy:
        arguments.extend(("--proxy", options.BootstrapProxy))
    # The selected source interpreter only hosts the UI/CLI. Runtime deployment
    # still uses the hash-pinned project-private CPython in the manager.
    return int(manager_main(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
