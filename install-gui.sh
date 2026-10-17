#!/bin/sh
# Explicit standard-library GUI entry. No package installation is attempted.
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2
PROBE='import sys, tkinter; assert sys.implementation.name == "cpython" and (3,10) <= sys.version_info[:2] <= (3,13); window = tkinter.Tk(); window.withdraw(); window.update_idletasks(); window.destroy()'
for PYTHON in "$ROOT/.runtime/python/bin/python3" "$ROOT/.runtime/python/bin/python" python3 python; do
    if command -v "$PYTHON" >/dev/null 2>&1 && "$PYTHON" -I -S -c "$PROBE" >/dev/null 2>&1; then
        exec "$PYTHON" "$ROOT/dependency_manager.py" gui "$@"
    fi
done
printf '%s\n' 'No compatible full CPython 3.10-3.13 with working Tcl/Tk was found.' 'Install an official Python for your platform with Tcl/Tk enabled, then rerun install-gui.sh.' 'https://www.python.org/downloads/' 'No system packages or security settings were changed.' >&2
exit 2
