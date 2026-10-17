"""Offline private runtime and mirror regressions; no external Python or installer."""
import hashlib
import io
import threading
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.dependency_manager import private_runtime as private
from tools.dependency_manager import runtime
from tools.dependency_manager import runtime_download as transfer
from tools.dependency_manager.mirrors import ProxyConfig, resolve_mirror

PROXY = ProxyConfig("none")


def artifact(payload=b"official bytes", mirror=True):
    url = "https://api.nuget.org/v3-flatcontainer/python/3.12.10/python.3.12.10.nupkg"
    value = {"url": url, "sha256": hashlib.sha256(payload).hexdigest()}
    if mirror:
        value["mirrors"] = [{"id": "huawei", "url": runtime._mirror_url(url)}]
    return value


def test_slow_progress_beyond_180_seconds_still_verifies(tmp_path, monkeypatch):
    clock = [0]
    payload = b"x" * (3 * 65536)
    class Slow(io.BytesIO):
        def read1(self, count):
            clock[0] += 70
            return super().read1(count)
    monkeypatch.setattr(transfer.time, "monotonic", lambda: clock[0])
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.return_value = Slow(payload)
        assert runtime.download_runtime(artifact(payload), tmp_path/"result", PROXY, threading.Event(), lambda e: None) == len(payload)
    assert clock[0] > 180 and (tmp_path/"result").read_bytes() == payload


@pytest.mark.parametrize("failure", [TimeoutError("stalled"), OSError("connection lost")])
def test_stalled_source_retries_then_falls_back_without_partial_file(tmp_path, failure):
    events = []
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.side_effect = [failure, failure, io.BytesIO(b"official bytes")]
        runtime.download_runtime(artifact(), tmp_path/"result", PROXY, threading.Event(), events.append)
        assert builder.return_value.open.call_count == 3
    assert any("falling back" in e.get("text","") for e in events)
    assert not list(tmp_path.glob("*.part")) and (tmp_path/"result").read_bytes() == b"official bytes"


def test_bad_mirror_hash_never_executes_and_official_is_verified(tmp_path):
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.side_effect = [io.BytesIO(b"tampered"), io.BytesIO(b"official bytes")]
        runtime.download_runtime(artifact(), tmp_path/"result", PROXY, threading.Event(), lambda e: None)
        assert builder.return_value.open.call_count == 2
    assert (tmp_path/"result").read_bytes() == b"official bytes"


def test_all_sources_exhausted_is_bounded(tmp_path):
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.side_effect = TimeoutError("stalled")
        with pytest.raises(TimeoutError):
            runtime.download_runtime(artifact(), tmp_path/"result", PROXY, threading.Event(), lambda e: None)
        assert builder.return_value.open.call_count == 4
    assert list(tmp_path.iterdir()) == []


def test_cancel_during_read_does_not_retry_or_publish(tmp_path):
    cancel = threading.Event()
    class Cancel(io.BytesIO):
        def read1(self, count):
            cancel.set()
            return super().read1(count)
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.return_value = Cancel(b"official bytes")
        with pytest.raises(InterruptedError):
            runtime.download_runtime(artifact(), tmp_path/"result", PROXY, cancel, lambda e: None)
        assert builder.return_value.open.call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_cancel_during_retry_wait_is_prompt_and_does_not_fallback(tmp_path):
    cancel = threading.Event()
    def emit(e):
        if e.get("retry"):
            cancel.set()
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.side_effect = TimeoutError("stalled")
        with pytest.raises(InterruptedError):
            runtime.download_runtime(artifact(), tmp_path/"result", PROXY, cancel, emit)
        assert builder.return_value.open.call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_atomic_publication_and_existing_destination_preserved(tmp_path):
    dest = tmp_path/"result"
    states = []
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.return_value = io.BytesIO(b"official bytes")
        runtime.download_runtime(artifact(mirror=False), dest, PROXY, threading.Event(),
                                 lambda e: states.append(dest.exists()))
    assert not any(states)
    with pytest.raises(FileExistsError):
        runtime.download_runtime(artifact(), dest, PROXY, threading.Event(), lambda e: None)
    assert dest.read_bytes() == b"official bytes"


@pytest.mark.parametrize("kind", ["header", "stream", "hash", "redirect"])
def test_invalid_official_bytes_or_redirect_do_not_publish(tmp_path, kind):
    body = io.BytesIO(b"too big")
    if kind == "header":
        body.headers = {"Content-Length":"999999999"}
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.return_value = body
        if kind == "redirect":
            builder.return_value.open.side_effect = ValueError("outside official HTTPS source")
        with pytest.raises(ValueError):
            runtime.download_runtime(artifact(mirror=False), tmp_path/"result", PROXY,
                                     threading.Event(), lambda e: None, max_bytes=2 if kind=="stream" else 100)
    assert list(tmp_path.iterdir()) == []


def test_unknown_mirror_is_rejected_before_network(tmp_path):
    a = artifact()
    a["mirrors"][0]["url"] = "https://evil.example/python.nupkg"
    with patch.object(runtime, "build_opener") as builder, pytest.raises(ValueError, match="Unapproved"):
        runtime.download_runtime(a, tmp_path/"result", PROXY, threading.Event(), lambda e: None)
    builder.assert_not_called()


def test_idle_socket_timeout_is_distinct_from_connect(tmp_path):
    settings = []
    body = io.BytesIO(b"official bytes")
    body.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=settings.append)))
    with patch.object(runtime, "build_opener") as builder:
        builder.return_value.open.return_value = body
        runtime.download_runtime(artifact(mirror=False), tmp_path/"result", PROXY, threading.Event(), lambda e: None)
        assert builder.return_value.open.call_args.kwargs["timeout"] == 10
    assert settings == [15]


@pytest.fixture
def package(tmp_path):
    entries = {n:b"fixture" for n in ("python.exe","python312.dll","Lib/venv/__init__.py",
                "Lib/ensurepip/__init__.py","Lib/site-packages/pip/__init__.py","LICENSE.txt")}
    archive = tmp_path/"python.nupkg"
    with zipfile.ZipFile(archive,"w") as z:
        for name, payload in entries.items():
            z.writestr("tools/"+name,payload)
    files={n:{"size":len(b),"sha256":hashlib.sha256(b).hexdigest()} for n,b in entries.items()}
    m={"version":"3.12.10","artifacts":{"x86_64":{"sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
        "inventory_sha256":private.inventory_digest(files)}}}
    return archive,m


def test_archive_owned_reentry_and_modified_file_preserved(tmp_path, package):
    archive,m=package
    stage=tmp_path/"stage"
    stage.mkdir()
    private.extract_package(archive,stage,m,threading.Event())
    assert private.inspect_owned(stage,m)["version"]=="3.12.10"
    (stage/"python.exe").write_bytes(b"changed")
    with pytest.raises(ValueError,match="changed"):
        private.inspect_owned(stage,m)
    assert (stage/"python.exe").read_bytes()==b"changed"


@pytest.mark.parametrize("name", ["../escape", "tools/../../escape", "tools/C:evil", "tools/a\\b",
                                  "tools/NUL.txt", "tools/file.", "/absolute", "tools/a//b"])
def test_archive_paths_rejected_before_any_extraction(tmp_path, package, name):
    archive,m=package
    with zipfile.ZipFile(archive,"a") as z:
        info=zipfile.ZipInfo("safe")
        info.filename=name
        info.orig_filename=name
        z.writestr(info,b"unsafe")
    m["artifacts"]["x86_64"]["sha256"]=hashlib.sha256(archive.read_bytes()).hexdigest()
    stage=tmp_path/"stage"
    stage.mkdir()
    with pytest.raises(ValueError,match="Unsafe"):
        private.extract_package(archive,stage,m,threading.Event())
    assert list(stage.iterdir())==[]


@pytest.mark.parametrize("kind", ["duplicate", "symlink"])
def test_archive_aliases_and_links_rejected(tmp_path, package, kind):
    archive,m=package
    with zipfile.ZipFile(archive,"a") as z:
        info=zipfile.ZipInfo("tools/PYTHON.exe" if kind=="duplicate" else "tools/link")
        if kind=="symlink":
            info.external_attr=(0o120777 << 16)
        z.writestr(info,b"unsafe")
    m["artifacts"]["x86_64"]["sha256"]=hashlib.sha256(archive.read_bytes()).hexdigest()
    stage=tmp_path/"stage"
    stage.mkdir()
    with pytest.raises(ValueError):
        private.extract_package(archive,stage,m,threading.Event())
    assert list(stage.iterdir())==[]


def test_unknown_runtime_blocks_without_system_scan(tmp_path, monkeypatch):
    (tmp_path/".runtime/python").mkdir(parents=True)
    (tmp_path/".runtime/python/keep").write_text("preserve")
    monkeypatch.setattr(runtime,"_registered_python_paths",lambda *a:pytest.fail("no registry scan"))
    plan=runtime.plan_runtime(project=tmp_path,platform_name="win32",machine="AMD64")
    assert plan["status"]=="blocked" and "owned" in plan["errors"][0]
    assert (tmp_path/".runtime/python/keep").read_text()=="preserve"


def test_first_default_and_explicit_official_selection(tmp_path):
    assert resolve_mirror(tmp_path)=="tuna"
    assert resolve_mirror(tmp_path,"pypi")=="pypi"
    assert resolve_mirror(tmp_path,"ustc")=="ustc"


def test_saved_reviewed_mirror_survives_new_default(tmp_path, monkeypatch):
    from tools.dependency_manager import project_deploy
    state=tmp_path/"app/.git/dependency-manager-deploy"
    state.mkdir(parents=True)
    (state/"plan.json").write_text("{}")
    monkeypatch.setattr(project_deploy,"read_project_deployment_plan",
                        lambda p:{"dependency_plan":{"mirror":"pypi"}})
    assert resolve_mirror(tmp_path)=="pypi"
    assert resolve_mirror(tmp_path,"tuna")=="tuna"
