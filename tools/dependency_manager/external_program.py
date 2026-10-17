"""Keep PyInstaller's DLL search path out of normal Python/Git subprocesses."""
import ctypes
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

_DLL_LOCK = threading.RLock()


@contextmanager
def external_program_context():
    if os.name != "nt" or not getattr(sys, "frozen", False) or not getattr(sys, "_MEIPASS", None):
        yield
        return
    with _DLL_LOCK:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
        kernel.GetDllDirectoryW.restype = ctypes.c_uint32
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.restype = ctypes.c_int
        length = kernel.GetDllDirectoryW(0, None)
        previous = ctypes.create_unicode_buffer(length + 1)
        kernel.GetDllDirectoryW(len(previous), previous)
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous.value or None):
                raise ctypes.WinError(ctypes.get_last_error())


def clean_frozen_environment(env):
    bundle = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not bundle:
        return env
    extraction = Path(bundle).resolve()
    def in_bundle(value):
        try:
            path = Path(value).resolve()
            return path == extraction or extraction in path.parents
        except (OSError, ValueError):
            return False
    for name in list(env):
        if name.startswith("_PYI_") or (name in ("TCL_LIBRARY", "TK_LIBRARY") and in_bundle(env[name])):
            env.pop(name, None)
    if "PATH" in env:
        env["PATH"] = os.pathsep.join(part for part in env["PATH"].split(os.pathsep) if part and not in_bundle(part))
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def run(*args, **kwargs):
    with external_program_context():
        return subprocess.run(*args, **kwargs)


def popen(*args, **kwargs):
    with external_program_context():
        return subprocess.Popen(*args, **kwargs)
