# v0.1.0-alpha.1 validation

The release was built on Windows x64 with CPython 3.13.16 and PyInstaller 6.22.3. All build dependencies were installed into a new task-local venv; no existing project, Downloads extraction, or user Python was modified.

## Completed

- Full offline regression: **409 passed, 3 skipped, 71 subtests passed**. Two skips are POSIX-shell execution and Windows symlink privileges. One Tk close/confirmation fixture encountered transient initialization failure in the full process; the complete final GUI and new-feature suite was then run in a fresh process: **48 passed**, including that close/confirmation case.
- Ruff's selected correctness/import rules passed.
- The native AMD64 onefile archive contains its own Python DLL, `_tkinter`, Tcl/Tk DLLs and scripts, manifests, icon and license texts. No application scientific runtime is bundled.
- The final EXE was copied alone into a new directory containing Chinese characters and spaces, started from that directory with Python/Git absent from the process PATH, and passed its offline resource/Tk/TLS self-check with exit 0. This demonstrates runtime isolation; it is not a zero-Python-machine installation test.
- The final EXE itself discovered an explicitly selected existing normal CPython and used it to create a new no-pip `.venv` in an isolated task directory. The resulting prefix matched that directory and differed from its base prefix. The frozen EXE was not used as a venv builder. No network or package installation occurred in this check.
- A brief native GUI smoke run returned exit 0, reported a visible 800×680 Tk window, CNB and preview defaults, no worker start, and no project-directory creation.
- Offline update fixtures cover semantic version precedence, numeric prerelease ordering, stable/preview selection, drafts, downgrade avoidance, trusted download-page validation, offline/timeout/rate-limit results, absent projects, dirty worktree preservation and exact-SHA CNB version evidence. No check performs fetch, checkout, replacement or automatic download.
- GUI fixtures cover explicit page-opening clicks, Python reuse, worker-thread boundaries, exact confirmation identity, cancellation/close, full final diagnostics and bounded queues.

## Not established

This alpha does **not** claim a completed clean zero-Python end-to-end download/install/cancel/recovery run or scientific CPU/GPU computation acceptance. The earlier Sandbox runtime download timed out and is not counted as a pass. No old Sandbox was reopened for this release task. The EXE is unsigned and Windows-only; source bat/sh entries remain available.

The complete regression and executable checks are local results. GitHub workflow status is reported separately by the repository; an uploaded workflow is not itself a passing CI result.
