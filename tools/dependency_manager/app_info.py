"""Installer identity and read-only resources, separate from mutable targets."""
import sys
from pathlib import Path

VERSION = "0.1.0-alpha.11"
INSTALLER_REPOSITORY = "ii33/coating-sim-installer"


def default_installation_directory(source_entry=None):
    """Use the real executable or source entry location, never cwd or _MEIPASS."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).absolute().parent
    entry = Path(source_entry) if source_entry is not None else Path(__file__).resolve().parents[2] / "installer.py"
    return entry.absolute().parent


def icon_path():
    return Path(__file__).resolve().parents[2] / "assets/launcher.ico"
