# Default GUI private-role installation (unreleased local milestone)

The default GUI Install action now uses `default_install.run_default_installation`, not the former one-environment interactive transaction. This code is a local development milestone, not a new published installer release or a completed clean-Windows acceptance result. There is no silent legacy installation fallback.

## Ownership and source/frozen entries

The GUI snapshots directory, source, mirror, development profile and requested backend before starting its worker thread. `project_operation` serializes the operation. UI widgets stay on the Tk thread; the worker owns source/runtime/package transactions and emits only plans, progress, logs and completion records. Repeated Install/Retry clicks while busy do not start a second worker.

Both source GUI and frozen EXE GUI import this same coordinator statically. The frozen EXE provides only its own front-end runtime, not application Python. The standalone source CLI role commands remain available; this change does not make the frozen EXE a general Python interpreter or add generic CLI forwarding.

## Source-first transaction

1. Validate the fixed GitHub/CNB source and prepare the checkout with Git only. `prepare_project_source` does not discover Python, build dependency plans, import project code or execute project tests. Its separate `source.json` receipt is not an executable dependency plan.
2. Authenticate the checkout's known origin, immutable commit, nonlinked tree, unchanged tracked source and required binding module before any application interpreter probe. Unrelated untracked user files are preserved.
3. Deploy/reuse the pinned owned Python at `app/.runtime/python`. The base is contained in the application root, matching the strict runtime-manifest consumer. The old `container/.runtime/python` sibling layout is not a fallback. Foreign, unknown, partial or sibling-base app venvs are preserved and produce an actionable fresh-directory error.
4. Plan and confirm app runtime, deposition CPU alias, independent deposition DirectML, hardware-adapted deposition CUDA when reviewed evidence selects a profile, and appearance environments. CUDA never excludes DirectML. The app/CPU role is not presented as a separate redundant environment.
5. Execute all planned role transactions, then verify source/base/app identity again before preparing launch entries and the noncritical desktop shortcut.

The default path never discovers or borrows system Python, uses the frozen EXE as an application interpreter, or falls back to a single mixed GPU environment. Existing source/runtime ownership checks and the reviewed binary-only package resolver remain in force.

Source authentication and GUI read-only version checks use tracked-only Git status with content filters disabled. They do not enumerate every private-runtime or untracked user-data file, so a large `.runtime` tree cannot exhaust the bounded Git-output budget merely because the checked-out source does not ignore it. The general CLI project-status check still treats ordinary untracked user files as dirty: it checks all tracked edits first, then excludes only untracked `.runtime`/`.venv` directories from its user-file listing. Source edits still block updates; protected-path and untracked-file collision checks remain in the mutating updater.

## Outcomes, cancellation and retry

Every long-running runner owns its child-process cancellation and cleanup. Progress/log callbacks must not throw merely because cancellation was requested. The coordinator observes cancellation before starting the next transaction and after the active runner returns safely. Cancellation never emits successful installation completion; already prepared source, runtimes and environments remain intact.

A failed role reports its role ID and concrete component error, stops later writers and does not claim installation completion. Retry reauthenticates the prepared checkout and generates a fresh plan; it does not delete partial environments, steal stale locks, replace unknown data or silently switch backends. Unknown/incomplete venv ownership can require a fresh directory rather than an unsafe automatic repair.

Launch-script conflicts preserve existing files. A byte-identical installer script may be reused. Desktop-shortcut failure remains a successful installation with a concrete shortcut-only retry, not a full dependency reinstall.

## Managed project updates

All mutating project updates use this same coordinator after the updater's known-origin, clean-source, ancestry, protected-path and non-force checkout guards. There is no alternate legacy dependency updater or `END_USER_GROUPS` injection. Marker presence or a sibling base never authorizes an application interpreter probe. The coordinator requires the authenticated current app-contained layout before probing; unknown, malformed, foreign or old sibling-base app environments are preserved and rejected with an actionable fresh-directory error. No automatic migration is attempted. Read-only version checks remain available for these directories.

An unchanged source HEAD still runs managed-role repair/verification when the user requests an update. Completion requires both a successful coordinator exit and its verified completion record. Source rollback/recovery journals and partial-role files are retained on failure or cancellation; no runtime or user data is automatically deleted. An active or stale role-install lock blocks source checkout and is never stolen. Version-only checks do not enter the coordinator or mutate files. Role/probe evidence is carried through the update result into the GUI instead of being inferred from package presence.

## Evidence and limits

Completion reports installed roles separately from `backend_usable` numerical-probe evidence. No package-presence check sets a GPU ready. A manually selected CUDA/DirectML backend whose numerical probe fails produces an explicit failure, preserving all prepared roles. Automatic installation can prepare roles without a GPU-ready claim; it does not establish the main application's readiness-aware automatic dispatch policy.

Mocked hardware/planning/transaction tests and headless controller tests cover default dispatch, immutable choices, no external-Python route, role coexistence, cancellation, retry and error display. Native Tk/Windows and clean-machine full-install acceptance remain unverified on the current Linux host. This milestone does not establish complete native DLL/CUDA closure, driver availability, physical simulation accuracy or GPU performance. No film GPU-forward behavior is added.
