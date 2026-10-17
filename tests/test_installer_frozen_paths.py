import sys

import installer
from tools.dependency_manager import app_info, git_runtime, runtime, runtime_paths


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
