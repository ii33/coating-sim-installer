"""Source preparation is a Git-only receipt, not a dependency installation."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tools.dependency_manager import project_deploy as deploy
from tools.dependency_manager import python_discovery

COMMIT = "a" * 40


class ProjectSourcePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.directory = self.parent / "光学仿真 new project"
        self.git = shutil.which("git") or sys.executable
        self.commands = []
        self.on_command = None
        self.commit = COMMIT
        self.status = ""

    def fake_git(self, git, args, **kwargs):
        self.commands.append(args)
        if self.on_command:
            self.on_command(args, kwargs)
        if args[:2] == ["ls-remote", "--get-url"]:
            return args[-1] + "\n"
        if args[0] == "ls-remote":
            return COMMIT + "\trefs/heads/main\n"
        if args[0] == "init":
            (self.directory / ".git").mkdir()
        if args[0] == "ls-tree":
            return "100644 blob " + COMMIT + "\trequirements.txt\0"
        if args[0] == "checkout":
            package = self.directory / "coat_sim"
            package.mkdir()
            (package / "__init__.py").write_text("raise RuntimeError('source must not execute')\n", encoding="utf-8")
            (self.directory / "launcher.py").write_text("raise RuntimeError('must not launch')\n", encoding="utf-8")
            (self.directory / "requirements.txt").write_text("example==1\n", encoding="utf-8", newline="\n")
            (self.directory / "requirements-desktop.txt").write_text("desktop-example==2\n", encoding="utf-8", newline="\n")
        if args[0] == "rev-parse":
            return self.commit + "\n"
        if args[0] == "status":
            return self.status
        return ""

    def prepare(self, **kwargs):
        with ExitStack() as stack:
            stack.enter_context(patch.object(deploy, "_run_git", side_effect=self.fake_git))
            stack.enter_context(patch.object(deploy, "find_managed_git", return_value=None))
            stack.enter_context(patch.object(deploy.shutil, "which", return_value=self.git))
            # None of these are necessary for a source checkout, even on a host
            # without a usable Python or package-registry configuration.
            for name in ("plan_environment", "execute_plan", "inspect_environment", "probe_python"):
                mock = stack.enter_context(patch.object(deploy.manager, name, side_effect=AssertionError(name)))
                self.addCleanup(mock.assert_not_called)
            discovery = stack.enter_context(patch.object(
                python_discovery, "discover_python", side_effect=AssertionError("Python discovery")))
            self.addCleanup(discovery.assert_not_called)
            registry = stack.enter_context(patch.object(deploy, "resolve_mirror", side_effect=AssertionError("registry")))
            self.addCleanup(registry.assert_not_called)
            return deploy.prepare_project_source(self.directory, **kwargs)

    def test_ready_receipt_exposes_pinned_source_without_dependency_plan(self):
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "ready", receipt)
        self.assertEqual(receipt["operation"], "project_source_prepare")
        self.assertTrue(receipt["source_only"])
        self.assertFalse(receipt["requires_confirmation"])
        self.assertEqual(receipt["directory"], str(self.directory))
        self.assertEqual(receipt["source"]["commit"], COMMIT)
        self.assertEqual(receipt["source"]["requested_ref"], "main")
        self.assertEqual(receipt["source"]["resolved_ref"], "refs/heads/main")
        self.assertEqual(receipt["plan_id"], deploy._plan_id(receipt))
        self.assertEqual(receipt["source_inputs"], [
            {"path": "requirements-desktop.txt", "sha256": hashlib.sha256(b"desktop-example==2\n").hexdigest()},
            {"path": "requirements.txt", "sha256": hashlib.sha256(b"example==1\n").hexdigest()},
        ])
        for field in ("dependency_plan", "profile", "groups", "backend", "bootstrap_python", "python"):
            self.assertNotIn(field, receipt)
        self.assertFalse((self.directory / ".venv").exists())
        self.assertIn(["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules", "origin", COMMIT], self.commands)

    def test_receipt_is_saved_separately_from_executable_plan(self):
        receipt = self.prepare()
        state = self.directory / ".git" / "dependency-manager-deploy"
        self.assertEqual(json.loads((state / "source.json").read_text(encoding="utf-8")), receipt)
        self.assertFalse((state / "plan.json").exists())
        with self.assertRaises(FileNotFoundError):
            deploy.read_project_deployment_plan(self.directory)

    def test_receipt_explicitly_rejected_by_dependency_executor(self):
        receipt = self.prepare()
        with patch.object(deploy, "_run_git") as git, patch.object(deploy.manager, "execute_plan") as execute:
            result = deploy.execute_project_deployment(receipt, confirmed=True)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Source-only", result["errors"][0])
        self.assertIn("not executable", result["errors"][0])
        git.assert_not_called()
        execute.assert_not_called()
        self.assertFalse((self.directory / ".git/dependency-manager-deploy/execute.lock").exists())

    def test_cnb_origin_and_explicit_git_use_the_same_verified_checkout(self):
        url = "https://cnb.cool/nonereason/coating-sim.git"
        receipt = self.prepare(source="cnb", repository_url=url, git_path=self.git)
        self.assertEqual(receipt["status"], "ready", receipt)
        self.assertEqual(receipt["provider"], "cnb")
        self.assertEqual(receipt["source"]["url"], url)
        self.assertTrue(receipt["git_explicit"])
        self.assertIn(["remote", "add", "origin", url], self.commands)

    def test_existing_unknown_directory_preserved_before_git(self):
        self.directory.mkdir()
        keep = self.directory / "keep.txt"
        keep.write_text("unrelated user content", encoding="utf-8")
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "blocked")
        self.assertIn("fresh directory", receipt["errors"][0])
        self.assertEqual(keep.read_text(encoding="utf-8"), "unrelated user content")
        self.assertEqual(self.commands, [])

    def test_stale_checkout_receipt_is_never_reused_or_replaced(self):
        state = self.directory / ".git/dependency-manager-deploy"
        state.mkdir(parents=True)
        stale = state / "source.json"
        stale.write_text('{"status": "cancelled"}', encoding="utf-8")
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(stale.read_text(encoding="utf-8"), '{"status": "cancelled"}')
        self.assertEqual(self.commands, [])

    def test_existing_file_is_not_deleted(self):
        self.directory.write_text("keep this file", encoding="utf-8")
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(self.directory.read_text(encoding="utf-8"), "keep this file")
        self.assertEqual(self.commands, [])

    def test_broken_symlink_is_not_followed_or_deleted(self):
        try:
            self.directory.symlink_to(self.parent / "missing", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("This host does not permit symlink creation")
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "blocked")
        self.assertTrue(self.directory.is_symlink())
        self.assertFalse((self.parent / "missing").exists())
        self.assertEqual(self.commands, [])

    def test_cancellation_before_creation_does_not_create_directory(self):
        cancel = threading.Event()
        cancel.set()
        receipt = self.prepare(cancel_event=cancel)
        self.assertEqual(receipt["status"], "cancelled")
        self.assertFalse(self.directory.exists())
        self.assertEqual(self.commands, [])

    def test_cancelled_fetch_preserves_partial_checkout_and_cancelled_receipt(self):
        def cancel_fetch(args, kwargs):
            if args[0] == "fetch":
                raise InterruptedError("Project fetch cancelled")
        self.on_command = cancel_fetch
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "cancelled")
        state = self.directory / ".git/dependency-manager-deploy"
        self.assertTrue(state.is_dir())
        self.assertEqual(json.loads((state / "source.json").read_text(encoding="utf-8")), receipt)
        self.assertFalse(any(command[0] == "checkout" for command in self.commands))
        self.assertNotIn("dependency_plan", receipt)

    def test_cancellation_at_final_git_check_does_not_return_ready(self):
        cancel = threading.Event()
        def cancel_status(args, kwargs):
            if args[0] == "status":
                cancel.set()
        self.on_command = cancel_status
        receipt = self.prepare(cancel_event=cancel)
        self.assertEqual(receipt["status"], "cancelled")
        self.assertTrue((self.directory / "requirements.txt").is_file())

    def test_cancellation_while_hashing_inputs_does_not_return_ready(self):
        cancel = threading.Event()
        source_inputs = deploy._source_inputs
        def cancelled_inputs(directory):
            inputs = source_inputs(directory)
            cancel.set()
            return inputs
        with patch.object(deploy, "_source_inputs", side_effect=cancelled_inputs):
            receipt = self.prepare(cancel_event=cancel)
        self.assertEqual(receipt["status"], "cancelled")
        self.assertEqual(receipt["source"]["commit"], COMMIT)
        self.assertTrue((self.directory / "requirements.txt").is_file())

    def test_timeout_preserves_partial_checkout_without_planning_dependencies(self):
        def timeout_fetch(args, kwargs):
            if args[0] == "fetch":
                raise TimeoutError("Project fetch timed out")
        self.on_command = timeout_fetch
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "timed_out")
        self.assertTrue(self.directory.is_dir())
        self.assertNotIn("dependency_plan", receipt)

    def test_commit_mismatch_is_blocked(self):
        self.commit = "b" * 40
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "blocked")
        self.assertIn("immutable commit", receipt["errors"][0])
        self.assertNotIn("source_inputs", receipt)

    def test_dirty_checkout_is_blocked(self):
        self.status = " M launcher.py\n"
        receipt = self.prepare()
        self.assertEqual(receipt["status"], "blocked")
        self.assertIn("not clean", receipt["errors"][0])

    def test_untrusted_origin_or_ref_is_blocked_before_directory_creation(self):
        for options in ({"repository_url": "https://github.com/other/project.git"}, {"ref": "../main"}):
            with self.subTest(options=options):
                receipt = self.prepare(**options)
                self.assertEqual(receipt["status"], "blocked")
                self.assertFalse(self.directory.exists())
                self.assertEqual(self.commands, [])

    def test_readonly_status_defaults_to_including_all_untracked_files(self):
        commands = []
        def run(args):
            commands.append(args)
            return ""
        self.assertEqual(deploy._readonly_status(run), "")
        self.assertEqual(commands, [
            ["config", "--null", "--name-only", "--list"],
            ["status", "--porcelain", "--untracked-files=all", "--ignore-submodules=all"],
        ])

    def test_tracked_only_status_still_disables_every_configured_filter(self):
        commands = []
        def run(args):
            commands.append(args)
            if args[0] == "config":
                return ("filter.payload.smudge\0filter.payload.clean\0filter.payload.process\0"
                        "filter.payload.required\0filter.payload.clean\0core.ignorecase\0")
            return " M launcher.py\n"
        self.assertEqual(deploy._readonly_status(run, untracked=False), " M launcher.py\n")
        self.assertEqual(commands, [
            ["config", "--null", "--name-only", "--list"],
            ["-c", "filter.payload.clean=", "-c", "filter.payload.process=",
             "-c", "filter.payload.required=false", "-c", "filter.payload.smudge=",
             "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all"],
        ])

    def test_tracked_only_status_rejects_unsafe_filter_before_status(self):
        commands = []
        def run(args):
            commands.append(args)
            return "filter.unsafe\nname.process\0"
        with self.assertRaisesRegex(ValueError, "Unsafe Git filter"):
            deploy._readonly_status(run, untracked=False)
        self.assertEqual(commands, [["config", "--null", "--name-only", "--list"]])


if __name__ == "__main__":
    unittest.main()
