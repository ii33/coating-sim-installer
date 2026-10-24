# Coating Sim Installer

[中文](README.md) · [Windows x64 releases](https://github.com/ii33/coating-sim-installer/releases)

A standalone online installer for the public [ii33/coating-sim](https://github.com/ii33/coating-sim) project. The frozen installer's Python/Tcl/Tk is separate from the application runtime. Opening the EXE needs no preinstalled Python.

## alpha.11

Download `CoatingSimInstaller-v0.1.0-alpha.11-windows-x64.exe` and verify the matching `SHA256SUMS.txt`. The default target is the EXE's directory, or the entry script's directory when running from source. A typed or browsed target and an explicit `--project` take precedence. Source defaults to CNB, with GitHub available. Tsinghua TUNA PyPI is enabled by default; explicit choices and a saved reviewed project index take precedence. No global pip configuration is changed.

After clicking Install, the installer extracts **CPython's official NuGet Python 3.12.10 x64** to `<target>/.runtime/python`. Only this private interpreter creates `<target>/app/.venv`. It does not discover/reuse system Python, run a Python EXE/MSI installer, or change registrations/PATH. Launch using `app/launch-coating.cmd`, which references the project venv only.

“Check / prepare Python” deploys or validates that project's owned runtime. An unchanged runtime is reused after its pinned file inventory is verified. Unknown existing runtimes, modified content and venvs based on external Python are preserved; choose a new target.

## Downloads and compatibility

Runtime mirrors are distinct from package indexes. Acceleration uses the verified Huawei Cloud NuGet mirror, then safely falls back to official NuGet on download/verification failure. Both must match the same pinned official-package SHA256. TLS remains enabled. Downloads may continue past 180 seconds while receiving bytes: connection/header waits are bounded at 10 seconds and no-data body reads at 15 seconds, with at most two fresh attempts per source and cancellable backoff. Size, archive-path and expanded-size guards remain; publication occurs atomically after verification.

TUNA is the default package index. Use CLI `--mirror pypi` for official sources or `--mirror ustc` for USTC. A package-index failure is reported and requires a fresh reviewed plan; indexes are never silently mixed.

Windows desktop uses Python 3.12 x64 with `torch-directml==0.2.5.dev240914` and its `torch==2.4.1` constraint. CUDA12/13 is chosen by device, driver and wheel compatibility; unavailable acceleration retains CPU. Minimal numerical probes do not establish full simulation, precision or performance qualification. See the [hardware policy](docs/hardware-cuda-selection.md).

Limitations: private deployment currently supports Windows x64 only. Pinned Python3.12.10 is not the newest3.12 security patch. The official NuGet package includes venv/pip but no Tcl/Tk; the packaged installer includes its own GUI runtime and the application uses Qt. Source GUI may explicitly select a Tcl/Tk interpreter solely to host the front end; the application still gets private Python.

## Source and build

On Windows, run the packaged `CoatingSimInstaller-*-windows-x64.exe` or `install.bat` beside it. Neither Developer Mode, PowerShell 7, a script-policy change, nor preinstalled Python is required. Without the packaged EXE, the source CMD entry needs an explicitly selected Python (`COAT_DEPENDENCY_PYTHON`) or an existing project-private runtime.

```text
python -m pip install -r requirements-build.txt
python -m pytest -q
python -m PyInstaller --noconfirm --clean installer.spec
```

`--self-check` and `--smoke-gui` are offline native diagnostics. Downloads, temporary deployment files and logs stay under the selected target. Diagnostic output must remain outside the onefile extraction directory. alpha.1 release files remain intact.

[Bootstrap details](docs/dependency-manager-bootstrap.md) · [Private runtime policy](docs/private-python-runtime.md) · [License](LICENSE)

MinGit binary downloads use the Huawei Cloud mirror first when mirrors are enabled, with official GitHub fallback. Both sources must match the same pinned official SHA-256. Choosing PyPI disables this mirror too. Project source selection is unchanged.

## Changes through alpha.11

The compact default interface supports one-click installation with expandable details. Update checks and updates the main application from the selected source main branch. Successful installation creates and verifies the current user desktop shortcut; a shortcut failure does not fail the install/update, is shown as a visible warning, and can be retried on its own. Installation, update, already-current, failed and cancelled outcomes are explicit; Exit becomes available after cleanup.

Git software itself (MinGit) defaults to the Huawei Cloud mirror with verified official fallback, independently of the CNB/GitHub project-source selection.

Desktop shortcuts now use `logolite.ico`, matching the application taskbar relaunch identity. Verified installer-owned shortcuts refresh atomically when the icon path or bytes change, while modified and unrelated links are preserved. Windows receipt tests now read UTF-8 fixtures explicitly.

[Performance evidence](docs/installation-performance.md) · [Application updates and desktop shortcuts](docs/project-update-and-desktop.md) · [Completion and exit](docs/terminal-outcomes.md)
