"""Exclusive project operations and atomic installer-owned JSON receipts."""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .runtime_paths import unlinked_local_path


@contextmanager
def project_operation(container):
    from .interactive import project_directory
    app = project_directory(container)
    parent = app.parent
    if not unlinked_local_path(parent):
        raise ValueError("项目目录不安全；未修改文件")
    parent.mkdir(parents=True, exist_ok=True)
    lock = parent / ("." + app.name + ".coating-installer.lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError("此项目已有安装或更新任务；请等待其结束。遗留锁不会自动删除：" + str(lock)) from None
    identity = os.fstat(fd)
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "project": str(app)}).encode())
        yield
    finally:
        os.close(fd)
        if lock.exists():
            current = lock.stat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                lock.unlink()


def owned_record(path, *, root, schema):
    path = Path(path)
    if not unlinked_local_path(path):
        raise ValueError("Installer receipt path uses a link/reparse point")
    if not path.exists():
        return None
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Installer receipt exceeds size bound; preserved")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != schema or not isinstance(data.get("root"), str) or Path(data["root"]) != Path(root):
        raise ValueError("Unknown installer receipt; preserved: " + str(path))
    return data


def save_record(path, data, *, root, schema):
    path = Path(path)
    previous = owned_record(path, root=root, schema=schema)
    old_bytes = path.read_bytes() if previous is not None else None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".coating-receipt-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({**data, "schema": schema, "root": str(root)}, output, ensure_ascii=False, indent=2)
        if old_bytes is None:
            if os.name == "nt":
                temp.rename(path)
            else:
                os.link(temp, path)
        else:
            if not unlinked_local_path(path) or path.read_bytes() != old_bytes:
                raise ValueError("Installer receipt changed concurrently; preserved")
            os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
