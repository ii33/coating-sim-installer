"""Installer identity and read-only resources, separate from mutable targets."""
import os
import sys
from pathlib import Path

VERSION = "0.1.0-alpha.8"
INSTALLER_REPOSITORY = "ii33/coating-sim-installer"


def default_installation_directory():
    if sys.platform == "win32":
        location = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        if not location.is_absolute():
            location = Path.home() / "AppData/Local"
        return location / "CoatingSim"
    return Path.home() / "CoatingSim"


def icon_path():
    return Path(__file__).resolve().parents[2] / "assets/launcher.ico"
