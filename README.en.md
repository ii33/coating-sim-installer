# Coating Sim Installer

[中文](README.md) · [Windows x64 releases](https://github.com/ii33/coating-sim-installer/releases)

A standalone Tk online installer for the public [ii33/coating-sim](https://github.com/ii33/coating-sim) project. This repository contains installer code and public resources only, not the simulation application or its datasets.

## Run

Download the single-file Windows x64 EXE and verify its `SHA256SUMS.txt`. The installer includes its own Python/Tcl/Tk runtime, so opening it does not require preinstalled Python. Choose a target directory, source (CNB by default, or GitHub), optional mirror acceleration and optional development tools. Review each complete plan before confirming.

“Check / prepare Python” reuses a compatible normal CPython instead of invoking another installer of an already-registered minor version. Unusable registrations remain blocked with the actual reason. Project dependencies go into `<target>/app/.venv`; the frozen installer runtime is never passed to `-m venv`. Existing Python, unknown directories and modified project files are preserved. Launch scripts and optional non-overwriting shortcuts retain the original icon.

Mutable project files, downloads and logs use the selected target. The default is `%LOCALAPPDATA%\CoatingSim`. The onefile extraction directory is reserved for bundled resources and is rejected as an installation/diagnostic target. Official CPython installation may create normal per-user registration and maintenance records.

## Updates

Read-only checks report installed/latest versions, sources and availability separately for the installer and the project. Installer channels are `stable` and `preview`; comparison follows semantic version precedence, including numeric prerelease identifiers. Offline, timeouts, API rate limits and missing releases are explicit outcomes.

An available installer update enables a button that opens the validated official Release page only after a click. Verify the new EXE's published SHA256; no background download, in-place replacement or running-binary overwrite occurs. Project checks never fetch, checkout or overwrite dirty worktrees. Date-based project previews are compared numerically; different commits at an identical version are not interpreted as an upgrade. CNB version text is verified only using the identical public GitHub commit, with unknown reported when unavailable.

## Source and build

The original GUI and terminal bat/sh entries remain available. Source GUI needs normal CPython 3.10–3.13 with Tcl/Tk. Windows bat uses PowerShell 5.1 without altering execution policy.

With a new build venv on Windows x64 / full CPython 3.13:

```text
python -m pip install -r requirements-build.txt
python -m pytest -q
python -m PyInstaller --noconfirm --clean installer.spec
```

Dependencies are pinned in `requirements-build-lock.txt`. Use `--self-check --self-check-output <absolute JSON path>` for an offline executable/Tk/resource check; `--smoke-gui` briefly opens the UI without installing. Explicit developer mode `--verify-venv <new path> --python <normal CPython> --self-check-output <JSON>` creates a no-pip isolated venv only, without network or project packages.

This unsigned alpha has targeted offline regression and native onefile startup checks. These do not establish a clean zero-Python end-to-end installation, cancellation/recovery, or scientific computation acceptance; the earlier failed Sandbox run is not counted as a pass.

## License

Installer code is MIT; original public copyright notices are retained. Bundled CPython, Tcl/Tk, OpenSSL and PyInstaller notices, including the bootloader exception, are included under `tools/dependency_manager/licenses`. Their respective licenses continue to apply.
