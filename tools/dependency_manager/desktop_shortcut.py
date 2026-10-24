"""Idempotent native desktop entry; no unrelated same-name shortcut is replaced."""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import launchers
from .mirrors import redact
from .project_operations import owned_record, save_record
from .runtime_paths import unlinked_local_path

SCHEMA = "coating-desktop-v1"


def shortcut_failure(exc, proxy=None):
    """Failed shortcut payload for an unexpected error at a noncritical boundary.

    Callers wrap the shortcut step with this so an ordinary Exception cannot fail
    a completed install or roll back a verified update. KeyboardInterrupt and
    other BaseException are never passed here. An empty message falls back to the
    exception class name.
    """
    reason = str(exc).strip()
    message = f"{type(exc).__name__}: {reason}" if reason else type(exc).__name__
    return {"status": "failed", "errors": [redact(message, proxy)], "warnings": []}


def ensure_desktop_shortcut(root, *, desktop=None):
    result = {"status": "failed", "errors": [], "warnings": []}
    if sys.platform != "win32":
        return {**result, "status": "success", "skipped": True}
    try:
        root = Path(root).absolute()
        base = launchers.plan_launch_entries(root, name="launch-coating")
        existing_script = root / "launch-coating.cmd"
        if base["status"] != "ready":
            errors = base.get("errors", [])
            if not (len(errors) == 1 and errors[0].startswith("Existing launch entry is preserved;")
                    and existing_script.is_file() and unlinked_local_path(existing_script)
                    and existing_script.read_bytes() == base.get("script", "").encode("utf-8")):
                raise ValueError("启动入口验证失败：" + "; ".join(errors))
        git = root / ".git"
        if not git.is_dir() or not unlinked_local_path(git):
            raise ValueError("项目不是受支持的普通 Git 安装；未创建快捷方式")
        record_path = git / "coating-installer-desktop.json"
        record = owned_record(record_path, root=root, schema=SCHEMA)
        folder = Path(desktop) if desktop is not None else launchers._known_folder("desktop")
        folder = folder.absolute().resolve()
        if not folder.is_dir():
            raise ValueError("Windows 当前用户桌面目录不可用")
        primary = folder / "Coating Sim.lnk"
        alternate = folder / ("Coating Sim - " + hashlib.sha256(str(root).casefold().encode()).hexdigest()[:8] + ".lnk")
        destination = primary
        owned_existing = False
        previous_sha256 = None
        arguments = subprocess.list2cmdline(["-E", str(root / "launcher.py")])
        if record is not None:
            destination = folder / Path(record["path"]).name
            if destination not in (primary, alternate):
                raise ValueError("快捷方式记录的目标不是本安装器管理的名称")
            if destination.exists():
                if (not destination.is_file() or destination.is_symlink()
                        or destination.stat().st_size > 1024 * 1024
                        or destination.resolve() != destination):
                    raise ValueError("已有桌面快捷方式被修改；已保留，请人工检查")
                actual_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
                if (record.get("state") not in ("prepared", "created", "refreshing")
                        or record.get("python") != base["python"]
                        or record.get("arguments") != arguments
                        or record.get("working_directory") != str(root)):
                    raise ValueError("已有桌面快捷方式记录不匹配；已保留，请人工检查")
                if record.get("state") == "refreshing":
                    if actual_sha256 == record.get("sha256"):
                        # The atomic shortcut replacement completed before a prior
                        # run could finalize its receipt. Complete that receipt.
                        record = {key: value for key, value in record.items()
                                  if key != "previous_sha256"}
                        record.update({"path": str(destination), "state": "created"})
                        save_record(record_path, record, root=root, schema=SCHEMA)
                    elif actual_sha256 != record.get("previous_sha256"):
                        raise ValueError("已有桌面快捷方式被修改；已保留，请人工检查")
                elif actual_sha256 != record.get("sha256"):
                    raise ValueError("已有桌面快捷方式被修改；已保留，请人工检查")
                if actual_sha256 == record.get("sha256"):
                    # A verified receipt proves the existing link is ours. Refresh
                    # it in place only when its icon resource or bytes changed.
                    owned_existing = True
                    previous_sha256 = actual_sha256
                    refresh_icon = (record.get("icon") != base["icon"]
                                    or record.get("icon_sha256") != base.get("icon_sha256"))
                    if not refresh_icon:
                        if record["path"] != str(destination):
                            save_record(record_path, {**record, "path": str(destination)},
                                        root=root, schema=SCHEMA)
                        return {**result, "status": "success", "path": str(destination), "reused": True}
                else:
                    # A pending refresh still has the previously verified link.
                    # Rebuild it and retry the atomic replacement.
                    owned_existing = True
                    previous_sha256 = actual_sha256
        elif primary.exists():
            destination = alternate
            result["warnings"].append("保留已有同名快捷方式，使用此安装目录对应的独立名称")
        if not owned_existing and (destination.exists() or destination.is_symlink()):
            raise ValueError("快捷方式名称已被其他文件占用；未覆盖：" + str(destination))
        with tempfile.TemporaryDirectory(prefix=".coating-shortcut-", dir=folder) as temporary:
            staged = Path(temporary) / "entry.lnk"
            launchers._write_shortcut(staged, base)
            data = staged.read_bytes()
            receipt = {"path": str(destination), "sha256": hashlib.sha256(data).hexdigest(),
                       "python": base["python"], "icon": base["icon"],
                       "icon_sha256": base.get("icon_sha256"),
                       "arguments": arguments,
                       "working_directory": str(root), "state": "prepared"}
            if owned_existing:
                # Keep both hashes in the receipt across a crash around the atomic
                # replacement. A retry can distinguish the old owned link, the new
                # verified link, and a user-modified/unrelated file.
                pending = {**receipt, "state": "refreshing", "previous_sha256": previous_sha256}
                save_record(record_path, pending, root=root, schema=SCHEMA)
                if (not destination.is_file() or destination.is_symlink()
                        or hashlib.sha256(destination.read_bytes()).hexdigest() != previous_sha256):
                    save_record(record_path, record, root=root, schema=SCHEMA)
                    raise ValueError("已有桌面快捷方式在更新期间发生变化；已保留，请人工检查")
                os.replace(staged, destination)
                save_record(record_path, {**receipt, "state": "created"}, root=root, schema=SCHEMA)
            else:
                # A crash between receipt and first publication can be retried
                # without duplication; creation remains exclusive and no-clobber.
                save_record(record_path, receipt, root=root, schema=SCHEMA)
                if os.name == "nt":
                    staged.rename(destination)
                else:
                    os.link(staged, destination)
                save_record(record_path, {**receipt, "state": "created"}, root=root, schema=SCHEMA)
        return {**result, "status": "success", "path": str(destination), "reused": False}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result["errors"].append(str(exc))
        return result
