"""Project-local managed runtime paths; never relocate existing installations."""
import sys
from pathlib import Path


def project_root(project=None):
    if project is None:
        if getattr(sys, "frozen", False):
            raise ValueError("Select a project installation root before installing a runtime")
        project = Path(__file__).resolve().parents[2]
    return Path(project).absolute()


def unlinked_local_path(path):
    path = Path(path).absolute()
    bundle = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundle:
        extraction = Path(bundle).resolve()
        if path.resolve() == extraction or extraction in path.resolve().parents:
            return False
    if str(path).startswith(("\\\\", "//")) or path != path.resolve():
        return False
    for entry in (path, *path.parents):
        if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
            return False
        if entry.exists() and getattr(entry.lstat(), "st_file_attributes", 0) & 0x400:
            return False
    return True


def contained_runtime_path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    return (unlinked_local_path(root) and unlinked_local_path(path)
            and path != root and path.is_relative_to(root))
