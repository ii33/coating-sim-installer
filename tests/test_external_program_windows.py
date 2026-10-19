"""Windows GUI child processes keep their diagnostics without a console window."""
import os
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest

from tools.dependency_manager import external_program
from tools.dependency_manager.manager import run_command


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows console process flags")


def test_wrappers_preserve_group_flags_and_captured_streams():
    group = subprocess.CREATE_NEW_PROCESS_GROUP
    with patch.object(external_program.subprocess, "run") as run:
        external_program.run(["python", "-V"], creationflags=group, capture_output=True)
    assert run.call_args.kwargs["creationflags"] == group | subprocess.CREATE_NO_WINDOW
    assert run.call_args.kwargs["capture_output"] is True

    with patch.object(external_program.subprocess, "Popen") as popen:
        external_program.popen(["git", "status"], creationflags=group,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert popen.call_args.kwargs["creationflags"] == group | subprocess.CREATE_NO_WINDOW
    assert popen.call_args.kwargs["stdout"] == subprocess.PIPE
    assert popen.call_args.kwargs["stderr"] == subprocess.STDOUT


def test_real_console_child_is_hidden_and_failure_output_is_available():
    code = ("import ctypes,sys; "
            "print(int(ctypes.windll.kernel32.GetConsoleWindow())); "
            "print('child failure detail', file=sys.stderr); sys.exit(7)")
    result = external_program.run([sys.executable, "-I", "-c", code],
                                  capture_output=True, text=True, timeout=10)
    assert result.returncode == 7
    assert result.stdout.strip() == "0"
    assert "child failure detail" in result.stderr


def test_gui_command_streams_hidden_child_failure_to_events():
    code = ("import ctypes,sys; "
            "print(int(ctypes.windll.kernel32.GetConsoleWindow()), flush=True); "
            "print('pip diagnostic failure', file=sys.stderr, flush=True); sys.exit(9)")
    events = []
    status = run_command([sys.executable, "-I", "-c", code], events.append,
                         threading.Event(), timeout=10)
    assert status == 9
    lines = [event["text"] for event in events if event["event"] == "log"]
    assert "0" in lines
    assert "pip diagnostic failure" in lines
