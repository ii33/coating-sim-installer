import base64
import threading
from urllib.error import HTTPError, URLError

import pytest

from tools.dependency_manager import updates


def release(tag, **extra):
    return {"tag_name": tag, "html_url": updates.validated_release_page(tag), **extra}


@pytest.mark.parametrize("older,newer", [("0.1.0-alpha.2", "0.1.0-alpha.10"), ("0.1.0-rc.1", "0.1.0"), ("0.9.9", "0.10.0"), ("0.1.0-alpha.1", "0.1.0-beta.1")])
def test_semantic_order_instead_of_lexical_order(older, newer):
    assert updates.semver_key(older) < updates.semver_key(newer)


@pytest.mark.parametrize("bad", ["01.2.3", "1.2", "1.2.3-alpha.01", "1.2.3-.", "1.2.3/evil"])
def test_invalid_versions_rejected(bad):
    with pytest.raises(ValueError):
        updates.semver_key(bad)


def test_channels_and_unordered_api_results():
    rows = [release("v0.2.0-alpha.10", prerelease=True), release("v0.1.2"), release("v9.0.0", draft=True), release("v0.1.10")]
    def fetch(*args):
        return rows
    stable = updates.check_installer_updates("stable", current="0.1.0", fetch=fetch)
    preview = updates.check_installer_updates("preview", current="0.1.0", fetch=fetch)
    assert stable["latest_version"] == "v0.1.10"
    assert preview["latest_version"] == "v0.2.0-alpha.10"
    assert preview["download_page"].startswith("https://github.com/ii33/coating-sim-installer/releases/tag/")


def test_no_downgrade_or_automatic_download():
    result = updates.check_installer_updates(current="0.2.0", fetch=lambda *args: [release("v0.1.0")])
    assert result["update_available"] is False and result["download_page"] is None


def test_release_page_rejects_redirect_or_another_repository():
    with pytest.raises(ValueError):
        updates.validated_release_page("v1.0.0", "https://evil.example/installer.exe")
    row = release("v1.0.0")
    row["html_url"] = "https://github.com/another/project/releases/tag/v1.0.0"
    assert updates.check_installer_updates(fetch=lambda *args: [row])["status"] == "no_releases"


@pytest.mark.parametrize("error,status", [(HTTPError("https://api.github.com", 403, "limit", {}, None), "rate_limited"), (HTTPError("https://api.github.com", 429, "limit", {}, None), "rate_limited"), (URLError("offline"), "unavailable"), (TimeoutError("timed out"), "timed_out"), (HTTPError("https://api.github.com", 404, "no release", {}, None), "unavailable")])
def test_offline_timeout_and_rate_limit(error, status):
    def fetch(*args):
        raise error
    result = updates.check_installer_updates(fetch=fetch)
    assert result["status"] == status
    assert result["update_available"] is None and result["download_page"] is None


def make_project(tmp_path):
    root = tmp_path / "app"
    (root / "coat_sim").mkdir(parents=True)
    (root / "coat_sim/version.txt").write_text("dev.20261016.09")
    (root / "launcher.py").write_text("# fixture")
    (root / "requirements.txt").write_text("")
    (root / ".git").mkdir()
    return root


def test_dirty_project_check_preserves_bytes_and_compares_date_numbers(tmp_path, monkeypatch):
    root = make_project(tmp_path)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    local, remote = "1" * 40, "2" * 40
    commands = []
    def run(git, args, **kwargs):
        commands.append(args)
        if args[:1] == ["rev-parse"]:
            return local
        if "status" in args:
            return " M launcher.py"
        return ""
    monkeypatch.setattr(updates.deploy, "_run_git", run)
    def fetch(path, *args):
        if "/commits/" in path:
            return {"sha": remote}
        assert path.endswith("ref=" + remote)
        return {"encoding": "base64", "size": 17, "content": base64.b64encode(b"dev.20261016.10").decode()}
    result = updates.check_project_updates(tmp_path, "github", fetch=fetch, git_path="fixture-git")
    assert result["update_available"] is True and result["dirty"] is True
    assert result["can_update_automatically"] is False
    assert all("fetch" not in command and "checkout" not in command for command in commands)
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_missing_project_does_not_create_target_or_request_network(tmp_path):
    destination = tmp_path / "new-target"
    result = updates.check_project_updates(destination, fetch=lambda *a: pytest.fail("no remote check for absent project"))
    assert result["status"] == "not_installed"
    assert not destination.exists()


def test_cnb_version_lookup_uses_exact_cnb_commit(tmp_path, monkeypatch):
    make_project(tmp_path)
    remote = "3" * 40
    monkeypatch.setattr(updates.deploy, "_run_git", lambda git, args, **kwargs: updates.CNB_URL if "--get-url" in args else ("1" * 40 if "rev-parse" in args else ""))
    monkeypatch.setattr(updates.deploy, "_resolve_commit", lambda *a: {"commit": remote})
    seen = []
    def fetch(path, *args):
        seen.append(path)
        raise HTTPError("https://api.github.com", 404, "mirror commit unavailable", {}, None)
    result = updates.check_project_updates(tmp_path, "cnb", fetch=fetch, git_path="fixture")
    assert result["latest_commit"] == remote
    assert result["latest_version"] is None and result["status"] == "unavailable"
    assert seen == ["/repos/ii33/coating-sim/contents/coat_sim/version.txt?ref=" + remote]


def test_cancelled_request_does_not_open_connection(monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(updates, "build_opener", lambda *a: pytest.fail("cancelled"))
    with pytest.raises(InterruptedError):
        updates.api_json("/repos/ii33/coating-sim-installer/releases", updates.ProxyConfig(), event)
