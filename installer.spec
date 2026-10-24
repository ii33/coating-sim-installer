# PyInstaller onefile: all bundled data is read-only; mutable targets are external.
from pathlib import Path

root = Path(SPECPATH)
package = root / "tools/dependency_manager"
datas = [(str(path), "tools/dependency_manager") for path in package.glob("*.json")]
datas += [(str(path), "tools/dependency_manager/licenses") for path in (package / "licenses").iterdir() if path.is_file()]
datas += [(str(root / "assets/launcher.ico"), "assets"), (str(root / "LICENSE"), ".")]
a = Analysis([str(root / "installer.py")], pathex=[str(root)], binaries=[], datas=datas,
             hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog"],
             hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=["pip", "ensurepip", "venv", "pytest", "numpy", "PySide6", "matplotlib", "setuptools"],
             noarchive=False, optimize=1)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="CoatingSimInstaller-v0.1.0-alpha.12-windows-x64", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, argv_emulation=False,
          target_arch=None, codesign_identity=None, entitlements_file=None,
          icon=str(root / "assets/launcher.ico"), version=str(root / "version_info.txt"))
