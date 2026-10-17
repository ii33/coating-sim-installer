# Hardware-aware dependencies (unreleased candidate)

Published alpha.1 is unchanged. Dependency selection is separate from application
compute preferences and from each module's actual numerical acceptance.

Windows desktop installs `torch-directml==0.2.5.dev240914` by default alongside
CPU or CuPy CUDA dependencies. It pins Torch2.4.1/torchvision0.19.1; modern CUDA
Torch is manual-only in a separate environment. Existing incompatible Torch or
CuPy distributions block installation rather than being silently replaced.
Linux/macOS desktop defaults skip DirectML and explain the platform restriction.

Prefer an existing full CPython3.12 base interpreter; compatible existing
3.10/3.11 remain usable. Default Windows DirectML rejects Python3.13 and preserves
the existing venv, with instructions to choose a new installation directory.
The managed application download is official **3.12.10**, the last conventional
Windows full installer in this series, not the latest 3.12 security patch. Prefer
a newer compatible complete3.12 already installed when available. The frozen
installer runtime may remain3.13. Same-minor registration guards remain active:
reuse a compatible registered interpreter; never remove registrations or run
maintenance over an existing installation. Stale registrations remain blockers.

## Reviewed CUDA profiles

| Physical CUDA device0 | Driver prerequisite | Ordered candidates |
| --- | --- | --- |
| Reviewed Maxwell/Pascal/Volta SM5.x/6.x/7.0/7.2 | Linux530.30.02 / Windows531.14 | CUDA12, CPU |
| Reviewed Turing/Ampere/Ada/Hopper SM7.5/8.x/9.0 | Linux580.65.06 / WindowsR580 for13; otherwise12.1 baseline | CUDA13 then CUDA12, or CUDA12; CPU if unsupported |
| Reviewed SM10.0/10.3/11.0/12.0/12.1 | CUDA13 driver baseline | CUDA13, CPU |
| Unknown driver/SM, absent target, unsupported OS/Python/architecture | unverified | CPU |

Read physical index, model, driver and compute capability through a bounded
NVIDIA query. A display name, CIM/PCI visibility, or nvidia-smi's maximum CUDA
version is insufficient. A capable secondary GPU cannot qualify an older
device0. Unresolved CUDA device remapping retains CPU. Unknown future SMs are
not accepted by a broad numeric range. CUDA13's pre-Turing removal is enforced
even with a newer driver;12.1 uses fuller driver baselines because minor driver
compatibility alone does not guarantee NVRTC/PTX compilation.

Both profiles use CuPy14.2.0 (`cupy-cuda12x` or `cupy-cuda13x`), its official
`ctk` extra and pinned `cuda-toolkit==12.1.1` or `==13.0.0`. These install runtime
wheels inside the application environment, not a driver, nvcc or system Toolkit.
The CTK pin prevents later13.x PTX requirements silently outgrowing the reviewed
driver baseline. All application package constraints are resolved together.

Live official PyPI root wheel tags are checked for the target CPython/x86-64
platform. Missing/yanked wheels or unknown registry/target availability try an
older compatible profile or retain CPU. This is not a complete resolver claim:
the mandatory binary-only pip dry-run checks all transitive dependencies before
any package installation. Resolver failure stops; no source build or silent
plan substitution occurs. Manual CUDA requests also pass the hardware guard.
Before confirmation, show hardware/driver/SM, wheel evidence, selected profile,
module limitations and exact package actions.

## Isolated numerical validation

After package inspection, the target interpreter runs bounded child-process
CuPy FP64 deposition math and DirectML FP32 geometry/math/readback probes. The
CUDA runtime major must match the approved profile. Timeout, invalid response,
initialization failure and native `0xC0000005` produce explicit unavailable
diagnostics and a fallback recommendation. Access violation is not proof of OOM.
There is no automatic driver update or package reinstall. Cancellation stops
before launcher creation. DirectML installation does not force its device use.

Film CUDA Torch is separate and retains the main project's FP64/complex128 and
CPU-parity requirements. Mitsuba3.9.0/Dr.Jit1.4.0 retain independent CPU/CUDA/OptiX
probes; CuPy runtime choice does not relabel or qualify them.

Tests of rules and orchestration do not establish real GPU qualification. No
new end-to-end CUDA12/13/DirectML installation, workload numerical parity,
performance or driver-hang cancellation acceptance has been completed on NVIDIA
legacy/modern/Blackwell, Intel or AMD hardware for this candidate. Live wheel
metadata checks and official runtime hash readback are separate evidence.

## Official sources

- [CUDA13 removal and driver table](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html)
- [CUDA13.0 exact compiler architecture list](https://docs.nvidia.com/cuda/archive/13.0.0/cuda-compiler-driver-nvcc/index.html)
- [CUDA12.1 driver table](https://docs.nvidia.com/cuda/archive/12.1.1/cuda-toolkit-release-notes/index.html)
- [CuPy wheel/ctk installation](https://docs.cupy.dev/en/stable/install.html)
- [CUDA runtime metapackage](https://pypi.org/project/cuda-toolkit/)
- [DirectML dependencies](https://pypi.org/pypi/torch-directml/json)
- [PyTorch distributions](https://pytorch.org/get-started/previous-versions/)
- [Mitsuba3.9 requirements](https://mitsuba.readthedocs.io/en/v3.9.0/)
- [Python3.12.10 installers](https://www.python.org/downloads/release/python-31210/)

Implementation: `tools/dependency_manager/{cuda_policy,compute_recommendation,
manager,python_discovery,python_setup,runtime,interactive}.py` and
`runtime_manifest.json`. Tests: `tests/test_cuda_hardware_policy.py` plus existing
manager/discovery/transaction regression suites.
