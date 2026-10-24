"""Click-authorized updates of the selected public main source, with recovery journal."""
import os
import threading
import time

from . import default_install
from . import project_deploy as deploy
from .git_runtime import discover_git
from .interactive import CNB_URL, project_directory
from .mirrors import ProxyConfig, redact
from .project_operations import owned_record, project_operation, save_record
from .runtime_paths import unlinked_local_path

SCHEMA = "coating-source-update-v1"
KNOWN_ORIGINS = {deploy.REPOSITORY_URL, deploy.REPOSITORY_URL[:-4], CNB_URL, CNB_URL + ".git"}


def update_project(container, provider="cnb", *, mirror="tuna", backend=None, development=False,
                   emit=None, output=None, phase_fn=None, cancel_event=None, proxy=None):
    cancel = cancel_event or threading.Event()
    proxy = proxy or ProxyConfig("env")
    output = output or (lambda value: None)
    emit = emit or (lambda value: None)
    result = {"status": "failed", "errors": [], "warnings": [], "source_changed": False}
    app, journal, run = None, None, None
    previous, latest, original_branch = None, None, None
    changed = False
    roles_started = False
    managed_completion = {}
    lease = None
    lease_entered = False

    def log(text):
        output("[主项目] " + redact(str(text), proxy))

    def event(value):
        emit({**value, "component": "主项目"})
        if value.get("text"):
            log(value["text"])

    def phase(text):
        if cancel.is_set():
            raise InterruptedError("主项目更新已取消")
        if phase_fn:
            phase_fn(text)
        log(text)

    def dependency_active():
        return any(os.path.lexists(path) for path in (
            app / ".venv.dependency-manager.lock", app / ".git" / deploy._STATE / "execute.lock",
            app / ".runtime/compute-runtime-install.lock"))

    def version():
        path = app / "coat_sim/version.txt"
        if not path.is_file() or path.stat().st_size > 128:
            raise ValueError("项目版本文件无效；未覆盖任何用户数据")
        return path.read_text(encoding="utf-8").strip()

    try:
        app = project_directory(container)
        if not (app / ".git").is_dir() or not unlinked_local_path(app / ".git"):
            raise ValueError("当前路径还没有受支持的主项目安装，请先点击安装")
        lease = project_operation(container)
        lease.__enter__()
        lease_entered = True
        source = deploy.repository_source(provider, CNB_URL if provider == "cnb" else None)
        git = discover_git(project=container) or discover_git(project=app.parent)
        if not git:
            raise ValueError("此安装没有可用的 Git；请先点击安装准备 Git")
        deadline = time.monotonic() + 300
        filters = []
        def run(args, *, rollback=False):
            return deploy._run_git(git, [*filters, *args], cwd=app, proxy=proxy,
                cancel_event=threading.Event() if rollback else cancel,
                deadline=time.monotonic() + 30 if rollback else deadline, emit=event, log=True)
        phase("检查主项目最新源码")
        if run(["remote", "get-url", "origin"]).strip() not in KNOWN_ORIGINS:
            raise ValueError("当前目录的来源不是公开主项目；已停止，未改动目录")
        if run(["ls-remote", "--get-url", source["url"]]).strip() != source["url"]:
            raise ValueError("Git URL 重写改变了所选公开来源；更新已停止")
        hooks = app / ".git" / deploy._STATE / "disabled-hooks"
        if hooks.exists() and (not unlinked_local_path(hooks) or any(hooks.iterdir())):
            raise ValueError("受管理的禁用 hooks 目录含未知文件；更新已停止")
        for key in run(["config", "--null", "--name-only", "--list"]).split("\0"):
            if key.startswith("filter.") and key.rsplit(".", 1)[-1] in ("clean", "smudge", "process", "required"):
                if len(key) > 4096 or any(ord(c) < 32 for c in key):
                    raise ValueError("Git filter 配置无效")
                filters += ["-c", key + ("=false" if key.endswith(".required") else "=")]
        def tracked_clean():
            return not run(["status", "--porcelain", "--untracked-files=no", "--ignore-submodules=none"]).strip()
        flags = run(["ls-files", "-v", "-z"]).split("\0")
        if any(row and (row[0].islower() or row[0] == "S") for row in flags):
            raise ValueError("本地存在隐藏修改检查的 Git 索引标记；已停止更新，未清除标记")
        if not tracked_clean():
            raise ValueError("检测到本地源码修改；已停止更新。请先保存/提交修改或选择新的安装目录")
        if dependency_active():
            raise ValueError("此项目的依赖安装仍在运行；请等待结束后再更新")
        previous = run(["rev-parse", "--verify", "HEAD"]).strip()
        if not deploy._SHA.fullmatch(previous):
            raise ValueError("本地提交记录无效")
        original_branch = run(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        journal = app / ".git/coating-installer-update.json"
        prior = owned_record(journal, root=app, schema=SCHEMA)
        if prior and prior.get("state") == "recovery_required":
            raise ValueError("上次更新尚需恢复，请检查记录：" + str(journal))
        if prior and prior.get("state") == "applying" and previous not in (prior.get("old_commit"), prior.get("new_commit")):
            raise ValueError("上次更新中断后源码提交又发生变化；请先检查恢复记录")
        # All mutating updates use the same authenticated current-layout contract.
        # Mere sibling-base/marker presence never authorizes an interpreter probe.
        layout_record = {"runtime_layout": "managed_roles"}
        old_version = version()
        result.update(local_version=old_version, local_commit=previous, provider=provider)
        latest = deploy._resolve_commit(run, "refs/heads/main", source["url"])["commit"]
        result["latest_commit"] = latest
        if latest != previous:
            phase("下载并验证主项目更新")
            run(["fetch", "--no-tags", "--no-recurse-submodules", "--depth=256", "--", source["url"], latest])
            deploy._validate_tree(run, latest)
            try:
                run(["merge-base", "--is-ancestor", previous, latest])
            except ValueError:
                raise ValueError("本地历史与所选 main 不存在已验证的向前更新关系；已保留本地版本") from None
            new_paths = set(run(["ls-tree", "-r", "--name-only", "-z", latest]).split("\0"))
            old_paths = set(run(["ls-tree", "-r", "--name-only", "-z", previous]).split("\0"))
            for relative in new_paths - {""}:
                if relative.split("/", 1)[0] in (".venv", ".runtime", ".dependency-manager"):
                    raise ValueError("更新包含受保护的运行时目录；已停止")
                if relative not in old_paths and os.path.lexists(app / relative):
                    raise ValueError("更新会与已有工程/配置/数据文件冲突，已保留：" + relative)
            if not tracked_clean() or run(["rev-parse", "HEAD"]).strip() != previous:
                raise ValueError("下载期间本地源码发生变化；已停止")
            if dependency_active():
                raise ValueError("依赖任务正在运行；源码未切换")
            save_record(journal, {"state": "applying", "old_commit": previous, "new_commit": latest,
                                 "old_version": old_version, "provider": provider, **layout_record}, root=app, schema=SCHEMA)
            phase("应用主项目源码更新")
            # No force/reset/clean: Git also protects ignored collision paths.
            changed = True
            run(["checkout", "--quiet", "--no-overwrite-ignore", "--detach", latest])
            result["source_changed"] = True
        phase("检查并更新项目私有分环境依赖")
        # Even an unchanged HEAD can need repair after a partial role install.
        # Record before entering any runtime writer, including same-HEAD repair.
        save_record(journal, {"state": "applying", "old_commit": previous, "new_commit": latest,
            "old_version": old_version, "provider": provider, **layout_record}, root=app, schema=SCHEMA)
        roles_started = True
        messages = []
        def managed_output(text):
            messages[:] = [redact(str(text), proxy)]
            log(text)
        def managed_event(value):
            if value.get("event") == "completion":
                managed_completion.update(value)
            else:
                # The coordinator also sends event text through output.
                emit({**value, "component": "主项目"})
        def managed_review(value):
            if value.get("operation") == "compute_route":
                result["compute_route"] = dict(value)
                if value.get("summary"):
                    log(value["summary"])
            event({"event": "plan", **value})
        code = default_install.run_default_installation(container, output=managed_output,
            prompt_fn=lambda key: "y", phase_fn=phase, plan_fn=managed_review, cancel_event=cancel,
            mirror=mirror, backend=backend, event_fn=managed_event, source=provider, development=development)
        result["runtime_layout"] = "managed_roles"
        if code == 130 or cancel.is_set():
            raise InterruptedError("分环境依赖更新已取消；已准备的角色环境保留，可重试")
        if code != 0 or not managed_completion:
            detail = messages[-1] if messages else "未收到分环境安装完成验证"
            raise ValueError("分环境依赖更新未完成：" + detail)
        # Installed role bindings are not evidence of numerical GPU readiness.
        for key in ("compute_route", "runtime_roles", "manifest"):
            if key in managed_completion:
                result[key] = managed_completion[key]
        # Role package transactions have their own timeouts and may take hours.
        # Their duration must not consume the earlier Git source-fetch budget.
        deadline = time.monotonic() + 30
        phase("验证当前主项目版本")
        if run(["rev-parse", "HEAD"]).strip() != latest or not tracked_clean():
            raise ValueError("更新后的源码身份/完整性检查失败")
        current_version = version()
        # The shared coordinator owns launcher/shortcut preparation. Its verified
        # completion is required even for same-HEAD repair; there is no alternate
        # updater-local dependency or shortcut installation path.
        shortcut = managed_completion.get("shortcut") or {"status": "failed", "errors": ["未收到桌面快捷方式结果"]}
        if shortcut.get("status") != "success":
            shortcut_error = "; ".join(shortcut.get("errors", [])) or "未知错误"
            result["warnings"].append("主项目已更新并保留；桌面快捷方式未完成：" + shortcut_error)
            log("桌面快捷方式未完成（非关键；更新后的源码与依赖已保留）：" + shortcut_error)
        save_record(journal, {"state": "success", "old_commit": previous, "new_commit": latest,
            "version": current_version, "provider": provider, "shortcut": shortcut.get("path"),
            "shortcut_status": shortcut.get("status"), "updated_at": time.time(), **layout_record}, root=app, schema=SCHEMA)
        result.update(status="success", up_to_date=latest == previous, current_version=current_version,
                      current_commit=latest, shortcut=shortcut)
        log(("已是最新源码：" if latest == previous else "更新完成：") + current_version + " @ " + latest[:12])
        return result
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["status"] = "cancelled" if cancel.is_set() or isinstance(exc, InterruptedError) else "failed"
        result["errors"].append(redact(str(exc), proxy))
        log(str(exc))
        # Checkout rollback never discards user edits. The journal identifies both commits.
        if changed and run is not None:
            restored = False
            try:
                log("正在恢复更新前的源码；用户文件和私有运行时保留")
                run(["checkout", "--quiet", "--no-overwrite-ignore", "--detach", previous], rollback=True)
                if original_branch != "HEAD" and run(["rev-parse", "refs/heads/" + original_branch], rollback=True).strip() == previous:
                    run(["checkout", "--quiet", "--no-overwrite-ignore", original_branch], rollback=True)
                restored = run(["rev-parse", "HEAD"], rollback=True).strip() == previous
            except (OSError, ValueError) as recovery:
                result["warnings"].append("自动恢复受阻；不要删除数据，请检查恢复记录：" + redact(str(recovery), proxy))
            result["source_rolled_back"] = restored
            result["warnings"].append("私有依赖可能已部分更新；可重试，或点击安装修复。工程/配置/运行数据未删除")
            try:
                save_record(journal, {"state": "failed" if restored else "recovery_required", "old_commit": previous,
                    "new_commit": latest, "errors": result["errors"], "source_rolled_back": restored,
                    **layout_record}, root=app, schema=SCHEMA)
            except (OSError, ValueError):
                result["warnings"].append("恢复记录未能更新；原 applying 记录已保留")
        elif roles_started:
            # Same-HEAD role repair has no source checkout to undo. Keep its
            # failed record and role files for an authenticated coordinator retry.
            result["warnings"].append("私有角色依赖可能已部分更新；源码和用户数据保留，可重试分环境更新")
            try:
                save_record(journal, {"state": "failed", "old_commit": previous, "new_commit": latest,
                    "errors": result["errors"], "runtime_layout": "managed_roles"}, root=app, schema=SCHEMA)
            except (OSError, ValueError):
                result["warnings"].append("恢复记录未能更新；原 applying 记录已保留")
        return result
    finally:
        if lease_entered:
            lease.__exit__(None, None, None)
