"""Standard-library terminal installer, reusing the checked project transactions."""

from __future__ import annotations

import json
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .compute_recommendation import END_USER_GROUPS, recommend_environment
from .desktop_shortcut import ensure_desktop_shortcut
from .git_runtime import discover_git, execute_git_runtime, plan_git_runtime
from .launchers import execute_launch_entries, plan_launch_entries
from .manager import execute_plan, inspect_environment, plan_environment
from .mirrors import MIRRORS, ProxyConfig, redact, resolve_mirror
from .parallel_work import LinkedCancellation
from .project_deploy import execute_project_deployment, plan_project_deployment
from .runtime import default_runtime_directory, execute_runtime, plan_runtime
from .runtime_paths import unlinked_local_path

CNB_URL = "https://cnb.cool/nonereason/coating-sim"


def project_directory(root):
    """An install container and its app checkout have separate ownership."""
    root = Path(root).absolute()

    def is_project(path):
        return (
            (path / "requirements.txt").is_file()
            and (path / "coat_sim").is_dir()
            and (path / "launcher.py").is_file()
        )

    app = root if is_project(root) else root / "app"
    if not unlinked_local_path(root) or not unlinked_local_path(app):
        raise ValueError("安装目录必须是本地真实目录，不能经过链接或重解析点")
    if app.exists() and not is_project(app):
        raise ValueError("app 目录已有未知内容；请选择其他安装目录，不会覆盖")
    return app


def run_interactive(
    project=None, *, input_fn=input, output=print, cancel_event=None,
    prompt_fn=None, phase_fn=None, plan_fn=None, mirror=None, backend=None, pipeline=False, event_fn=None,
):
    backend = None if backend == "auto" else backend
    requested_backend = backend or "auto"
    compute_route = {}
    cancel = cancel_event or threading.Event()
    proxy = ProxyConfig("env")
    pipeline_pool = None
    git_future = None
    git_cancel = LinkedCancellation(cancel)
    progress_times = {}
    progress_lock = threading.Lock()

    def say(message):
        if threading.current_thread().name.startswith("coating-git") and not str(message).startswith("["):
            message = "[Git] " + str(message)
        output(redact(str(message), proxy))

    def phase(name):
        if cancel.is_set():
            raise KeyboardInterrupt
        if phase_fn is not None:
            phase_fn(name)

    def ask(prompt, default="", key=""):
        if cancel.is_set():
            raise KeyboardInterrupt
        answer = prompt_fn(key, prompt, default) if prompt_fn else input_fn(prompt)
        if cancel.is_set():
            raise KeyboardInterrupt
        return answer.strip() or default

    def yes(prompt, key=""):
        return ask(prompt + " [y/N]：", key=key).lower() in ("y", "yes", "是")

    def prepare_git():
        found = discover_git(project=root)
        if found:
            return found
        git_plan = plan_git_runtime(proxy=proxy, project=root, runtime_mirror=mirror != "pypi")
        if not ready(git_plan):
            raise ValueError("Git preparation plan failed; existing files preserved")
        if not yes("准备并校验项目私有 Git？", "git"):
            raise KeyboardInterrupt
        result = operation(execute_git_runtime, git_plan, True, emit, git_cancel, proxy)
        if not success(result):
            raise ValueError("Git preparation failed; see component diagnostics")
        return result["git"]

    def emit(event):
        nonlocal pipeline_pool, git_future
        stage = event.get("phase", "")
        component = event.get("component") or ("Git" if "git" in stage else
                    "Python" if "runtime" in stage else "依赖")
        event = dict(event, component=component)
        now = time.monotonic()
        progress = event.get("event") == "progress" and "download_bytes" in event
        with progress_lock:
            last = progress_times.get(component, 0)
            deliver = not progress or now - last >= 0.2
            if deliver:
                progress_times[component] = now
        if deliver and event_fn is not None:
            event_fn(event)
        if not progress:
            say("[" + component + "] " + str(event.get("text") or stage or json.dumps(event, ensure_ascii=False)))
        # Begin the next independent component only after Python has completely
        # downloaded and passed its pinned archive hash. Source checkout still waits for Git.
        if pipeline and prompt_fn is not None and stage == "runtime_extract" and git_future is None and not app.exists():
            pipeline_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coating-git")
            git_future = pipeline_pool.submit(prepare_git)

    def operation(function, *args, **kwargs):
        # Core operations own subprocess cleanup. Set their cancellation event
        # instead of unwinding past a live pip/Git child on Ctrl+C.
        previous = None
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGINT, lambda *unused: cancel.set())
        started = time.perf_counter()
        operation_name = getattr(function, "__name__", type(function).__name__)
        try:
            return function(*args, **kwargs)
        finally:
            emit({"event": "timing", "phase": operation_name, "seconds": time.perf_counter() - started,
                  "text": f"{operation_name}: {time.perf_counter() - started:.2f}s"})
            if previous is not None:
                signal.signal(signal.SIGINT, previous)

    def show(result):
        if plan_fn is not None:
            plan_fn(result)

        def mapping(value):
            return value if isinstance(value, Mapping) else {}

        def entries(value):
            if isinstance(value, (list, tuple)):
                return list(value)
            return [value] if value is not None else []

        say("状态：" + str(result.get("status", "unknown")))
        details = mapping(result.get("dependency_plan")) or result
        target = mapping(details.get("target"))
        if target:
            say(
                f"环境：{target.get('venv', '')}；Python {target.get('python', '')} {target.get('platform', '')}"
            )
        source = result.get("source")
        if isinstance(source, Mapping):
            say(
                f"源码：{source.get('url', result.get('repository_url', ''))} @ {source.get('commit', 'unknown')}"
            )
        elif isinstance(source, str) and source:
            # MinGit uses a vendor URL string; project deployment uses a mapping.
            say("工具来源：" + source)
        artifact = mapping(result.get("artifact"))
        if artifact:
            say(
                f"官方来源：{artifact.get('url', '')}\nSHA256：{artifact.get('sha256', '')}\n安装位置：{result.get('directory', '')}\n许可：{result.get('license', '见官方许可')}"
            )
        if "selected_backend" in result:
            identity = mapping(result.get("python"))
            say(
                f"Python：{identity.get('python', '?')} / {identity.get('platform', '?')}；计算后端：{result['selected_backend']}"
            )
            say(
                "设备："
                + ", ".join(
                    str(row.get("name", row.get("model", row.get("vendor", "?"))))
                    if isinstance(row, Mapping)
                    else str(row)
                    for row in entries(result.get("visible_gpus"))
                )
            )
            say(
                "可用性证据："
                + str(result.get("backend_usable", {}))
                + "；"
                + str(result.get("reason", ""))
            )
        if "actions" in details:
            say(
                f"依赖：{profile} / {', '.join(END_USER_GROUPS)}；{len(entries(details.get('packages')))} 个包，{len(entries(details['actions']))} 个执行步骤；镜像 {mirror}"
            )
        for message in entries(result.get("errors")) + entries(result.get("warnings")):
            say(message)
        logs = root / ".dependency-manager" / "logs"
        if unlinked_local_path(logs):
            try:
                logs.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix="installer-",
                    suffix=".json",
                    dir=logs,
                    delete=False,
                ) as handle:
                    handle.write(redact(json.dumps(result, ensure_ascii=False, indent=2), proxy))
                say("详细记录：" + handle.name)
            except OSError as exc:
                say("无法保存诊断记录：" + str(exc))

    def ready(plan):
        show(plan)
        return plan.get("status") == "ready"

    def success(result):
        show(result)
        return result.get("status") in ("success", "completed")

    try:
        say("在线安装器 · 仅使用 Python 标准库；Ctrl+C 取消")
        default = str(Path(project or Path.cwd() / "coating-sim").absolute())
        root = Path(ask(f"安装目录 [{default}]：", default, "directory")).expanduser().absolute()
        app = project_directory(root)
        source = ask("源码来源：1 CNB（默认），2 GitHub [1]：", "1", "source")
        if source not in ("1", "2"):
            raise ValueError("源码来源请输入 1 或 2")
        provider = "cnb" if source == "1" else "github"
        selected = resolve_mirror(root, mirror)
        enabled = ask("启用镜像加速（包索引 + Python 完整安装包；不改 Git URL）？ "
                      + ("[Y/n]：" if selected != "pypi" else "[y/N]："),
                      "y" if selected != "pypi" else "n", "mirror").lower() in ("y", "yes", "是")
        mirror = (selected if selected != "pypi" else "tuna") if enabled else "pypi"
        profile = "development" if yes("安装开发环境（含测试与静态检查工具）？", "development") else "runtime"
        say(
            f"安装目录：{root}\n源码：{app}\n依赖环境：{app / '.venv'}\n镜像：{MIRRORS[mirror]['url']}"
        )
        existing = (app / ".venv").exists() or (app / ".venv").is_symlink()
        phase("python")
        plan = plan_runtime(backend="cpu", proxy=proxy, project=root, runtime_mirror=mirror != "pypi")
        show(plan)
        if plan.get("status") != "ready":
            return 2
        if plan.get("action") != "reuse" and not yes(
            "确认上述官方 NuGet/镜像、固定 SHA256、许可后解压项目私有 Python？不使用系统 Python。",
            "python",
        ):
            return 130
        result = operation(execute_runtime, plan, True, emit, cancel, proxy)
        if not success(result):
            return 130 if cancel.is_set() else 2
        python, identity = result["python"], result["identity"]
        if existing:
            # Only inspect the chosen project environment, never external Python.
            inspected = inspect_environment(app, app / ".venv", END_USER_GROUPS, "cpu", profile)
            target = inspected.get("target") or {}
            if not target.get("valid") or Path(target.get("base_prefix", "")).resolve() != default_runtime_directory(root).resolve():
                say("现有 .venv 不属于本项目私有 Python，保留原环境；请选择新的安装目录。")
                return 2
            identity = target
        phase("recommend")
        recommendation = recommend_environment(identity, cancel_event=cancel, proxy=proxy,
                                               **({"manual_backend": backend} if backend else {}))
        if cancel.is_set() or recommendation.get("status") == "cancelled":
            raise KeyboardInterrupt
        show(recommendation)
        candidate = recommendation.get("selected_backend", "cpu")
        optional_cuda = candidate if requested_backend == "auto" and candidate in ("cuda12", "cuda13") else None
        backend = "cpu" if optional_cuda else candidate

        def route(selected, reason):
            if optional_cuda and selected == "cpu" and reason.startswith("可选 CUDA"):
                compute_route["fallback_reason"] = reason
            if compute_route.get("fallback_reason") and compute_route["fallback_reason"] not in reason:
                reason += "；" + compute_route["fallback_reason"]
            compute_route.update(requested_backend=requested_backend, candidate_backend=candidate,
                                 planned_backend=selected, reason=reason)
            summary = f"计算设备：{requested_backend} → {selected}；{reason}"
            show(dict(compute_route, operation="compute_route", summary=summary))
            say(summary)

        route(backend, ("先完成 CPU/受支持 DirectML 基础安装，再检查可选 " + optional_cuda)
              if optional_cuda else recommendation.get("reason", "按用户选择生成计划"))
        if sys.platform != "win32":
            say("DirectML 已跳过：本平台不支持 Windows 默认 DirectML 配置。")
        if existing:
            say("按上述硬件建议生成依赖计划；保留既有解释器，包冲突由计划检查。")
        if app.exists():
            plan = plan_environment(
                app,
                app / ".venv",
                END_USER_GROUPS,
                backend,
                mirror,
                "install",
                python,
                proxy,
                profile,
            )
            execute = execute_plan
        else:
            git = git_future.result() if git_future is not None else prepare_git()
            # Only create the container. Core deployment still requires a new app.
            root.mkdir(parents=True, exist_ok=True)
            phase("source")
            plan = operation(
                plan_project_deployment,
                str(app),
                groups=END_USER_GROUPS,
                backend=backend,
                profile=profile,
                mirror=mirror,
                python=python,
                proxy=proxy,
                emit=emit,
                cancel_event=cancel,
                source=provider,
                repository_url=CNB_URL if provider == "cnb" else None,
                git_path=git,
            )
            execute = execute_project_deployment
        if cancel.is_set():
            raise KeyboardInterrupt
        if not ready(plan):
            return 2
        launch_path = app / (
            "launch-coating.cmd" if sys.platform == "win32" else "launch-coating.sh"
        )
        say(f"安装后创建启动脚本：{launch_path}（已有文件不覆盖；不创建桌面快捷方式）")
        if plan_fn is not None:
            # Review metadata describes the later launcher transaction without
            # changing the dependency plan or its approved plan ID.
            plan_fn({
                "schema_version": 1,
                "operation": "installation_review",
                "installation_plan": plan,
                "launch_entry_intent": {
                    "path": str(launch_path),
                    "overwrite": False,
                    "desktop_shortcuts": sys.platform == "win32",
                },
            })
        if not yes("确认上述源码版本、目录、完整依赖及启动脚本计划，开始安装？", "install"):
            say("已取消依赖安装；已准备的源码或独立运行时保留，不删除任何现有文件。")
            return 130
        phase("dependencies")
        result = operation(
            execute, plan, confirmed=True, emit=emit, cancel_event=cancel, proxy=proxy
        )
        if not success(result):
            return 130 if cancel.is_set() or result.get("status") == "cancelled" else 2
        if optional_cuda:
            # The confirmed base transaction completes first. CUDA is optional in
            # auto mode; a late device/driver/wheel failure must not undo that base.
            phase("dependencies")
            extra = plan_environment(app, app / ".venv", END_USER_GROUPS, optional_cuda,
                                     mirror, "install", python, proxy, profile)
            show(extra)
            if cancel.is_set():
                raise KeyboardInterrupt
            if extra.get("status") == "ready":
                route(optional_cuda, "基础安装完成；执行已重新校验的可选 CUDA 计划")
                optional_result = operation(execute_plan, extra, confirmed=True, emit=emit,
                                            cancel_event=cancel, proxy=proxy)
                if cancel.is_set() or optional_result.get("status") == "cancelled":
                    raise KeyboardInterrupt
                if success(optional_result):
                    backend, result = optional_cuda, optional_result
                else:
                    # Pip may have changed dependencies before failing. CPU fallback
                    # is only a success after the real target's full base rows pass.
                    base = inspect_environment(app, app / ".venv", END_USER_GROUPS, "cpu", profile)
                    rows = base.get("packages") or []
                    if (base.get("errors") or not base.get("target", {}).get("valid")
                            or not rows or any(row.get("status") != "satisfied" for row in rows)):
                        show(base)
                        say("安装失败：可选 CUDA 未完成，基础依赖复核也未通过；保留现场，请检查日志后重试。")
                        return 2
                    route("cpu", "可选 CUDA 安装未完成；基础依赖已重新验证，继续使用 CPU："
                          + "; ".join(optional_result.get("errors", [])))
                    # The earlier base validation is stale after a failed pip writer.
                    result = {}
            else:
                route("cpu", "可选 CUDA 计划不可用；保留已完成的 CPU 基础安装："
                      + "; ".join(extra.get("errors", [])))
        phase("verify")
        inspected = inspect_environment(app, app / ".venv", END_USER_GROUPS, backend, profile)
        actual = inspected.get("target") or {}
        if actual.get("valid"):
            installed = result.get("dependency_result", result)
            verified = installed.get("compute_validation") or recommend_environment(actual, manual_backend=backend, cancel_event=cancel, proxy=proxy, check_wheels=False)
            if cancel.is_set() or verified.get("status") == "cancelled":
                raise KeyboardInterrupt
            show(verified)
            say("验证后建议计算后端：" + str(verified.get("recommended_backend", "cpu")))
            usable_backend = verified.get("recommended_backend", "cpu")
            if usable_backend not in ("cpu", "directml", "cuda12", "cuda13"):
                usable_backend = "cpu"
            if usable_backend != "cpu" and verified.get("backend_usable", {}).get(usable_backend) is not True:
                usable_backend = "cpu"
            compute_route["usable_backend"] = usable_backend
            route(backend, "已验证可用后端：" + usable_backend +
                  ("；可选 CUDA 未通过，保留 CPU/已验证 DirectML" if optional_cuda and usable_backend not in ("cuda12", "cuda13") else ""))
            if (requested_backend in ("cuda12", "cuda13")
                    and verified.get("backend_usable", {}).get(requested_backend) is not True):
                say("安装失败：手选 " + requested_backend + " 的数值验证未通过；依赖已保留。请选择 auto 或 cpu 后重试，未宣称 GPU 可用。")
                return 2
            for probe_backend, diagnostic in verified.get("probe_diagnostics", {}).items():
                say(probe_backend + " isolated probe: " + json.dumps(diagnostic, ensure_ascii=False))
            if backend != "cpu":
                usable = verified.get("backend_usable", {}).get(backend) is True
                say(
                    "GPU 数值计算验证："
                    + (
                        "通过"
                        if usable
                        else "未通过；依赖安装成功不代表 GPU 可用，可选择 CPU 重新生成计划。未修改驱动。"
                    )
                )
        else:
            show(inspected)
            say("依赖事务已完成，但目标解释器身份未验证；不宣称 GPU 可用。")
            if requested_backend in ("cuda12", "cuda13"):
                say("安装失败：手选 CUDA 的目标 Python 身份无法确认；请选择 auto 或 cpu 后重试。")
                return 2
        phase("launch")
        launch_plan = plan_launch_entries(app, name="launch-coating")
        if cancel.is_set():
            raise KeyboardInterrupt
        if launch_plan.get("status") == "ready":
            launch_result = execute_launch_entries(launch_plan, confirmed=True)
            if success(launch_result):
                say("启动脚本：" + str(launch_path))
            else:
                say("依赖已安装，但启动脚本创建失败；请查看以上诊断。")
                return 2
        else:
            # Repeat installation can reuse byte-identical installer scripts.
            # Unknown/conflicting content is preserved and reported as failure.
            expected = launch_plan.get("script")
            errors = launch_plan.get("errors", [])
            if (isinstance(expected, str) and len(errors) == 1
                    and errors[0].startswith("Existing launch entry is preserved;")
                    and launch_path.is_file() and unlinked_local_path(launch_path)
                    and launch_path.read_bytes() == expected.encode("utf-8")):
                say("复用已有启动脚本：" + str(launch_path))
            else:
                show(launch_plan)
                say("启动入口准备失败；冲突文件已保留，请查看以上诊断。")
                return 2
        if sys.platform == "win32":
            desktop_result = ensure_desktop_shortcut(app)
            if not success(desktop_result):
                say("项目已安装，但桌面快捷方式未完成；请查看诊断，重试不会覆盖无关文件。")
                return 2
            say("桌面快捷方式：" + str(desktop_result.get("path", "")))
        if cancel.is_set():
            raise KeyboardInterrupt
        say(f"安装完成：{app}；可使用 {app / '.venv'} 中的 Python 启动 launcher.py。")
        if event_fn is not None:
            event_fn({"event": "completion", "operation": "install", "target": str(app),
                      "compute_route": dict(compute_route),
                      "shortcut": desktop_result if sys.platform == "win32" else {"status": "success", "skipped": True}})
        return 0
    except (KeyboardInterrupt, EOFError):
        cancel.set()
        say("已取消；已有文件保留。中断安装可能留下部分环境，可重新运行检查。")
        return 130
    except (ValueError, OSError, RuntimeError) as exc:
        say("安装失败：" + str(exc))
        return 2
    finally:
        if pipeline_pool is not None:
            git_cancel.set()
            pipeline_pool.shutdown(wait=True, cancel_futures=True)
