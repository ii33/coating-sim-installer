"""Explicit isolated runtime transactions for the Windows private-runtime layout.

The manifest binds local environments, not GPU readiness or complete native-DLL
closure. This entry point is deliberately separate from the legacy one-venv GUI
until Windows end-to-end acceptance establishes that migration is safe.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from . import manager
from .cuda_policy import candidates
from .mirrors import ProxyConfig, redact, resolve_mirror
from .private_runtime import inspect_owned
from .runtime import runtime_manifest
from .runtime_paths import contained_runtime_path, unlinked_local_path

SCHEMA_VERSION = 1
MANIFEST_RELATIVE = ".runtime/compute-runtimes.json"
_APP_GROUPS = ("cpu", "gui", "assistant", "interchange")
_LAYOUT = {
    "app": (".venv", _APP_GROUPS, "cpu"),
    "deposition_directml": (".runtime/envs/deposition-directml", ("cpu",), "directml"),
    "appearance": (".runtime/envs/appearance", ("cpu", "appearance"), "cpu"),
}


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def package_inventory_digest(packages):
    return hashlib.sha256("".join(
        f"{name}\0{version}\n" for name, version in sorted(packages.items())
    ).encode("utf-8")).hexdigest()


def role_layout(cuda_backend=None):
    """Four isolated environments, with CPU deposition sharing only the app env."""
    if cuda_backend not in (None, "cuda12", "cuda13"):
        raise ValueError("Unknown isolated CUDA profile")
    layout = dict(_LAYOUT)
    if cuda_backend:
        layout["deposition_cuda"] = (
            ".runtime/envs/deposition-cuda", ("cpu",), cuda_backend)
    return layout


def _private_base(root):
    base = root / ".runtime/python"
    pin = runtime_manifest()
    inspect_owned(base, pin)
    identity = manager.probe_python(base / "python.exe")
    if (identity.get("platform") != "win32" or
            identity.get("machine", "").lower() not in ("amd64", "x86_64") or
            identity.get("python") != pin["version"] or
            any(Path(identity.get(key, "")).absolute() != base
                for key in ("prefix", "base_prefix")) or
            Path(identity.get("executable", "")).absolute() != base / "python.exe"):
        raise ValueError("A verified project-private Windows x64 base Python is required")
    return identity, pin


def _snapshot(root, relative, groups, backend, profile):
    target = root / relative
    if not contained_runtime_path(target, root):
        raise ValueError("Runtime environment path is linked or outside the project")
    if not target.exists():
        return {"exists": False}
    _authenticate_environment(root, target)
    for name in ("Scripts/python.exe", "pyvenv.cfg"):
        if not contained_runtime_path(target / name, target) or not (target / name).is_file():
            raise ValueError("Role interpreter/config is linked or missing; preserved")
    inspection = manager.inspect_environment(root, target, groups, backend, profile)
    observed = inspection["target"]
    if (inspection["errors"] or not observed.get("valid") or
            Path(observed.get("base_prefix", "")).absolute() != root / ".runtime/python" or
            observed.get("platform") != "win32" or
            observed.get("python") != runtime_manifest()["version"]):
        raise ValueError("Existing role environment uses an external or unverified Python; preserved")
    # Dependency conflicts remain repairable, but identity/ownership never is.
    inventory = manager.probe_python(target / "Scripts/python.exe")["inventory"]
    _check_gpu_roots(inventory, backend)
    return {"exists": True, "python_sha256": hashlib.sha256(
        (target / "Scripts/python.exe").read_bytes()).hexdigest(),
        "pyvenv_cfg_sha256": hashlib.sha256((target / "pyvenv.cfg").read_bytes()).hexdigest(),
        "packages_sha256": package_inventory_digest(inventory)}


def _authenticate_environment(root, target):
    """Check existing executable/config bytes before any target interpreter probe."""
    for name in ("Scripts/python.exe", "pyvenv.cfg"):
        if not contained_runtime_path(target / name, target) or not (target / name).is_file():
            raise ValueError("Role interpreter/config is linked or missing; preserved")
    owned = inspect_owned(root / ".runtime/python", runtime_manifest())
    trusted = {owned["files"][name]["sha256"] for name in (
        "python.exe", "Lib/venv/scripts/nt/python.exe", "Lib/venv/scripts/nt/venvlauncher.exe")
        if name in owned["files"]}
    interpreter = target / "Scripts/python.exe"
    if interpreter.stat().st_size > 64 * 1024 * 1024 or hashlib.sha256(interpreter.read_bytes()).hexdigest() not in trusted:
        raise ValueError("Existing role interpreter is not from the pinned private base; preserved without execution")
    path = target / "pyvenv.cfg"
    if path.stat().st_size > 65536:
        raise ValueError("Existing role configuration exceeds its size bound; preserved")
    config = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        key = key.strip().lower()
        if not separator or key in config:
            raise ValueError("Existing role configuration is ambiguous; preserved")
        config[key] = value.strip()
    base = root / ".runtime/python"
    if (config.get("include-system-site-packages", "").lower() != "false" or
            config.get("version") != runtime_manifest()["version"] or
            any(not config.get(key) or Path(config[key]) != expected or not Path(config[key]).is_absolute()
                for key, expected in (("home", base), ("executable", base / "python.exe")))):
        raise ValueError("Existing role configuration does not belong to the private base; preserved without execution")


def _check_gpu_roots(packages, backend):
    cupy = {name for name in packages if name == "cupy" or name.startswith("cupy-cuda")}
    conflicting = set()
    if backend == "directml":
        conflicting.update(cupy)
    elif backend in ("cuda12", "cuda13"):
        conflicting.update(cupy - {"cupy-cuda12x" if backend == "cuda12" else "cupy-cuda13x"})
        if "torch-directml" in packages:
            conflicting.add("torch-directml")
    if conflicting:
        raise ValueError("Conflicting GPU distribution roots in private role; preserved: " + ", ".join(sorted(conflicting)))


def _existing_manifest(root):
    path = root / MANIFEST_RELATIVE
    if not contained_runtime_path(path, root):
        raise ValueError("Compute-runtime manifest path is linked or outside the project")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Unknown compute-runtime manifest; preserved")
    raw = path.read_bytes()
    def unique(pairs):
        value = {}
        for name, item in pairs:
            if name in value:
                raise ValueError("Ambiguous compute-runtime manifest; preserved")
            value[name] = item
        return value
    data = json.loads(raw, object_pairs_hook=unique)
    allowed = set(role_layout("cuda12"))
    pin = runtime_manifest()
    expected_base = {"root": ".runtime/python", "python": ".runtime/python/python.exe",
                     "version": pin["version"], "inventory_sha256": pin["artifacts"]["x86_64"]["inventory_sha256"]}
    if (not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != 1 or
            data.get("platform") != "win32" or data.get("architecture") != "x86_64" or
            set(data) != {"schema_version", "platform", "architecture", "base", "environments", "roles"} or
            data.get("base") != expected_base or not isinstance(data.get("environments"), dict) or
            "app" not in data["environments"] or
            not set(data["environments"]).issubset(allowed) or
            not isinstance(data.get("roles"), dict) or
            data["roles"] != {**{name: name for name in data["environments"]}, "deposition_cpu": "app"}):
        raise ValueError("Unknown compute-runtime manifest; preserved")
    for name, env in data["environments"].items():
        relative = role_layout("cuda12")[name][0]
        backend = env.get("backend") if isinstance(env, dict) else None
        if (not isinstance(env, dict) or set(env) != {"root", "python", "backend", "python_sha256",
                "pyvenv_cfg_sha256", "packages", "packages_sha256"} or
                env.get("root") != relative or env.get("python") != relative + "/Scripts/python.exe" or
                backend not in (("cuda12", "cuda13") if name == "deposition_cuda" else
                                (role_layout("cuda12")[name][2],)) or
                not isinstance(env.get("packages"), dict) or not env["packages"]):
            raise ValueError("Unknown compute-runtime environment binding; preserved")
        for digest_name in ("python_sha256", "pyvenv_cfg_sha256", "packages_sha256"):
            if not isinstance(env[digest_name], str) or not re.fullmatch(r"[0-9a-f]{64}", env[digest_name]):
                raise ValueError("Unknown compute-runtime inventory digest; preserved")
        if any(not isinstance(n, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", n) or
               not isinstance(v, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]{0,127}", v)
               for n, v in env["packages"].items()):
            raise ValueError("Unknown compute-runtime package inventory; preserved")
        if package_inventory_digest(env["packages"]) != env["packages_sha256"]:
            raise ValueError("Unknown compute-runtime inventory digest; preserved")
    return hashlib.sha256(raw).hexdigest()


def _verify_sources(root, plan):
    for relative, expected in plan["source_sha256"].items():
        path = root / relative
        if not contained_runtime_path(path, root) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Project requirements changed; review a new runtime role plan")


def plan_runtime_roles(root, *, cuda_backend="auto", profile="runtime", mirror=None, proxy=None):
    """Read-only plan. Bootstrap the pinned private base with runtime-install first.

    Auto selects the reviewed hardware/driver CUDA candidate, independently of
    DirectML. Actual binary dependency resolution occurs per isolated env.
    """
    root = Path(root).absolute()
    proxy = proxy or ProxyConfig()
    result = {"schema_version": 1, "operation": "runtime_roles_plan", "status": "blocked",
              "root": str(root), "requested_cuda": cuda_backend, "profile": profile,
              "mirror": mirror, "proxy": proxy.public(), "environments": [], "roles": {},
              "requires_confirmation": True, "errors": [], "warnings": [
                  "Binding/package installation is not proof of GPU readiness or full native dependency closure.",
                  "This opt-in transaction does not migrate existing external-Python environments or change system settings."]}
    try:
        proxy.validate()
        if manager.sys.platform != "win32":
            raise ValueError("Isolated project-private runtime execution currently supports Windows x64 only")
        if cuda_backend not in ("auto", "cpu", "cuda12", "cuda13"):
            raise ValueError("CUDA selection must be auto, cpu, cuda12 or cuda13")
        if profile not in manager.PROFILES:
            raise ValueError("Unknown dependency profile")
        if not root.is_dir() or not unlinked_local_path(root):
            raise ValueError("Select an existing unlinked local project checkout")
        for relative in ("requirements.txt", "requirements-test.txt", "launcher.py", "coat_sim/compute_runtime_manifest.py"):
            if not (root / relative).is_file() or not contained_runtime_path(root / relative, root):
                raise ValueError("Project source lacks the managed runtime contract: " + relative)
        result["mirror"] = resolve_mirror(root, mirror)
        base_identity, pin = _private_base(root)
        result["base"] = {"root": ".runtime/python", "python": ".runtime/python/python.exe",
                          "version": pin["version"], "inventory_sha256": pin["artifacts"]["x86_64"]["inventory_sha256"]}
        from .compute_recommendation import _visible_gpus
        devices, warnings = _visible_gpus("win32")
        choices, reason = candidates(devices, base_identity)
        selected = (choices[0] if choices else None) if cuda_backend == "auto" else (
            None if cuda_backend == "cpu" else cuda_backend)
        if selected and selected not in choices:
            raise ValueError("Requested CUDA profile is not a reviewed hardware/driver combination: " + reason)
        result.update(selected_cuda=selected, cuda_reason=reason, devices=devices)
        result["warnings"].extend(warnings)
        paths = ["requirements.txt", "requirements-test.txt"]
        if profile == "development":
            paths.append("requirements-analysis.txt")
        result["source_sha256"] = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}
        result["previous_manifest_sha256"] = _existing_manifest(root)
        env_parent = root / ".runtime/envs"
        if not contained_runtime_path(env_parent, root) or (env_parent.exists() and not env_parent.is_dir()):
            raise ValueError("Isolated environment parent is linked or conflicting; preserved")
        for name, (relative, groups, backend) in role_layout(selected).items():
            _, rows = manager.catalog(root, groups, backend, profile)
            result["environments"].append({"id": name, "root": relative,
                "python": relative + "/Scripts/python.exe", "backend": backend,
                "groups": list(groups), "requirements": [r["requirement"] for r in rows],
                "snapshot": _snapshot(root, relative, groups, backend, profile)})
            result["roles"][name] = name
        result["roles"]["deposition_cpu"] = "app"
        result["status"] = "ready"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result["errors"].append(redact(str(exc), proxy))
    result["plan_id"] = _digest(result)
    return result


def _environment_record(root, item, profile):
    _snapshot(root, item["root"], item["groups"], item["backend"], profile)
    inspection = manager.inspect_environment(root, root / item["root"], item["groups"], item["backend"], profile)
    if inspection["errors"] or not inspection["target"].get("valid") or any(
            r["status"] != "satisfied" for r in inspection["packages"]):
        raise ValueError("Final role dependency verification failed: " + item["id"])
    packages = manager.probe_python(root / item["python"])["inventory"]
    _check_gpu_roots(packages, item["backend"])
    if (not isinstance(packages, dict) or any(
            not isinstance(n, str) or manager.normalized(n) != n or
            not isinstance(v, str) or not v or len(v) > 128 for n, v in packages.items())):
        raise ValueError("Invalid normalized package inventory")
    return {"root": item["root"], "python": item["python"], "backend": item["backend"],
            "python_sha256": hashlib.sha256((root / item["python"]).read_bytes()).hexdigest(),
            "pyvenv_cfg_sha256": hashlib.sha256((root / item["root"] / "pyvenv.cfg").read_bytes()).hexdigest(),
            "packages": packages, "packages_sha256": package_inventory_digest(packages)}


def _publish_exclusive(source, destination):
    if os.name == "nt":
        source.rename(destination)
    else:
        os.link(source, destination)


def _publish_manifest(root, manifest, expected_previous):
    if _existing_manifest(root) != expected_previous:
        raise ValueError("Compute-runtime manifest changed concurrently; preserved")
    path = root / MANIFEST_RELATIVE
    fd, name = tempfile.mkstemp(prefix=".compute-runtimes-", dir=path.parent)
    temporary = Path(name)
    backup, recovery = None, None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(manifest, output, sort_keys=True, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if _existing_manifest(root) != expected_previous:
            raise ValueError("Compute-runtime manifest changed concurrently; preserved")
        if expected_previous is not None:
            # A plain check+os.replace is not CAS and can destroy an unrelated
            # concurrent writer. Move to a unique owned recovery directory,
            # inspect what actually moved, then publish create-only. Readers
            # refuse the operation lock throughout this brief path gap.
            recovery = Path(tempfile.mkdtemp(prefix=".compute-runtimes-recovery-", dir=path.parent))
            backup = recovery / path.name
            path.rename(backup)
            if hashlib.sha256(backup.read_bytes()).hexdigest() != expected_previous:
                raise ValueError("Compute-runtime manifest changed during publication; preserved")
        _publish_exclusive(temporary, path)
        if backup is not None:
            if hashlib.sha256(backup.read_bytes()).hexdigest() != expected_previous:
                raise ValueError("Recovery copy changed; preserved for inspection: " + str(backup))
            backup.unlink()
    except (OSError, ValueError) as exc:
        if backup is not None and backup.exists():
            try:
                _publish_exclusive(backup, path)
                backup.unlink(missing_ok=True)
            except OSError:
                raise ValueError("Concurrent manifest preserved; prior/recovery copy retained at " + str(backup)) from exc
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if recovery is not None:
            try:
                recovery.rmdir()
            except OSError:
                pass  # A recovery copy is evidence, never cleanup fodder.


def execute_runtime_roles(plan, confirmed=False, emit=None, cancel_event=None, proxy=None):
    """Run all approved isolated transactions, publish bindings only on success.

    Failure/cancellation preserves existing and partially prepared environments.
    No old binding is published as fresh evidence after a failed package writer.
    """
    emit = emit or (lambda event: None)
    cancel_event = cancel_event or threading.Event()
    proxy = proxy or ProxyConfig()
    result = {"schema_version": 1, "operation": "runtime_roles_install", "status": "blocked",
              "plan_id": plan.get("plan_id"), "components": [], "errors": [], "warnings": []}
    if not confirmed:
        result["errors"].append("Review and confirm the current isolated runtime plan first")
        return result
    descriptor, lock, lock_identity = None, None, None
    try:
        root = Path(plan["root"])
        def fresh_plan():
            return plan_runtime_roles(root, cuda_backend=plan["requested_cuda"], profile=plan["profile"],
                                      mirror=plan["mirror"], proxy=proxy)
        if fresh_plan() != plan or plan["status"] != "ready":
            raise ValueError("Runtime role plan is stale, altered or blocked; review a fresh plan")
        if cancel_event.is_set():
            raise InterruptedError("Runtime preparation cancelled")
        lock = root / ".runtime/compute-runtime-install.lock"
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError("Runtime preparation/recovery lock already exists: " + str(lock) +
                ". Do not remove it while an installer is running. After confirming that installer has stopped, "
                "preserve all environments, remove only this stale lock, and review a fresh runtime-roles-plan before retrying.") from None
        lock_identity = os.fstat(descriptor)
        os.write(descriptor, json.dumps({"schema_version": 1, "pid": os.getpid(), "operation": "runtime_roles_install"}).encode())
        if fresh_plan() != plan:
            raise ValueError("Runtime inputs changed while acquiring the operation lock")
        parent = root / ".runtime/envs"
        if not contained_runtime_path(parent, root):
            raise ValueError("Runtime parent changed; existing contents preserved")
        parent.mkdir(exist_ok=True)
        for item in plan["environments"]:
            if cancel_event.is_set():
                raise InterruptedError("Runtime preparation cancelled")
            _private_base(root)
            _verify_sources(root, plan)
            if _snapshot(root, item["root"], item["groups"], item["backend"], plan["profile"]) != item["snapshot"]:
                raise ValueError("Role environment changed before execution: " + item["id"])
            nested = manager.plan_environment(root, root / item["root"], item["groups"], item["backend"],
                plan["mirror"], "repair", root / ".runtime/python/python.exe", proxy, plan["profile"])
            if nested["status"] != "ready" or [r["requirement"] for r in nested["packages"]] != item["requirements"]:
                raise ValueError("Role dependency scope changed or is blocked: " + item["id"] + "; " + "; ".join(nested["errors"]))
            emit({"event": "phase", "phase": "runtime_role", "component": item["id"], "text": item["id"]})
            component = manager.execute_plan(nested, True, emit, cancel_event, proxy)
            result["components"].append({"id": item["id"], "result": component})
            if component["status"] != "success":
                result["status"] = "cancelled" if component["status"] == "cancelled" else "failed"
                result["errors"].append("Isolated runtime preparation did not complete: " + item["id"])
                result["warnings"].append("Previous binding metadata was not refreshed; changed environments must pass strict validation or be repaired before managed use.")
                return result
        if cancel_event.is_set():
            raise InterruptedError("Runtime preparation cancelled")
        _private_base(root)
        _verify_sources(root, plan)
        manifest = {"schema_version": 1, "platform": "win32", "architecture": "x86_64",
                    "base": plan["base"], "roles": plan["roles"], "environments": {
                        item["id"]: _environment_record(root, item, plan["profile"])
                        for item in plan["environments"]}}
        if cancel_event.is_set():
            raise InterruptedError("Runtime preparation cancelled")
        _publish_manifest(root, manifest, plan["previous_manifest_sha256"])
        result.update(status="success", manifest=str(root / MANIFEST_RELATIVE), roles=plan["roles"])
        result["warnings"].append("Bindings verified; GPU readiness and complete native-DLL closure are not established by this transaction.")
    except InterruptedError as exc:
        result.update(status="cancelled")
        result["errors"].append(str(exc))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        result.update(status="failed")
        result["errors"].append(redact(str(exc), proxy))
    finally:
        if descriptor is not None:
            os.close(descriptor)
            if unlinked_local_path(lock) and lock.exists():
                current = lock.stat()
                if (current.st_dev, current.st_ino) == (lock_identity.st_dev, lock_identity.st_ino):
                    lock.unlink()
    return result
