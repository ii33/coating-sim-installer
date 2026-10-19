"""Offline fault injection for script-free Windows shortcut creation."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.dependency_manager import launchers


class ShortcutDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "测试 output.lnk"
        self.plan = {"root": str(self.root), "python": str(self.root / "python.exe"),
                     "icon": str(self.root / "launcher.ico")}

    def test_com_failure_is_redacted_and_does_not_spawn_powershell(self):
        with patch.object(launchers, "create_and_verify", side_effect=OSError("Access denied password=hunter2")) as native, \
                patch.object(launchers.subprocess, "run", side_effect=AssertionError("script host called")):
            with self.assertRaises(OSError) as caught:
                launchers._write_shortcut(self.path, self.plan)
        self.assertIn("Shell Link COM", str(caught.exception))
        self.assertIn("Access denied", str(caught.exception))
        self.assertNotIn("hunter2", str(caught.exception))
        native.assert_called_once_with(str(self.path), self.plan["python"],
            subprocess.list2cmdline(["-E", str(self.root / "launcher.py")]),
            str(self.root), self.plan["icon"])
        self.assertFalse(self.path.exists())

    def test_return_without_artifact_is_failure(self):
        with patch.object(launchers, "create_and_verify"):
            with self.assertRaisesRegex(OSError, "without creating"):
                launchers._write_shortcut(self.path, self.plan)

    def test_short_artifact_is_failure(self):
        def short(*_args):
            self.path.write_bytes(b"bad")
        with patch.object(launchers, "create_and_verify", side_effect=short):
            with self.assertRaisesRegex(OSError, "smaller than 76"):
                launchers._write_shortcut(self.path, self.plan)

    def test_valid_artifact_succeeds_without_script_host(self):
        def write(*_args):
            self.path.write_bytes(b"x" * 76)
        with patch.object(launchers, "create_and_verify", side_effect=write), \
                patch.object(launchers.subprocess, "run", side_effect=AssertionError("script host called")):
            self.assertIsNone(launchers._write_shortcut(self.path, self.plan))

    def test_diagnostics_redact_and_bound_bad_output(self):
        output = ("https://alice:pass@example.org/path?private=hidden "
                  "Authorization: Bearer bearer-secret password=\"multiple words secret\" "
                  "api_key=key-secret token=" + "s" * 2000)
        error = launchers._shortcut_failure("COM failed", stderr=output.encode(), stdout=b"x" * 5000)
        for secret in ("alice", "pass@", "hidden", "bearer-secret", "multiple words secret", "key-secret"):
            self.assertNotIn(secret, error)
        self.assertIn("[REDACTED]", error)
        self.assertIn("[truncated]", error)


if __name__ == "__main__":
    unittest.main()
