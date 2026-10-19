import sys
from pathlib import Path
from types import SimpleNamespace

import installer
from tools.dependency_manager import app_info, cli, git_runtime, gui, runtime, runtime_paths


def test_source_default_uses_entry_location_not_working_directory(tmp_path, monkeypatch):
    entry_dir = tmp_path / "中文 空格 安装器"
    entry_dir.mkdir()
    elsewhere = tmp_path / "shortcut working directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(elsewhere / "python.exe"))
    assert app_info.default_installation_directory(entry_dir / "installer.py") == entry_dir
    assert app_info.default_installation_directory() == Path(installer.__file__).parent
    assert cli.discover_project() == Path(installer.__file__).parent


def test_frozen_default_uses_exe_location_not_extraction_or_cwd(tmp_path, monkeypatch):
    exe_dir = tmp_path / "中文 空格 EXE"
    exe_dir.mkdir()
    extraction = tmp_path / "_MEI_extraction"
    extraction.mkdir()
    elsewhere = tmp_path / "shortcut working directory"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extraction), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "CoatingSimInstaller.exe"))
    assert app_info.default_installation_directory(installer.__file__) == exe_dir
    assert cli.discover_project() == exe_dir
    assert runtime_paths.unlinked_local_path(exe_dir)


def test_explicit_project_overrides_entry_default(tmp_path, monkeypatch):
    selected = tmp_path / "手动 选择"
    seen = []

    class Root:
        def mainloop(self):
            pass

    monkeypatch.setattr(gui, "tk", SimpleNamespace(Tk=Root))
    monkeypatch.setattr(gui, "DependencyManagerWindow", lambda root, project: seen.append(project))
    assert installer.main(["--project", str(selected)]) == 0
    assert seen == [selected]


def test_frozen_paths_keep_resources_readable_and_extraction_unwritable(tmp_path, monkeypatch):
    extraction = tmp_path / "_MEI_fixture"
    extraction.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(extraction), raising=False)
    assert not runtime_paths.unlinked_local_path(extraction / "project/app")
    assert runtime_paths.unlinked_local_path(tmp_path / "selected-project")
    assert runtime.MANIFEST_PATH.name == "runtime_manifest.json" and runtime.MANIFEST_PATH.is_file()
    assert git_runtime.MANIFEST_PATH.name == "git_runtime_manifest.json" and git_runtime.MANIFEST_PATH.is_file()
    assert app_info.icon_path().is_file()
    assert extraction not in app_info.default_installation_directory().parents


def test_selfcheck_refuses_report_in_onefile_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    try:
        installer.write_report(tmp_path / "report.json", {})
    except ValueError:
        pass
    else:
        raise AssertionError("report must not mutate bundle extraction")
    assert not (tmp_path / "report.json").exists()


def test_external_environment_drops_only_bundled_runtime_paths(tmp_path, monkeypatch):
    import os

    from tools.dependency_manager.external_program import clean_frozen_environment
    bundle = tmp_path / "_MEI"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    env = {"PATH": os.pathsep.join((str(bundle), str(tmp_path / "normal"))), "TCL_LIBRARY": str(bundle / "_tcl_data"), "TK_LIBRARY": str(tmp_path / "system-tk"), "_PYI_APPLICATION_HOME_DIR": str(bundle)}
    result = clean_frozen_environment(env)
    assert result["PATH"] == str(tmp_path / "normal")
    assert "TCL_LIBRARY" not in result and "_PYI_APPLICATION_HOME_DIR" not in result
    assert result["TK_LIBRARY"] == str(tmp_path / "system-tk")
    assert result["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
