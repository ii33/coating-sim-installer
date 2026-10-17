# Main-project update and desktop shortcut

The compact default GUI has the installation path, Browse, Install and Update. Details remain collapsed. Update means the selected public **main-project main branch**, not the installer's own Release. CNB remains the default; GitHub is independently selectable. Installer-Release checks stay explicitly labelled in Details.

An Update click authorizes a cancellable check and safe forward update at the selected target. An unchanged version label does not hide a newer source commit. No newer commit is clearly reported. Git uses the existing credential helper and transient proxy, HTTPS and HTTP/1.1; no global configuration is changed.

The updater refuses unknown directories/origins, modified tracked source, hidden index flags, divergent local commits, protected runtime paths and new tracked files colliding with local user data. Ignored/untracked project settings/results are retained; checkout uses no force/reset/clean and explicitly disables ignored-file overwrites. Custom Git filters/hooks do not run. A shared operation lease excludes duplicate installer/update operations, and existing dependency locks remain active.

Only the project's owned private CPython and .venv can receive automatically reconciled normal-user dependencies. Source and requirements are validated before the existing single-writer manager acts. Runtime files are not upgraded or relocated. A bounded atomic journal in .git records previous/new commits and success/version; dependency failure or cancellation attempts a no-force source rollback while the operation lease is held. If recovery is obstructed, the journal is retained and further automatic updates stop. Partial dependency changes are reported and can be reinspected/retried; user files are not deleted.

Successful Windows installation creates a per-user desktop Shell Link through the Windows known-folder API, so relocated/OneDrive/Chinese desktop paths are supported. It uses the project .venv Python, launcher.py, project working directory and existing coat_sim/ui/assets/icons/launcher.ico. Native Unicode COM readback verifies all four fields before publication. Publication is atomic and does not overwrite an existing file.

An installer-owned receipt makes retries reuse the exact existing shortcut. An unrelated Coating Sim.lnk is preserved and gets a deterministic per-install alternative name. A modified owned shortcut or unknown receipt stops with a clear diagnostic. QA uses isolated repositories and synthetic desktop directories; it never writes the real user desktop.
