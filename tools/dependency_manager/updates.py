"""Bounded read-only update checks. No fetch, checkout, download or replacement."""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import project_deploy as deploy
from .app_info import INSTALLER_REPOSITORY, VERSION
from .git_runtime import discover_git
from .interactive import CNB_URL, project_directory
from .mirrors import ProxyConfig, _effective_proxy_map, _UniformProxyHandler, redact

MAX_JSON_BYTES = 1024 * 1024
_SEMVER = re.compile(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?")


def semver_key(value):
    match = _SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Invalid semantic version")
    identifiers = match[4].split(".") if match[4] else []
    if any(not part or (part.isdigit() and len(part) > 1 and part.startswith("0")) for part in identifiers):
        raise ValueError("Invalid semantic prerelease")
    if match[5] and any(not part for part in match[5].split(".")):
        raise ValueError("Invalid semantic build metadata")
    return (*map(int, match.group(1, 2, 3)), not bool(identifiers),
            tuple((0, int(part)) if part.isdigit() else (1, part) for part in identifiers))


def version_is_prerelease(value):
    return not semver_key(value)[3]


def validated_release_page(tag, value=None):
    semver_key(tag)
    expected = "https://github.com/" + INSTALLER_REPOSITORY + "/releases/tag/" + quote(tag, safe=".-+")
    if value is not None and value != expected:
        raise ValueError("Release page does not belong to the selected installer release")
    return expected


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def api_json(path, proxy, cancel_event):
    if cancel_event.is_set():
        raise InterruptedError("Update check cancelled")
    if not path.startswith("/repos/") or ".." in path:
        raise ValueError("Invalid GitHub API path")
    request = Request("https://api.github.com" + path,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "CoatingSimInstaller/" + VERSION,
                               "X-GitHub-Api-Version": "2022-11-28"})
    opener = build_opener(_UniformProxyHandler(_effective_proxy_map(proxy)), _NoRedirect())
    deadline = time.monotonic() + 8
    content = bytearray()
    with opener.open(request, timeout=5) as response:
        read = getattr(response, "read1", response.read)
        while True:
            if cancel_event.is_set():
                raise InterruptedError("Update check cancelled")
            if time.monotonic() > deadline:
                raise TimeoutError("Update metadata exceeded the overall time limit")
            chunk = read(min(65536, MAX_JSON_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_JSON_BYTES:
                raise ValueError("Update metadata exceeds the size limit")
    return json.loads(content)


def _error_result(result, exc, proxy):
    if isinstance(exc, InterruptedError):
        state, message = "cancelled", "检查已取消。"
    elif isinstance(exc, HTTPError) and exc.code in (403, 429):
        state, message = "rate_limited", "GitHub API 请求受限；请稍后重试。"
    elif isinstance(exc, (TimeoutError,)) or (isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError)):
        state, message = "timed_out", "检查超时；本地文件未更改。"
    elif isinstance(exc, HTTPError) and exc.code == 404:
        state, message = "unavailable", "尚无发布或该公共版本暂不可用。"
    else:
        state, message = "unavailable", "无法检查更新；可能离线或来源不可用。"
    result.update(status=state, update_available=None, errors=[message, redact(str(exc), proxy)])
    return result


def check_installer_updates(channel="preview", *, current=VERSION, proxy=None, cancel_event=None, fetch=None):
    if channel not in ("stable", "preview"):
        raise ValueError("Choose stable or preview channel")
    local = semver_key(current)
    proxy = proxy or ProxyConfig()
    cancel_event = cancel_event or threading.Event()
    fetch = fetch or api_json
    result = {"component": "installer", "local_version": current, "latest_version": None,
              "source": "https://github.com/" + INSTALLER_REPOSITORY, "channel": channel,
              "status": "no_releases", "update_available": False, "download_page": None,
              "action": "仅检查；打开下载页面需另行点击，不会自动下载或覆盖当前 EXE。", "errors": []}
    try:
        releases = []
        for page in (1, 2):
            rows = fetch(f"/repos/{INSTALLER_REPOSITORY}/releases?per_page=50&page={page}", proxy, cancel_event)
            if not isinstance(rows, list):
                raise ValueError("Invalid release metadata")
            releases.extend(rows)
            if len(rows) < 50:
                break
        choices = []
        for row in releases:
            if not isinstance(row, dict) or row.get("draft"):
                continue
            tag = row.get("tag_name", "")
            try:
                key = semver_key(tag)
                if channel == "stable" and (row.get("prerelease") or version_is_prerelease(tag)):
                    continue
                page = validated_release_page(tag, row.get("html_url"))
                choices.append((key, tag, page, row))
            except (TypeError, ValueError):
                continue
        if choices:
            latest, tag, page, release = max(choices, key=lambda item: item[0])
            newer = latest > local
            result.update(status="update_available" if newer else "up_to_date", latest_version=tag,
                          update_available=newer, download_page=page if newer else None,
                          published_at=release.get("published_at"))
            result["asset_sha256"] = [asset["digest"][7:] for asset in release.get("assets", [])
                                      if isinstance(asset, dict) and str(asset.get("name", "")).endswith("-windows-x64.exe")
                                      and isinstance(asset.get("digest"), str)
                                      and re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"])]
        return result
    except (OSError, ValueError, TypeError, InterruptedError) as exc:
        return _error_result(result, exc, proxy)


def _project_version(value):
    match = re.fullmatch(r"dev\.(\d{8})\.(\d+)", value)
    if match:
        datetime.strptime(match[1], "%Y%m%d")
        return "preview", (int(match[1]), int(match[2]))
    return "semver", semver_key(value)


def check_project_updates(container, provider="cnb", *, proxy=None, cancel_event=None, fetch=None, git_path=None):
    proxy = proxy or ProxyConfig()
    cancel_event = cancel_event or threading.Event()
    fetch = fetch or api_json
    result = {"component": "project", "status": "not_installed", "local_version": None,
              "latest_version": None, "local_commit": None, "latest_commit": None, "dirty": None,
              "update_available": None, "can_update_automatically": False, "errors": [],
              "action": "项目更新仅检查；不会 fetch、checkout 或覆盖本地修改。"}
    try:
        root = project_directory(container)
        source = deploy.repository_source(provider, CNB_URL if provider == "cnb" else None)
        result.update(directory=str(root), source=source)
        if not root.is_dir() or not (root / "launcher.py").is_file():
            return result
        local_file = root / "coat_sim/version.txt"
        if local_file.stat().st_size > 128:
            raise ValueError("Local version file exceeds the size limit")
        result["local_version"] = local_file.read_text(encoding="utf-8").strip()
        git = git_path or discover_git(project=container)
        deadline = time.monotonic() + 12
        def run(args):
            return deploy._run_git(git, args, cwd=root, proxy=proxy, cancel_event=cancel_event,
                                   deadline=deadline, emit=lambda event: None)
        if git and (root / ".git").exists():
            commit = run(["rev-parse", "--verify", "HEAD"]).strip()
            if not deploy._SHA.fullmatch(commit):
                raise ValueError("Invalid local commit")
            result["local_commit"] = commit
            result["dirty"] = bool(deploy._readonly_status(run, untracked=False))
            result["dirty_scope"] = "tracked parent worktree excluding submodules; private runtimes/user files excluded; filters disabled conservatively"
        if provider == "github":
            remote = fetch("/repos/ii33/coating-sim/commits/main", proxy, cancel_event)
            latest = remote["sha"]
        else:
            if not git:
                raise ValueError("Git is required to check the selected CNB source")
            if run(["ls-remote", "--get-url", source["url"]]).strip() != source["url"]:
                raise ValueError("Git URL rewriting changed the selected source")
            latest = deploy._resolve_commit(run, "main", source["url"])["commit"]
        if not deploy._SHA.fullmatch(latest):
            raise ValueError("Invalid remote commit")
        result["latest_commit"] = latest
        # A CNB commit is read only from the public GitHub copy at the identical
        # SHA; never substitute GitHub main for an unavailable CNB version.
        version = fetch("/repos/ii33/coating-sim/contents/coat_sim/version.txt?ref=" + latest, proxy, cancel_event)
        if version.get("encoding") != "base64" or version.get("size", 129) > 128:
            raise ValueError("Invalid remote version document")
        result["latest_version"] = base64.b64decode(version["content"], validate=False).decode("utf-8").strip()
        result["version_evidence"] = "Public GitHub version file at the exact selected-source commit SHA"
        local_kind, local_key = _project_version(result["local_version"])
        remote_kind, remote_key = _project_version(result["latest_version"])
        if result["local_commit"] == latest:
            result.update(status="up_to_date", update_available=False)
        elif local_kind == remote_kind:
            newer = remote_key > local_key
            result.update(status="update_available" if newer else "different_or_current", update_available=newer)
        else:
            result["status"] = "incomparable_versions"
        if result["dirty"]:
            result["action"] = "检测到本地修改；保留现状，不会覆盖。请先备份或处理修改，再自行更新项目。"
        return result
    except (OSError, ValueError, TypeError, KeyError, InterruptedError) as exc:
        return _error_result(result, exc, proxy)
