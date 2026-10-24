"""Default GUI coordinator: source first, then contained private runtime roles.

The frozen installer is only a front-end. Application environments are always
created by the pinned Python inside the verified application checkout. There is
no fallback to system Python, the frozen EXE, or the former sibling-base layout.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import project_deploy as source_tools
from .desktop_shortcut import ensure_desktop_shortcut, shortcut_failure
from .git_runtime import discover_git, execute_git_runtime, plan_git_runtime
from .interactive import CNB_URL, project_directory
from .launchers import execute_launch_entries, plan_launch_entries
from .mirrors import ProxyConfig, redact, resolve_mirror
from .runtime import execute_runtime, plan_runtime
from .runtime_paths import contained_runtime_path, unlinked_local_path
from .runtime_roles import (
    _authenticate_environment,
    _private_base,
    execute_runtime_roles,
    plan_runtime_roles,
)


def _checkout_state(app, git, proxy, cancel, emit):
    """Authenticate known checkout provenance/tree without importing its code."""
    if not unlinked_local_path(app) or not (app / ".git").is_dir() or not unlinked_local_path(app / ".git"):
        raise ValueError("现有源码不是受支持的真实 Git checkout；内容已保留，请选择新安装目录。")
    deadline = time.monotonic() + 300
    def run(arguments):
        return source_tools._run_git(git, arguments, cwd=app, proxy=proxy,
            cancel_event=cancel, deadline=deadline, emit=emit)
    url = run(["remote", "get-url", "origin"]).strip()
    known = {source_tools.REPOSITORY_URL, source_tools.REPOSITORY_URL[:-4], CNB_URL, CNB_URL + ".git"}
    if url not in known:
        raise ValueError("现有源码来源不是公开主项目；未执行项目或解释器，请选择新安装目录。")
    commit = run(["rev-parse", "--verify", "HEAD"]).strip()
    if not source_tools._SHA.fullmatch(commit):
        raise ValueError("源码提交记录无效；未执行项目。")
    source_tools._validate_tree(run, commit)
    flags = run(["ls-files", "-v", "-z"]).split("\0")
    if any(row and (row[0].islower() or row[0] == "S") for row in flags):
        raise ValueError("存在隐藏源码修改检查的 Git 索引标记；未执行解释器，请先保存修改并处理标记。")
    # Source identity must not enumerate tens of thousands of untracked private
    # runtime files (the main checkout need not ignore .runtime). Filters remain
    # disabled by the shared helper; user data/runtimes are never source inputs.
    if source_tools._readonly_status(run, untracked=False).strip():
        raise ValueError("检测到本地源码修改；已保留，请先保存修改或选择新安装目录。")
    for relative in ("requirements.txt", "requirements-test.txt", "launcher.py", "coat_sim/compute_runtime_manifest.py"):
        path = app / relative
        if not contained_runtime_path(path, app) or not path.is_file():
            raise ValueError("主项目源码缺少分环境运行契约，请先更新主项目或选择新安装目录：" + relative)
    return {"repository": source_tools.REPOSITORY, "provider": "cnb" if url.startswith("https://cnb.cool/") else "github",
            "url": url, "commit": commit, "source_inputs": source_tools._source_inputs(app)}


def _check_existing_app_layout(app):
    """Reject foreign/partial app environments before any interpreter probe."""
    environment = app / ".venv"
    if not environment.exists() and not environment.is_symlink():
        return
    config = environment / "pyvenv.cfg"
    if (not contained_runtime_path(environment, app) or not contained_runtime_path(config, environment)
            or not config.is_file() or config.stat().st_size > 65536):
        raise ValueError("现有 .venv 不是可核验的私有环境，已保留；请选择新安装目录。")
    values = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        key = key.strip().lower()
        if not separator or key in values:
            raise ValueError("现有 .venv 配置不明确，已保留；请选择新安装目录。")
        values[key] = value.strip()
    base = app / ".runtime/python"
    if (values.get("include-system-site-packages", "").lower() != "false"
            or any(not values.get(key) or Path(values[key]) != expected
                   for key, expected in (("home", base), ("executable", base / "python.exe")))):
        raise ValueError("现有 .venv 使用外部或旧同级 Python；未执行该解释器，环境已保留，请选择新安装目录。")


def _backend_evidence(result, selected_cuda):
    """Keep installed roles separate from actual numerical GPU probe evidence."""
    available, diagnostics = {}, {}
    for component in result.get("components", []):
        validation = component.get("result", {}).get("compute_validation") or {}
        for backend, value in validation.get("backend_usable", {}).items():
            if backend in ("directml", "cuda12", "cuda13"):
                available[backend] = value is True
        diagnostics.update(validation.get("probe_diagnostics", {}))
    preferred = next((name for name in (selected_cuda, "directml") if name and available.get(name) is True), None)
    return available, diagnostics, preferred


def run_default_installation(project, *, output=print, prompt_fn=None, phase_fn=None, plan_fn=None,
                             cancel_event=None, mirror=None, backend=None, event_fn=None,
                             source="cnb", development=False):
    """Install all planned roles or report failure/cancellation, preserving files.

    Called off the GUI thread with immutable user choices. Only confirmed,
    project-contained source/runtime/package/launcher actions are performed.
    The caller owns the project-operation lock, as for other GUI operations.
    """
    cancel = cancel_event or threading.Event()
    proxy = ProxyConfig("env")
    root = Path(project).expanduser().absolute()
    requested = backend or "auto"
    profile = "development" if development else "runtime"
    def say(text):
        output(redact(str(text), proxy))
    def check_cancel():
        if cancel.is_set():
            raise InterruptedError("安装已取消；源码、私有运行时和部分角色环境已保留。")
    def phase(name):
        check_cancel()
        if phase_fn:
            phase_fn(name)
    def review(value):
        if plan_fn:
            plan_fn(value)
        for message in value.get("errors", []):
            say("安装失败：" + str(message))
    def emit(value):
        # Never raise out of a live subprocess emitter: the owning runner must
        # observe cancellation and reap its process tree before returning.
        if event_fn:
            event_fn(value)
        if value.get("text"):
            say(value["text"])
    def approve(key):
        check_cancel()
        if prompt_fn is None or prompt_fn(key) != "y":
            raise InterruptedError("未确认安装计划；已有内容保留。")
        check_cancel()
    def require(result, accepted=("ready",)):
        review(result)
        if cancel.is_set() or result.get("status") == "cancelled":
            check_cancel() if cancel.is_set() else None
            raise InterruptedError("安装已取消；已准备内容保留。")
        if result.get("status") not in accepted:
            details = list(result.get("errors", []))
            for component in result.get("components", []):
                details.extend(component["id"] + ": " + str(error)
                               for error in component.get("result", {}).get("errors", []))
            raise ValueError("；".join(details) or "安装步骤未完成；请检查日志后重试。")
        check_cancel()
        return result
    try:
        if sys.platform != "win32":
            raise ValueError("默认私有分环境安装目前仅支持 Windows x64；不会复用外部 Python。")
        if requested not in ("auto", "cpu", "directml", "cuda12", "cuda13"):
            raise ValueError("不支持的计算设备选项。")
        source_tools.repository_source(source, CNB_URL if source == "cnb" else None)
        app = project_directory(root)
        mirror = resolve_mirror(root, mirror)
        phase("source")
        git = discover_git(project=root)
        if not git:
            plan = require(plan_git_runtime(proxy=proxy, project=root, runtime_mirror=mirror != "pypi"))
            approve("git")
            git = require(execute_git_runtime(plan, True, emit, cancel, proxy), ("success", "completed"))["git"]
        if not app.exists():
            prepared = require(source_tools.prepare_project_source(str(app), proxy=proxy,
                emit=emit, cancel_event=cancel, source=source,
                repository_url=CNB_URL if source == "cnb" else None, git_path=git))
            expected_commit = prepared["source"]["commit"]
        else:
            expected_commit = None
        checked_source = _checkout_state(app, git, proxy, cancel, emit)
        if expected_commit and checked_source["commit"] != expected_commit:
            raise ValueError("准备后的源码提交已变化；未执行依赖安装。")
        _check_existing_app_layout(app)
        phase("python")
        private_plan = require(plan_runtime(project=app, proxy=proxy, backend="cpu", runtime_mirror=mirror != "pypi"))
        if private_plan.get("action") != "reuse":
            approve("python")
        require(execute_runtime(private_plan, True, emit, cancel, proxy), ("success",))
        # Base authentication happens before role planning can probe any venv.
        _private_base(app)
        if _checkout_state(app, git, proxy, cancel, emit) != checked_source:
            raise ValueError("源码或依赖声明在准备 Python 时发生变化；未执行依赖安装。")
        phase("recommend")
        cuda = "cpu" if requested in ("cpu", "directml") else requested
        roles = require(plan_runtime_roles(app, cuda_backend=cuda, profile=profile, mirror=mirror, proxy=proxy))
        selected = roles.get("selected_cuda")
        planned = "CPU/应用 + DirectML + 外观" + (" + " + selected if selected else "")
        route = {"operation": "compute_route", "requested_backend": requested, "planned_backend": planned,
                 "usable_backend": "未确认", "reason": "已分离安装角色；包存在不等于 GPU 就绪。",
                 "summary": "分环境计划：" + planned + "；CUDA 不排除 DirectML，未宣称设备可用。"}
        review(route)
        review({"schema_version": 1, "operation": "installation_review", "source": checked_source,
                "installation_plan": roles, "launch_entry_intent": {"path": str(app / "launch-coating.cmd"),
                    "overwrite": False, "desktop_shortcuts": True}})
        approve("install")
        phase("dependencies")
        installed = require(execute_runtime_roles(roles, True, emit, cancel, proxy), ("success",))
        phase("verify")
        _private_base(app)
        _authenticate_environment(app, app / ".venv")
        if _checkout_state(app, git, proxy, cancel, emit) != checked_source:
            raise ValueError("角色安装期间源码发生变化；不宣称安装完成。")
        available, diagnostics, preferred = _backend_evidence(installed, selected)
        route.update(backend_usable=available, probe_diagnostics=diagnostics,
                     usable_backend=preferred or "未确认 GPU；应用/CPU 依赖已核验",
                     reason="角色依赖已分别核验；GPU 仅按独立数值探测结果报告，不静默改写设备请求。")
        route["summary"] = "角色依赖已准备；GPU 数值探测：" + json.dumps(available, ensure_ascii=False)
        review(route)
        if requested in ("cuda12", "cuda13", "directml") and available.get(requested) is not True:
            raise ValueError("手选 " + requested + " 的 GPU 数值验证未通过；各角色环境已保留，可检查日志后重试。")
        phase("launch")
        launch = plan_launch_entries(app, name="launch-coating")
        script = app / "launch-coating.cmd"
        if launch.get("status") == "ready":
            require(execute_launch_entries(launch, confirmed=True), ("success",))
        elif not (len(launch.get("errors", [])) == 1 and
                  launch["errors"][0].startswith("Existing launch entry is preserved;") and
                  isinstance(launch.get("script"), str) and unlinked_local_path(script) and script.is_file() and
                  script.read_bytes() == launch["script"].encode("utf-8")):
            require(launch)
        check_cancel()
        try:
            shortcut = ensure_desktop_shortcut(app)
        except Exception as exc:
            shortcut = shortcut_failure(exc, proxy)
        check_cancel()
        if shortcut.get("status") != "success":
            say("安装已完成，但桌面快捷方式待重试：" + "；".join(shortcut.get("errors", [])))
        say("安装完成：" + str(app) + "；应用/CPU、DirectML、外观及计划中的 CUDA 角色已分别准备；未宣称完整原生依赖闭包。")
        if event_fn:
            event_fn({"event": "completion", "operation": "install", "target": str(app),
                      "compute_route": route, "runtime_roles": installed.get("roles", roles.get("roles", {})),
                      "manifest": installed.get("manifest"), "shortcut": shortcut})
        return 0
    except (InterruptedError, KeyboardInterrupt, EOFError) as exc:
        cancel.set()
        say("已取消：" + str(exc) + " 重新安装会生成新计划，不删除现有文件。")
        return 130
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        say("安装失败：" + str(exc))
        return 2
