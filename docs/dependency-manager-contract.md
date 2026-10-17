# Project-private Python runtime (alpha.2)

The CPython [official Windows documentation](https://docs.python.org/3.12/using/windows.html#the-nuget-org-packages) describes the NuGet distribution. alpha.2 pins [Python3.12.10 x64](https://www.nuget.org/packages/python/3.12.10), authored by Python Software Foundation, rather than invoking a Windows installer.

Official package: `https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg`.

Verified mirror: `https://mirrors.huaweicloud.com/artifactory/api/nuget/v3/nuget-remote/python/3.12.10/python.3.12.10.nupkg`. This URL was resolved from the mirror's [NuGet service index](https://mirrors.huaweicloud.com/repository/nuget/v3/index.json), then the entire package was compared with official bytes.

Size 14,515,433 bytes. SHA256 `0eb85c2dfccccf1b17352de4c397f69194035b7d37149eacc16f1147d93de3b8`; the official NuGet catalog SHA512 also matched. The runtime inventory contains 1,322 files and its canonical digest is pinned in `runtime_manifest.json`. PSF/license and provenance are retained in `tools/dependency_manager/licenses`.

Deployment extracts only the package's `tools/` subtree. Preflight rejects traversal, absolute/drive/ADS paths, Windows reserved aliases, links, duplicate names and excessive members/sizes. Streaming extraction enforces actual member lengths and cancellation. A staging directory is verified before rename. No EXE/MSI, registration, PATH mutation, external Python discovery or system prerequisite scan occurs. If the verified interpreter cannot run, the post-deployment error is reported.

An ownership marker records the trusted per-file inventory. Reentry checks its digest against the pinned manifest and verifies original files; unknown/changed content is preserved. Interpreter identity, venv/ensurepip and private prefix are verified. Existing project venvs must belong to this runtime; foreign venvs are preserved. No environment is moved or copied.

Python download policy:10s connection/headers,15s no-data body reads, no180s total cutoff, at most2 attempts per source, fresh partial file per attempt, cancellable retry delay. A blocked read observes cancellation on return or within its configured socket bound. Both header and actual bytes are capped at128MiB; expanded data at256MiB. A complete pinned SHA256 is mandatory before atomic no-overwrite publication. Authentication/query strings are redacted from diagnostics.

TUNA is the default PyPI index. Runtime acceleration uses the Huawei NuGet mirror independently. Disable acceleration/`--mirror pypi` to use official sources. A mirror cannot replace or supply a new expected hash. TLS validation is never disabled.

Windows DirectML uses private CPython3.12 x64, torch-directml0.2.5.dev240914 and torch2.4.1. CUDA selection is separate and does not upgrade the DirectML torch dependency. CPU remains available when a GPU probe fails.

Python3.12.10 is not the latest security patch. NuGet omits Tcl/Tk: use the frozen installer for its bundled GUI, or an explicitly selected Tcl/Tk source-front-end host. The Qt application uses its own venv. Private runtime support is Windows x64; no universal GPU/performance claim is made.

Execution contract: recompute the reviewed plan before mutation, recheck directory identity under an exclusive lock, and preserve modified sources, unknown content and foreign venvs. Only configured public GitHub/CNB sources may be fetched. No force, private refs, history rewriting or automatic credential creation. New venv identity and isolated site configuration are checked before pip mutation. Launch entries refer only to the project venv and never overwrite existing scripts/shortcuts. Cancellation only manages owned subprocesses. Native regression, actual install/startup and frozen EXE acceptance are required before a Release.
