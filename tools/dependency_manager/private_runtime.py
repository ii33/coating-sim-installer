"""Hash-pinned project-private CPython archive deployment; no registry installer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

from .parallel_work import bounded_map
from .runtime_paths import contained_runtime_path, unlinked_local_path

MARKER = ".coating-python.json"
MAX_EXPANDED = 256 * 1024 * 1024
MAX_MEMBERS = 10000
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


def inventory_digest(files):
    return hashlib.sha256("".join(
        f"{name}\0{record['sha256']}\0{record['size']}\n"
        for name, record in sorted(files.items())
    ).encode("utf-8")).hexdigest()


def _safe_name(name):
    if (not name or "\\" in name or name.startswith("/") or "\x00" in name
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Unsafe runtime archive path")
    parts = PurePosixPath(name.rstrip("/")).parts
    if not parts or any(
        not p or p in (".", "..") or p.endswith((" ", ".")) or ":" in p or
        re.search(r'[<>"|?*]', p) or p.split(".")[0].upper() in _RESERVED
        for p in name.rstrip("/").split("/")
    ):
        raise ValueError("Unsafe runtime archive path")
    return parts


def inspect_owned(directory, manifest, cancel_event=None):
    """Validate trusted inventory and all original bytes before reusing only our runtime."""
    directory = Path(directory)
    if not unlinked_local_path(directory):
        raise ValueError("Private Python path must not use links or reparse points")
    marker = directory / MARKER
    if not marker.is_file() or marker.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Existing runtime is not owned by this installer; preserved")
    data = json.loads(marker.read_text(encoding="utf-8"))
    artifact = manifest["artifacts"]["x86_64"]
    files = data.get("files", {})
    if (data.get("schema_version") != 1 or data.get("version") != manifest["version"]
            or data.get("package_sha256") != artifact["sha256"]
            or not isinstance(files, dict) or inventory_digest(files) != artifact["inventory_sha256"]):
        raise ValueError("Private Python ownership/inventory differs from the pinned package; preserved")
    def verify_file(item):
        name, record = item
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Private Python verification cancelled")
        _safe_name(name)
        path = directory.joinpath(*PurePosixPath(name).parts)
        if (not path.is_file() or not unlinked_local_path(path)
                or path.stat().st_size != record["size"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]):
            raise ValueError("Private Python file changed; preserved: " + name)
    for _ in bounded_map(verify_file, files.items(), cancel_event or threading.Event()):
        pass
    # Ignore caches only: -I makes user/site configuration ineffective, -B is
    # used for installer probes and base-Python venv creation.
    for path in directory.rglob("*"):
        if not unlinked_local_path(path):
            raise ValueError("Private Python contains a link/reparse point")
        relative = path.relative_to(directory)
        if path.is_file() and relative.as_posix() not in files and relative.as_posix() != MARKER:
            if "__pycache__" not in relative.parts or path.suffix != ".pyc":
                raise ValueError("Unexpected private Python file; preserved: " + relative.as_posix())
    return data


def extract_package(archive, staging, manifest, cancel_event, *, workers=4, emit=None):
    """Preflight every ZIP member; extract only tools/, never execute packaging metadata."""
    emit = emit or (lambda event: None)
    started = time.perf_counter()
    artifact = manifest["artifacts"]["x86_64"]
    if hashlib.sha256(Path(archive).read_bytes()).hexdigest() != artifact["sha256"]:
        raise ValueError("Private Python archive SHA-256 mismatch")
    emit({"event": "timing", "phase": "runtime_hash", "seconds": time.perf_counter() - started,
          "text": f"runtime_hash: {time.perf_counter() - started:.2f}s"})
    started = time.perf_counter()
    staging = Path(staging)
    files, names, expanded = {}, set(), 0
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > MAX_MEMBERS:
            raise ValueError("Runtime archive has too many members")
        for entry in entries:
            _safe_name(entry.orig_filename)
            _safe_name(entry.filename)
            key = entry.filename.rstrip("/").casefold()
            if key in names:
                raise ValueError("Duplicate or Windows-alias runtime archive path")
            names.add(key)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                raise ValueError("Linked or encrypted runtime archive member")
            if entry.file_size > 64 * 1024 * 1024:
                raise ValueError("Runtime archive member exceeds size bound")
            expanded += entry.file_size
            if expanded > MAX_EXPANDED:
                raise ValueError("Runtime archive exceeds expanded size bound")
        emit({"event": "timing", "phase": "runtime_archive_preflight", "seconds": time.perf_counter() - started,
              "text": f"runtime_archive_preflight: {time.perf_counter() - started:.2f}s"})
        started = time.perf_counter()
        # Establish validated directory structure before independent file workers.
        parents = {staging.joinpath(*PurePosixPath(e.filename[len("tools/"):]).parts).parent
                   for e in entries if e.filename.startswith("tools/") and not e.is_dir()}
        for parent in sorted(parents, key=lambda value: len(value.parts)):
            if parent != staging and not contained_runtime_path(parent, staging):
                raise ValueError("Runtime directory escaped staging")
            parent.mkdir(parents=True, exist_ok=True)
        def extract_member(entry):
            if cancel_event.is_set():
                raise InterruptedError("Private Python extraction cancelled")
            if not entry.filename.startswith("tools/") or entry.is_dir():
                return None
            relative = entry.filename[len("tools/"):]
            _safe_name(relative)
            path = staging.joinpath(*PurePosixPath(relative).parts)
            if not contained_runtime_path(path, staging):
                raise ValueError("Runtime extraction escaped staging: " + str(path) + " -> " + str(path.resolve()))
            path.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with package.open(entry) as source, path.open("xb") as out:
                while chunk := source.read(65536):
                    if cancel_event.is_set():
                        raise InterruptedError("Private Python extraction cancelled")
                    size += len(chunk)
                    if size > entry.file_size:
                        raise ValueError("Runtime member size differs from metadata")
                    digest.update(chunk)
                    out.write(chunk)
            if size != entry.file_size:
                raise ValueError("Truncated runtime archive member")
            return relative, {"size": size, "sha256": digest.hexdigest()}
        for result in bounded_map(extract_member, entries, cancel_event, workers=workers):
            if result is not None:
                relative, record = result
                files[relative] = record
        emit({"event": "timing", "phase": "runtime_extract_verify", "seconds": time.perf_counter() - started,
              "workers": workers, "files": len(files),
              "text": f"runtime_extract_verify: {time.perf_counter() - started:.2f}s; {len(files)} files, up to {workers} workers"})
    if inventory_digest(files) != artifact["inventory_sha256"]:
        raise ValueError("Runtime inventory does not match the reviewed official package")
    for name in ("python.exe", "python312.dll", "Lib/venv/__init__.py",
                 "Lib/ensurepip/__init__.py", "Lib/site-packages/pip/__init__.py", "LICENSE.txt"):
        if name not in files:
            raise ValueError("Private Python package is incomplete: " + name)
    data = {"schema_version": 1, "version": manifest["version"],
            "package_sha256": artifact["sha256"], "files": files}
    (staging / MARKER).write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return data


def deploy(plan, manifest, download, probe, emit, cancel_event, verify_destination):
    directory = Path(plan["directory"])
    if plan["action"] == "reuse":
        inspect_owned(directory, manifest, cancel_event)
        return probe(directory / "python.exe")
    verify_destination(plan)
    directory.parent.mkdir(parents=True, exist_ok=True)
    verify_destination(plan)
    lock = directory.parent / "runtime-install.lock"
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    temporary = None
    try:
        verify_destination(plan)
        temporary = Path(tempfile.mkdtemp(prefix="coat-runtime-", dir=directory.parent))
        archive, staging = temporary / "python.nupkg", temporary / "python"
        staging.mkdir()
        emit({"event": "phase", "phase": "runtime_download"})
        started = time.perf_counter()
        download(plan["artifact"], archive, cancel_event, emit)
        emit({"event": "timing", "phase": "runtime_download", "seconds": time.perf_counter() - started,
              "text": f"runtime_download: {time.perf_counter() - started:.2f}s"})
        emit({"event": "phase", "phase": "runtime_extract"})
        extract_package(archive, staging, manifest, cancel_event, emit=emit)
        if cancel_event.is_set():
            raise InterruptedError("Private Python deployment cancelled")
        # Executing the verified interpreter is an after-download verification,
        # never an external prerequisite scan or installer/MSI invocation.
        identity = probe(staging / "python.exe")
        if (identity.get("python") != manifest["version"]
                or not identity.get("has_venv") or not identity.get("has_ensurepip")
                or identity.get("machine", "").lower() not in ("amd64", "x86_64")
                or Path(identity["prefix"]).resolve() != staging.resolve()):
            raise ValueError("Extracted private Python identity/venv/pip verification failed")
        verify_destination(plan)
        # Windows directory rename is atomic and refuses a racing destination.
        if os.path.lexists(directory):
            raise FileExistsError("Private Python destination appeared; preserved")
        staging.rename(directory)
        return probe(directory / "python.exe")
    finally:
        if temporary is not None:
            if not contained_runtime_path(temporary, directory.parent):
                raise ValueError("Refusing cleanup outside this operation's runtime directory")
            shutil.rmtree(temporary)
        lock.unlink(missing_ok=True)
