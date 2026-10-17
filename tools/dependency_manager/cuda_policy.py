"""Conservative deposition policy. No driver installation or system Toolkit use.

CUDA 13 removes pre-Turing compilation/library support. CUDA 12.1 retains the
older known architectures. Full Toolkit driver baselines are used, rather than
assuming minor compatibility also guarantees NVRTC/PTX compilation.
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.request import Request

from .mirrors import ProxyConfig, _effective_proxy_map, _make_opener, _read_response

REFERENCES = (
    "https://docs.nvidia.com/cuda/archive/13.0.0/cuda-toolkit-release-notes/index.html",
    "https://docs.nvidia.com/cuda/archive/13.0.0/cuda-compiler-driver-nvcc/index.html",
    "https://docs.nvidia.com/cuda/archive/12.1.1/cuda-toolkit-release-notes/index.html",
    "https://docs.cupy.dev/en/stable/install.html",
    "https://pytorch.org/get-started/previous-versions/",
    "https://mitsuba.readthedocs.io/en/v3.9.0/",
)
# A reviewed set, not a floating point comparison accepting unknown future SMs.
LEGACY = {(5, 0), (5, 2), (5, 3), (6, 0), (6, 1), (6, 2), (7, 0), (7, 2)}
MODERN = {(7, 5), (8, 0), (8, 6), (8, 7), (8, 9), (9, 0)}
NEW = {(10, 0), (10, 3), (11, 0), (12, 0), (12, 1)}
PROFILES = {
    "cuda12": {"cupy": "cupy-cuda12x", "version": "14.2.0", "runtime": "12.1.1",
               "driver": {"linux": (530, 30, 2), "win32": (531, 14, 0)}},
    "cuda13": {"cupy": "cupy-cuda13x", "version": "14.2.0", "runtime": "13.0.0",
               "driver": {"linux": (580, 65, 6), "win32": (580, 0, 0)}},
}


def requirements(backend):
    profile = PROFILES[backend]
    # CuPy's official ctk extra installs only environment-local NVIDIA runtime
    # wheels, not nvcc, a driver or a Windows system Toolkit. Pin the CTK release
    # so a later 13.x PTX/runtime cannot outgrow the reviewed driver baseline.
    return (f"{profile['cupy']}[ctk]=={profile['version']}",
            f"cuda-toolkit=={profile['runtime']}")


def candidates(devices, identity):
    """Return ordered candidates for the actual default physical CUDA target."""
    platform = identity.get("platform")
    if (platform not in ("win32", "linux") or
            str(identity.get("machine", "")).lower() not in ("amd64", "x86_64") or
            identity.get("implementation") != "CPython" or
            not re.fullmatch(r"3\.1[0-3]\.\d+", str(identity.get("python", "")))):
        return [], "CPU: no supported native x86-64 CPython/CUDA wheel target"
    # The application uses CuPy Device(0). Do not select a capable secondary GPU
    # while installing an unsupported runtime for the default first GPU.
    gpus = [d for d in devices if d.get("source") == "nvidia_smi"]
    if any(os.environ.get(k) for k in ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER")):
        return [], "CPU: CUDA device remapping is set; default physical device cannot be proved"
    first = next((d for d in gpus if str(d.get("index", "0")) == "0"), None)
    if not first:
        return [], "CPU: NVIDIA model, physical index, compute capability and driver are unverified"
    try:
        cap = tuple(int(v) for v in first["compute_capability"].split("."))
        driver = tuple(int(v) for v in first["driver"].split("."))
        driver = (driver + (0, 0, 0))[:3]
        if len(cap) != 2 or cap not in LEGACY | MODERN | NEW:
            raise ValueError("unreviewed architecture")
    except (KeyError, TypeError, ValueError, AttributeError):
        return [], "CPU: unknown NVIDIA architecture or driver; no model-name guess"
    choices = []
    if cap in MODERN | NEW and driver >= PROFILES["cuda13"]["driver"][platform]:
        choices.append("cuda13")
    if cap in LEGACY | MODERN and driver >= PROFILES["cuda12"]["driver"][platform]:
        choices.append("cuda12")
    reason = (f"GPU 0 {first.get('name', '')}: SM {cap[0]}.{cap[1]}, driver {first['driver']}; "
              + ("CUDA13 excludes pre-Turing; CUDA12.1 retained for legacy/older drivers"
                 if choices else "no reviewed runtime/driver combination; CPU retained"))
    return choices, reason


def _metadata(package, version, proxy, cancel_event):
    proxy = proxy or ProxyConfig()
    proxy.validate()
    opener = _make_opener({"pypi.org"}, _effective_proxy_map(proxy))
    deadline = time.monotonic() + 6
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=6) as response:
        raw, cancelled, _ = _read_response(response, 1024 * 1024, cancel_event,
                                           require_eof=True, deadline=deadline)
    if cancelled:
        raise InterruptedError("CUDA wheel metadata check cancelled")
    return json.loads(raw)


def wheel_evidence(backend, identity, proxy=None, cancel_event=None):
    """Check root wheel tags live. Pip's full binary-only resolver remains required.

    An unavailable/unknown registry is not evidence that a package is usable.
    No wheel bytes are downloaded, no package is imported or installed.
    """
    profile = PROFILES[backend]
    tag = "cp" + "".join(identity["python"].split(".")[:2])
    platform = identity["platform"]
    try:
        data = _metadata(profile["cupy"], profile["version"], proxy, cancel_event)
        matches = []
        for row in data.get("urls", []):
            name = str(row.get("filename", ""))
            if row.get("yanked") or row.get("packagetype") != "bdist_wheel":
                continue
            if not name.endswith(".whl") or f"-{tag}-{tag}-" not in name:
                continue
            if ((platform == "win32" and name.endswith("-win_amd64.whl")) or
                    (platform == "linux" and "manylinux" in name and name.endswith("_x86_64.whl"))):
                matches.append(name)
        # The exact runtime metapackage must exist too. Its transitive components
        # and all other application requirements are checked by pip before install.
        runtime = _metadata("cuda-toolkit", profile["runtime"], proxy, cancel_event)
        runtime_ok = any(not r.get("yanked") and str(r.get("filename", "")).endswith(
                             ("-py3-none-any.whl", "-py2.py3-none-any.whl"))
                         for r in runtime.get("urls", []))
        return {"available": bool(matches) and runtime_ok, "backend": backend,
                "wheels": matches[:2], "runtime": profile["runtime"],
                "reason": "Live PyPI root wheels checked; complete binary-only pip resolution required before installation"}
    except InterruptedError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"available": False, "backend": backend,
                "reason": f"Wheel availability unknown ({type(exc).__name__}); CPU/older reviewed profile retained"}
