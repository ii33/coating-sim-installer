"""Prepare only the selected project's owned CPython; never discover system Python."""
import json

from .mirrors import redact, resolve_mirror
from .runtime import execute_runtime, plan_runtime


def prepare_python(project, proxy, cancel_event, review, output, prompt, *, mirror=None):
    if cancel_event.is_set():
        return 130, "", "已取消项目 Python 准备。"
    plan = plan_runtime(backend="cpu", proxy=proxy, project=project,
                        runtime_mirror=resolve_mirror(project, mirror) != "pypi")
    review(plan)
    output(json.dumps(plan, ensure_ascii=False, indent=2))
    if plan.get("status") != "ready":
        return 2, "\n".join(plan.get("errors", [])), "项目 Python 准备受阻；已有内容保留。"
    if plan.get("action") != "reuse" and prompt("python") != "y":
        return 130, "", "已取消项目 Python 部署。"
    result = execute_runtime(plan, True, lambda e: output(e.get("text") or e), cancel_event, proxy)
    code = 130 if cancel_event.is_set() or result.get("status") == "cancelled" else (0 if result.get("status") == "success" else 2)
    return code, redact(json.dumps(result, ensure_ascii=False, indent=2), proxy), (
        "项目私有 Python 已就绪；未使用系统 Python。" if code == 0 else "项目 Python 部署未完成。")
