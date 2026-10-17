"""Explicit, hash-pinned CPython bootstrap, separate from the tool's runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

from .manager import probe_python
from .manager import run_command as run_command
from .mirrors import ProxyConfig, _effective_proxy_map, _UniformProxyHandler, redact
from .runtime_paths import contained_runtime_path, project_root

MANIFEST_PATH = Path(__file__).with_name("runtime_manifest.json")


def runtime_manifest():
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if data["schema_version"] != 1 or data.get("distribution") != "official-nuget":
        raise ValueError("Unknown private runtime manifest schema")
    if set(data["artifacts"]) != {"x86_64"}:
        raise ValueError("Private runtime requires the reviewed Windows x64 package")
    artifact = data["artifacts"]["x86_64"]
    expected = f"https://api.nuget.org/v3-flatcontainer/python/{data['version']}/python.{data['version']}.nupkg"
    if artifact["url"] != expected:
        raise ValueError("Runtime must come from the pinned official NuGet HTTPS source")
    for key in ("sha256", "inventory_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", artifact[key]):
            raise ValueError("Invalid pinned runtime SHA-256")
    for mirror in artifact.get("mirrors", []):
        if mirror != {"id": "huawei", "url": _mirror_url(expected), "verified_on": data["verified_date"]}:
            raise ValueError("Unapproved runtime mirror")
    return data


def _mirror_url(url):
    if url.startswith("https://api.nuget.org/v3-flatcontainer/python/"):
        return url.replace("https://api.nuget.org/v3-flatcontainer/",
            "https://mirrors.huaweicloud.com/artifactory/api/nuget/v3/nuget-remote/", 1)
    return url.replace("https://www.python.org/ftp/python/", "https://mirrors.huaweicloud.com/python/", 1)


def managed_runtime_notice():
    return ("项目私有 Python 3.12.10 x64（CPython 官方 NuGet，含 venv/pip）；不扫描或复用系统 Python，"
            "不运行 EXE/MSI，不改注册或 PATH。3.12.10 非最新安全补丁。")



def _architecture(machine):
    machine = machine.lower()
    return {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "i386": "x86",
        "i686": "x86",
        "x86": "x86",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, "unsupported")


def default_runtime_directory(project=None):
    return project_root(project) / ".runtime" / "python"


def _registered_python_paths(version):
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    core_name = "Software\\Python\\PythonCore"
    minor = ".".join(version.split(".")[:2])
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                core = winreg.OpenKey(hive, core_name, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with core:
                for index in range(winreg.QueryInfoKey(core)[0]):
                    tag = winreg.EnumKey(core, index)
                    if not re.match(r"^" + re.escape(minor) + r"(?:t)?(?:$|-)", tag):
                        continue
                    directory = ""
                    try:
                        with winreg.OpenKey(core, tag + "\\InstallPath") as key:
                            try:
                                directory = str(winreg.QueryValueEx(key, "")[0])
                            except FileNotFoundError:
                                pass
                    except FileNotFoundError:
                        pass
                    # Empty/stale registrations still make installer maintenance unsafe.
                    found.append(directory)
    return sorted(set(found))


def plan_runtime(
    directory=None, backend="cpu", proxy=None, *, platform_name=None, machine=None, project=None,
    runtime_mirror=False,
):
    from .private_runtime import inspect_owned
    if not isinstance(runtime_mirror, bool):
        raise ValueError("runtime_mirror must be a boolean")
    manifest = runtime_manifest()
    platform_name = platform_name or sys.platform
    machine = _architecture(machine or platform.machine())
    selected_project = project_root(project) if project is not None or directory is None else None
    boundary = selected_project if selected_project is not None else Path.home().resolve()
    directory = Path(directory or default_runtime_directory(selected_project)).absolute()
    proxy = proxy or ProxyConfig()
    proxy.validate()
    ancestor = directory.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    parent_stat = ancestor.stat() if ancestor.is_dir() else None
    parent_identity = ({"path": str(ancestor.resolve()), "device": parent_stat.st_dev, "inode": parent_stat.st_ino}
                       if parent_stat else None)
    errors, warnings, action = [], [managed_runtime_notice()], "extract"
    if platform_name != "win32" or machine != "x86_64":
        errors.append("Project-private Python currently supports Windows x64 only")
    if backend not in ("cpu", "cuda12", "cuda13", "directml", "rocm", "xpu"):
        errors.append("Unknown backend")
    if not contained_runtime_path(directory, boundary):
        errors.append("Managed runtime destination must be an unlinked local path inside the selected project")
    if directory.exists() and not errors:
        try:
            inspect_owned(directory, manifest)
            action = "reuse"
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
    artifact = manifest["artifacts"]["x86_64"]
    plan = {
        "schema_version": 1, "operation": "runtime_plan", "status": "blocked" if errors else "ready",
        "action": action, "directory": str(directory), "project": str(selected_project) if selected_project else None,
        "parent_identity": parent_identity, "platform": platform_name, "architecture": machine,
        "version": manifest["version"], "distribution": manifest["distribution"],
        "artifact": {k: v for k, v in artifact.items() if runtime_mirror or k != "mirrors"},
        "runtime_mirror": runtime_mirror,
        "download_policy": {"connect_seconds": 10, "idle_read_seconds": 15, "attempts_per_source": 2,
                            "max_bytes": 128 * 1024 * 1024, "total_duration_limit": None},
        "source": manifest["source"], "license": manifest["license"], "proxy": proxy.public(),
        "backend": backend, "requires_confirmation": action != "reuse", "warnings": warnings, "errors": errors,
    }
    plan["plan_id"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


class _OfficialRedirect(HTTPRedirectHandler):
    def __init__(self, mirror_urls=()):
        super().__init__()
        self.mirror_urls = frozenset(mirror_urls)

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (
            parsed.scheme != "https"
            or (parsed.hostname not in ("www.python.org", "api.nuget.org") and newurl not in self.mirror_urls)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Runtime redirect is outside the official HTTPS source")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_runtime(
    artifact, destination, proxy, cancel_event, emit, *,
    timeout=10, idle_timeout=15, max_bytes=128 * 1024 * 1024,
):
    """Approved mirrors first when requested; all bytes use the official hash."""
    from .runtime_download import download

    parsed = urlsplit(artifact["url"])
    if (parsed.scheme != "https" or parsed.hostname not in ("www.python.org", "api.nuget.org")
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment):
        raise ValueError("Runtime must come from the fixed official HTTPS source")
    if not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
        raise ValueError("Invalid pinned runtime SHA-256")
    sources = []
    approved = _mirror_url(artifact["url"])
    for mirror in artifact.get("mirrors", []):
        if mirror.get("id") != "huawei" or mirror.get("url") != approved or not approved.startswith("https://mirrors.huaweicloud.com/"):
            raise ValueError("Unapproved Python runtime mirror")
        if not sources:
            sources.append({"id": "huawei", "url": approved})
    sources.append({"id": "official", "url": artifact["url"]})
    proxies = _effective_proxy_map(proxy)

    def opener_factory(url):
        mirror_urls = (approved,) if url == approved else ()
        opener = build_opener(
            _UniformProxyHandler(proxies), HTTPSHandler(), _OfficialRedirect(mirror_urls))
        return opener, Request(url, headers={"User-Agent": "CoatingSimInstaller/1"})

    return download(artifact, destination, sources, opener_factory, cancel_event, emit, proxy,
                    connect_timeout=timeout, idle_timeout=idle_timeout, max_bytes=max_bytes)


def installer_arguments(installer, directory, log_path=None):
    # All are documented CPython full-installer options, not shell strings.
    return [
        str(installer),
        *(["/log", str(log_path)] if log_path is not None else []),
        "/quiet",
        "InstallAllUsers=0",
        f"TargetDir={directory}",
        "PrependPath=0",
        "AppendPath=0",
        "Include_launcher=0",
        "InstallLauncherAllUsers=0",
        "AssociateFiles=0",
        "Shortcuts=0",
        "Include_pip=1",
        "Include_exe=1",
        "Include_lib=1",
        "Include_dev=1",
        "Include_tcltk=0",
        "Include_doc=0",
        "Include_test=0",
        "Include_tools=0",
    ]


def _verify_runtime_destination(plan):
    directory = Path(plan["directory"])
    if (
        not contained_runtime_path(directory, plan.get("project") or Path.home().resolve())
        or (directory.exists() and plan.get("action") != "reuse")
    ):
        raise ValueError(
            "Runtime target/ancestor changed or target appeared; refusing overwrite"
        )
    parent = plan.get("parent_identity")
    if not parent:
        raise ValueError("Managed runtime parent identity is unavailable")
    ancestor = Path(parent["path"])
    stat = ancestor.stat()
    if (
        ancestor != ancestor.resolve()
        or stat.st_dev != parent["device"]
        or stat.st_ino != parent["inode"]
    ):
        raise ValueError("Managed runtime parent identity changed")


def _windows_file_version(path):
    """Read fixed PE version metadata without executing installer components."""
    import ctypes

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise AttributeError("Windows library loader unavailable")
    library = loader(str(Path(os.environ["SystemRoot"]) / "System32/version.dll"))
    size_api = library.GetFileVersionInfoSizeW
    size_api.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    size_api.restype = ctypes.c_uint32
    read_api = library.GetFileVersionInfoW
    read_api.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    read_api.restype = ctypes.c_int
    query_api = library.VerQueryValueW
    query_api.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
    query_api.restype = ctypes.c_int
    size = size_api(str(path), None)
    if not size:
        raise OSError("UCRT version metadata is unavailable")
    buffer = ctypes.create_string_buffer(size)
    if not read_api(str(path), 0, size, buffer):
        raise OSError("UCRT version metadata could not be read")
    address, length = ctypes.c_void_p(), ctypes.c_uint32()
    if not query_api(buffer, "\\", ctypes.byref(address), ctypes.byref(length)) or not address.value or length.value < 52:
        raise OSError("UCRT fixed version metadata is unavailable")
    fixed = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32))
    if fixed[0] != 0xFEEF04BD:
        raise OSError("UCRT fixed version metadata is invalid")
    return (fixed[2] >> 16, fixed[2] & 0xFFFF, fixed[3] >> 16, fixed[3] & 0xFFFF)


def _verify_system_ucrt():
    """Conservative OS readiness gate; VC DLLs are installed application-locally."""
    import ctypes

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise AttributeError("Windows library loader unavailable")
    path = Path(os.environ["SystemRoot"]) / "System32/ucrtbase.dll"
    loader(str(path))
    version = _windows_file_version(path)
    # Match the official installer's CRTInstalled condition, preserving the gate
    # that avoids its optional UCRT deployment. See the bootstrap documentation.
    if version[:2] != (10, 0) or version[2:] < (10137, 0):
        raise OSError("Windows UCRT is older than the installer readiness threshold")


def execute_runtime(plan, confirmed=False, emit=None, cancel_event=None, proxy=None):
    from .private_runtime import deploy
    emit, cancel_event, proxy = emit or (lambda event: None), cancel_event or threading.Event(), proxy or ProxyConfig()
    result = {"schema_version": 1, "operation": "runtime_install", "status": "blocked",
              "directory": plan.get("directory"), "plan_id": plan.get("plan_id"), "errors": [], "warnings": [],
              "installer_executed": False, "registry_changed": False}
    if not confirmed and plan.get("action") != "reuse":
        result["errors"].append("Explicit confirmation of the private Python download and license is required")
        return result
    try:
        if cancel_event.is_set():
            raise InterruptedError("Private Python deployment cancelled")
        current = plan_runtime(plan["directory"], plan["backend"], proxy, project=plan.get("project"),
                               runtime_mirror=plan.get("runtime_mirror", False))
        if current != plan or current["status"] != "ready":
            result["errors"].append("Runtime plan is stale, altered or unsupported; confirm a fresh plan")
            return result
        identity = deploy(plan, runtime_manifest(),
                          lambda artifact, path, cancel, log: download_runtime(artifact, path, proxy, cancel, log),
                          probe_python, emit, cancel_event, _verify_runtime_destination)
        directory = Path(plan["directory"])
        if (identity.get("python") != plan["version"] or Path(identity["prefix"]).resolve() != directory.resolve()
                or not identity.get("has_venv") or not identity.get("has_ensurepip")):
            raise ValueError("Private Python installed identity/venv/pip verification failed")
        result.update(status="success", python=str(directory / "python.exe"), identity=identity,
                      downloaded=plan["action"] != "reuse", reused=plan["action"] == "reuse")
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        result["status"] = "cancelled" if cancel_event.is_set() else "failed"
        result["errors"].append(redact(str(exc), proxy))
    return result
