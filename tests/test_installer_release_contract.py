"""Offline release metadata, workflow and disclosure contracts; no publishing."""
import ast
import re
from pathlib import Path

from tools.dependency_manager.app_info import VERSION

ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def _calls(source, name):
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name]


def test_embedded_and_packaged_versions_match_installer_identity():
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)-alpha\.(\d+)", VERSION)
    assert match is not None
    numeric = tuple(map(int, match.groups()))
    source = _text("version_info.txt")
    fixed, = _calls(source, "FixedFileInfo")
    fields = {keyword.arg: keyword.value for keyword in fixed.keywords}
    assert ast.literal_eval(fields["filevers"]) == numeric
    assert ast.literal_eval(fields["prodvers"]) == numeric
    strings = {ast.literal_eval(node.args[0]): ast.literal_eval(node.args[1])
               for node in _calls(source, "StringStruct")}
    assert strings["FileVersion"] == strings["ProductVersion"] == VERSION
    filename = f"CoatingSimInstaller-v{VERSION}-windows-x64"
    assert strings["OriginalFilename"] == filename + ".exe"
    executable, = _calls(_text("installer.spec"), "EXE")
    assert ast.literal_eval(next(keyword.value for keyword in executable.keywords if keyword.arg == "name")) == filename
    for relative in ("README.md", "README.en.md", ".github/workflows/windows.yml"):
        assert filename + ".exe" in _text(relative)


def test_release_workflow_uses_exact_source_and_validates_before_publication():
    alpha = VERSION.rsplit(".", 1)[1]
    workflow = _text(f".github/workflows/publish-alpha{alpha}.yml")
    filename = f"CoatingSimInstaller-v{VERSION}-windows-x64.exe"
    assert f"paths: ['.github/workflows/publish-alpha{alpha}.yml']" in workflow
    assert "ref: ${{ github.sha }}" in workflow and "$target = '${{ github.sha }}'" in workflow
    assert workflow.index("python -m pytest -q") < workflow.index("python -m PyInstaller") < workflow.index("gh release create")
    for required in ("--self-check", "--smoke-gui", "--verify-venv", "Get-AuthenticodeSignature",
                     "SHA256SUMS.txt", filename, f"'v{VERSION}'", "--target $target", "--prerelease"):
        assert required in workflow
    assert "-Encoding UTF8" in workflow
    assert f"docs/releases/v{VERSION}.md" in workflow
    assert "--clobber" not in workflow and "gh release delete" not in workflow


def test_release_notes_state_source_cli_and_unverified_boundaries():
    notes = _text(f"docs/releases/v{VERSION}.md")
    for required in ("源码 CLI", "默认 GUI", "EXE 入口", "未签名", "完整原生依赖闭包",
                     "尚未完成干净 Windows", "膜系 GPU 前向计算", "SHA256SUMS.txt"):
        assert required in notes
    contract = _text("docs/isolated-runtime-roles.md")
    assert "current frozen EXE entry does not forward" in contract
    assert "A frozen installer can run the same CLI" not in contract
