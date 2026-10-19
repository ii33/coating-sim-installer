"""Read-only, bounded host evidence and conservative end-user install advice.

Run off the GUI thread. This module never installs packages, drivers or changes
settings. GPU visibility and an install candidate do not prove runtime usability.
"""
from __future__ import annotations

import csv
import io
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .cuda_policy import REFERENCES, wheel_evidence
from .cuda_policy import candidates as cuda_candidates
from .external_program import run as external_run

END_USER_GROUPS = ("desktop", "appearance")
PROBE_TIMEOUT = 4
MAX_DEVICES = 16
_BACKENDS = ("cpu", "cuda12", "cuda13", "directml", "rocm", "xpu")
RUNTIME_PROBE_TIMEOUT = 20



# Self-contained subprocess scripts: importing the app core would import its
# services and defeat the stdlib-only standalone manager. Keep parity with
# coat_sim/core/compute_device_probe.py numerical checks.
_CUDA_PROBE = r'''
import json, sys
marker = "__COAT_SIM_DEVICE_PROBE__="
try:
    import cupy as cp
    count = int(cp.cuda.runtime.getDeviceCount())
    if count < 1:
        raise RuntimeError("未检测到 CUDA 设备")
    device = cp.cuda.Device(0)
    with device:
        probe = cp.asarray([1.0], dtype=cp.float64)
        value = float(cp.asnumpy(cp.sum(probe * probe)).reshape(-1)[0])
    if value != 1.0:
        raise RuntimeError("CUDA 设备计算探测结果异常")
    props = cp.cuda.runtime.getDeviceProperties(0)
    raw_name = props.get("name", "") if isinstance(props, dict) else ""
    if isinstance(raw_name, bytes):
        raw_name = raw_name.decode("utf-8", "replace")
    name = str(raw_name).replace("\x00", "").strip()[:160]
    result = {"available": True, "name": name, "reason": "",
              "cuda_runtime_major": int(cp.cuda.runtime.runtimeGetVersion()) // 1000}
except Exception as exc:
    result = {"available": False, "name": "", "reason":
              (type(exc).__name__ + (": " + str(exc) if str(exc) else ""))[:240]}
print(marker + json.dumps(result, ensure_ascii=False), flush=True)
'''

_DIRECTML_PROBE = r'''
import json, math
marker = "__COAT_SIM_DEVICE_PROBE__="
try:
    import torch
    import torch_directml
    device = None
    name = ""
    try:
        for index in range(int(torch_directml.device_count())):
            try:
                candidate_name = str(torch_directml.device_name(index)).replace("\x00", "").strip()
            except Exception:
                continue
            # Match the simulator's explicit DirectML target selection. The
            # default target name is left unknown if enumeration cannot prove it.
            if "amd" in candidate_name.lower() or "radeon" in candidate_name.lower():
                device = torch_directml.device(index)
                name = candidate_name[:160]
                break
    except Exception:
        pass
    if device is None:
        device = torch_directml.device()
    probe = torch.as_tensor([0.5], dtype=torch.float32).to(device)
    inside = ((probe > 0) & (probe < 1)) ^ torch.zeros_like(probe, dtype=torch.bool)
    trig = torch.sin(probe) ** 2 + torch.cos(probe) ** 2
    sample = torch.where(inside, torch.sqrt(probe ** 2) * trig, torch.zeros_like(probe))
    value = float(torch.sum(sample).detach().to("cpu").numpy())
    if not math.isclose(value, 0.5, rel_tol=1e-5):
        raise RuntimeError("DirectML 设备计算探测结果异常")
    result = {"available": True, "name": name, "reason": ""}
except Exception as exc:
    result = {"available": False, "name": "", "reason":
              (type(exc).__name__ + (": " + str(exc) if str(exc) else ""))[:240]}
print(marker + json.dumps(result, ensure_ascii=False), flush=True)
'''


def _run(argv, *, env=None):
    try:
        result = external_run(argv, capture_output=True, text=True,
                                timeout=PROBE_TIMEOUT, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0 and len(result.stdout) <= 65536:
            return result.stdout
    except (OSError, UnicodeError, subprocess.SubprocessError):
        pass
    return None


def _vendor(name):
    text = name.lower()
    if "nvidia" in text:
        return "nvidia"
    if "amd" in text or "radeon" in text or "advanced micro" in text:
        return "amd"
    if "intel" in text:
        return "intel"
    return "unknown"


def _windows_display_names():
    """Enumerate visible adapters through user32, without a script host."""
    import ctypes
    from ctypes import wintypes

    class DisplayDevice(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("device_name", wintypes.WCHAR * 32),
                    ("device_string", wintypes.WCHAR * 128), ("state_flags", wintypes.DWORD),
                    ("device_id", wintypes.WCHAR * 128), ("device_key", wintypes.WCHAR * 128)]

    enum = ctypes.WinDLL("user32").EnumDisplayDevicesW
    enum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DisplayDevice), wintypes.DWORD]
    enum.restype = wintypes.BOOL
    names = []
    for index in range(MAX_DEVICES):
        device = DisplayDevice()
        device.cb = ctypes.sizeof(DisplayDevice)
        if not enum(None, index, ctypes.byref(device), 0):
            break
        if device.device_string:
            names.append(device.device_string)
    return names


def _visible_gpus(host_platform):
    devices, warnings = [], []
    if host_platform == "win32":
        try:
            names = _windows_display_names()
            devices.extend({"name": name[:160], "vendor": _vendor(name),
                            "driver": "", "source": "windows_display_api"}
                           for name in names[:MAX_DEVICES])
        except (AttributeError, OSError):
            warnings.append("Windows display adapter API unavailable")
    elif host_platform == "linux":
        # PCI vendor IDs enumerate visibility only, including integrated GPUs.
        vendors = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}
        for entry in sorted(Path("/sys/class/drm").glob("card[0-9]*"))[:MAX_DEVICES]:
            if not re.fullmatch(r"card\d+", entry.name):
                continue
            try:
                vendor = vendors.get((entry / "device/vendor").read_text().strip(), "unknown")
                devices.append({"name": entry.name, "vendor": vendor, "driver": "",
                                "source": "linux_drm",
                                "kernel_driver": (entry / "device/driver").resolve().name
                                if (entry / "device/driver").exists() else ""})
            except OSError:
                continue
    elif host_platform == "darwin":
        profiler = shutil.which("system_profiler")
        if profiler:
            output = _run([profiler, "SPDisplaysDataType", "-json"])
            try:
                rows = json.loads(output or "{}").get("SPDisplaysDataType", [])
                for row in rows[:MAX_DEVICES]:
                    name = str(row.get("sppci_model", row.get("_name", "")))[:160]
                    devices.append({"name": name, "vendor": _vendor(name),
                                    "driver": "", "source": "macos_system_profiler"})
            except (ValueError, TypeError, AttributeError):
                warnings.append("macOS display adapter probe returned invalid data")
    smi = shutil.which("nvidia-smi")
    if smi and host_platform in ("linux", "win32"):
        output = _run([smi, "--query-gpu=index,name,driver_version,compute_cap", "--format=csv,noheader,nounits"])
        if output is not None:
            for row in list(csv.reader(io.StringIO(output)))[:MAX_DEVICES]:
                if len(row) == 4:
                    devices.append({"index": row[0].strip()[:8],
                                    "name": row[1].strip()[:160], "vendor": "nvidia",
                                    "driver": row[2].strip()[:80],
                                    "compute_capability": row[3].strip()[:16], "source": "nvidia_smi"})
        else:
            warnings.append("NVIDIA driver/device probe unavailable or timed out")
    return devices, warnings


def _runtime_probe(executable, backend):
    # Mirror the simulator's numerical device checks without importing Qt, NumPy,
    # or GPU packages into the dependency-manager process.
    from .manager import clean_environment
    script = _CUDA_PROBE if backend in ("cuda12", "cuda13") else _DIRECTML_PROBE
    with tempfile.TemporaryDirectory(prefix="coat-device-probe-") as cache:
        env = clean_environment()
        env["CUPY_CACHE_DIR"] = cache
        try:
            completed = external_run([str(executable), "-I", "-B", "-c", script], env=env,
                                     capture_output=True, text=True, timeout=RUNTIME_PROBE_TIMEOUT,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"available": False, "reason": "Isolated device probe timed out; no GPU availability claim"}
        except (OSError, UnicodeError) as exc:
            return {"available": False, "reason": "Isolated device probe failed: " + type(exc).__name__}
        code = completed.returncode
        if code:
            native = code & 0xFFFFFFFF
            return {"available": False, "exit_code": code, "reason":
                    f"Isolated device process exited {code} / 0x{native:08X}; "
                    + ("native access violation; driver/runtime failure, not proof of out-of-memory"
                       if native == 0xC0000005 else "driver/runtime probe failed; CPU retained")}
        output = completed.stdout if len(completed.stdout) <= 65536 else ""
    marker = "__COAT_SIM_DEVICE_PROBE__="
    for line in (output or "").splitlines():
        if line.startswith(marker):
            try:
                result = json.loads(line[len(marker):])
                if not isinstance(result, dict) or type(result.get("available")) is not bool:
                    break
                return result
            except (ValueError, AttributeError):
                break
    return {"available": False, "reason": "Isolated device probe produced no valid result"}


def _cuda_candidate(device, target_platform):
    # Compatibility helper for existing callers, sharing the single policy.
    target = {'platform': target_platform, 'python': '3.12.10',
              'machine': 'x86_64', 'implementation': 'CPython'}
    return 'cuda12' in cuda_candidates([device], target)[0]


def recommend_environment(python_identity=None, manual_backend=None, cancel_event=None,
                          proxy=None, check_wheels=True):
    """Read-only advice; root wheels and device math are separate evidence."""
    if manual_backend is not None and manual_backend not in _BACKENDS:
        raise ValueError('Unknown compute backend')
    host = {'platform': sys.platform, 'os': platform.system(),
            'release': platform.release(), 'machine': platform.machine()}
    target = {key: (python_identity or {}).get(key) for key in
              ('executable', 'python', 'platform', 'machine', 'implementation')}
    def cancelled():
        return cancel_event is not None and cancel_event.is_set()
    devices, warnings = ([], []) if cancelled() else _visible_gpus(sys.platform)
    backend, reason = 'cpu', 'CPU: no verified compatible GPU target'
    usable = {'cpu': None, 'cuda12': False, 'cuda13': False, 'directml': False}
    diagnostics, wheels = {}, []
    compatible = (target.get('platform') == sys.platform and
                  target.get('implementation') == 'CPython' and
                  str(target.get('machine', '')).lower() in ('amd64', 'x86_64'))
    choices, cuda_reason = cuda_candidates(devices, target)
    if not compatible:
        choices = []
    if compatible and target.get('executable') and not cancelled():
        probe = _runtime_probe(target['executable'], 'cuda12')
        diagnostics['cuda'] = probe if isinstance(probe, dict) else {'available': probe is True}
        major = diagnostics['cuda'].get('cuda_runtime_major')
        for known in ('cuda12', 'cuda13'):
            usable[known] = diagnostics['cuda'].get('available') is True and major == int(known[-2:])
    for choice in choices:
        if cancelled():
            break
        try:
            evidence = (wheel_evidence(choice, target, proxy, cancel_event) if check_wheels
                        else {'available': True, 'reason': 'Post-install binary resolver already ran'})
        except InterruptedError:
            return {'status': 'cancelled', 'selected_backend': 'cpu', 'warnings': [], 'errors': []}
        wheels.append(evidence)
        if evidence.get('available') is True:
            if not check_wheels and not usable[choice]:
                continue
            backend, reason = choice, cuda_reason + '; ' + evidence['reason']
            major = diagnostics.get('cuda', {}).get('cuda_runtime_major')
            break
    dml_platform = (compatible and target.get('platform') == 'win32' and
                    bool(re.match(r'3\.1[0-2]\.', str(target.get('python', '')))))
    if dml_platform and target.get('executable') and not cancelled():
        probe = _runtime_probe(target['executable'], 'directml')
        diagnostics['directml'] = probe if isinstance(probe, dict) else {'available': probe is True}
        usable['directml'] = diagnostics['directml'].get('available') is True
        if backend == 'cpu' and usable['directml']:
            backend, reason = 'directml', 'DirectML isolated numerical probe passed'
    if backend == 'cpu':
        reason = cuda_reason + '; no verified CUDA/wheel combination; CPU retained'
    dml_default = sys.platform == 'win32'
    warnings.append('Windows desktop installs DirectML by default; package presence is not device validation.')
    if sys.platform != 'win32':
        warnings.append('DirectML skipped: no supported default profile on this platform')
    if dml_default and not dml_platform:
        warnings.append('Default DirectML needs x64 CPython 3.10-3.12. Preserve an incompatible existing environment and select a new installation directory with Python 3.12.')
    if backend.startswith('cuda'):
        warnings.append('CUDA preferred for deposition, pending binary-only resolution and isolated numerical verification. No driver or system Toolkit install.')
    if manual_backend:
        warnings.append('Explicit backend retained for verification; failure never claims GPU usability')
    warnings.append('Film CUDA PyTorch needs a separate manual environment: do not replace DirectML-pinned torch 2.4.1. Mitsuba/DrJit has independent CPU/CUDA/OptiX requirements.')
    return {'schema_version': 1, 'host': host, 'python': target,
            'status': 'cancelled' if cancelled() else 'success',
            'operation': 'runtime_recommendation', 'visible_gpus': devices,
            'recommended_backend': backend, 'selected_backend': manual_backend or backend,
            'suggested_candidate': backend if backend != 'cpu' else ('directml' if dml_platform else None),
            'backend_usable': usable, 'probe_diagnostics': diagnostics, 'wheel_evidence': wheels,
            'reason': reason, 'warnings': warnings, 'references': list(REFERENCES),
            'modules': {'deposition': backend, 'directml_default_install': dml_default,
                        'film': 'CPU; CUDA Torch requires a separate manually managed environment',
                        'appearance': 'Mitsuba 3.9.0 / DrJit 1.4.0 independent CPU/CUDA/OptiX probe'},
            'groups': list(END_USER_GROUPS), 'profile': 'runtime',
            'install_requires_confirmation': True}


def verify_installed_backends(executable, backend, directml_default=False, cancel_event=None):
    """Run isolated minimal math after package verification; never installs/retries."""
    usable = {'cpu': True, 'cuda12': False, 'cuda13': False, 'directml': False}
    diagnostics = {}
    for name in ([backend] if backend.startswith('cuda') else []) + (['directml'] if directml_default or backend == 'directml' else []):
        if cancel_event is not None and cancel_event.is_set():
            return {'status': 'cancelled', 'recommended_backend': 'cpu', 'probe_diagnostics': diagnostics}
        result = _runtime_probe(executable, name)
        diagnostics[name] = result
        usable[name] = result.get('available') is True
        if name.startswith('cuda'):
            usable[name] = usable[name] and result.get('cuda_runtime_major') == int(name[-2:])
            if result.get('available') and not usable[name]:
                diagnostics[name] = dict(result, available=False, reason='Installed CUDA runtime major differs from the approved profile')
    if cancel_event is not None and cancel_event.is_set():
        return {'status': 'cancelled', 'recommended_backend': 'cpu', 'probe_diagnostics': diagnostics}
    selected = backend if usable.get(backend) else ('directml' if usable['directml'] else 'cpu')
    return {'operation': 'post_install_compute_validation', 'status': 'success',
            'selected_backend': backend, 'recommended_backend': selected,
            'backend_usable': usable, 'probe_diagnostics': diagnostics,
            'reason': 'Isolated minimal numerical checks only; no full job, precision or performance qualification',
            'warnings': [] if usable.get(backend) else ['Requested GPU failed; use the reported fallback. No driver was changed or automatic package reinstall attempted.']}
