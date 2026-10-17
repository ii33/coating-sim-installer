# Hardware-aware dependencies (alpha.2)

Application Python is always the project-private official CPython NuGet3.12.10 x64. No system-Python discovery/reuse or EXE/MSI maintenance occurs. See [private runtime policy](private-python-runtime.md). The frozen installer runtime is independent.

Windows desktop includes torch-directml0.2.5.dev240914 with torch2.4.1. TUNA is the default package index; explicit or reviewed saved project choices take precedence. Optional accelerator failure retains CPU. Python3.12.10 is not the newest3.12 security patch. alpha.1 assets remain unchanged. Minimal probes are not full numerical or performance qualification.

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


## alpha.8 自动安装与明确选择

自动选择会显示请求模式、候选后端、实际计划和验证后可用后端。先完成 CPU 与受支持的 Windows DirectML 基础依赖；兼容的可选 CUDA 使用第二份独立计划，重新核对目标 CPython 身份、设备和驱动。新建环境的身份传递保留 implementation，避免将有效 CPython 误报为不支持的目标。

无 NVIDIA、设备探测失败、驱动未知或可选 CUDA 计划不可用时保留 CPU 基础安装。若可选 pip 安装失败，只有基础依赖的真实目标复核完整通过才能继续报告 CPU 安装成功；否则明确失败并保留现场。取消始终为取消。手选 CUDA 继续严格校验，并在设备、身份或数值验证失败时提示改选 auto/cpu；不把软件包安装成功当作 GPU 可用。

Windows ARM 和不支持的 Python/DirectML 组合仍会在运行时或依赖计划边界准确阻断；自动模式不会绕过平台支持限制。开发依赖 profile 与设备选择独立。保存的镜像/源码记录不会把界面 auto 改成 CUDA 请求。
