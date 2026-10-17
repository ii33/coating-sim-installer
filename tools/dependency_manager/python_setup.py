"""Explicit project-Python preparation; the frozen tool is never a venv base."""
import json
import sys

from .mirrors import redact
from .python_discovery import discover_python
from .runtime import execute_runtime, plan_runtime


def prepare_python(project, proxy, cancel_event, review, output, prompt):
    found = discover_python("directml" if sys.platform == "win32" else "cpu", project=project, cancel_event=cancel_event, preferred_minor="3.12")
    if cancel_event.is_set() or found.get("status") == "cancelled":
        return 130, "", "已取消 Python 检查。"
    existing = found.get("selected_python")
    if existing:
        details = {"operation": "python_prepare", "status": "reused", "python": existing,
                   "downloaded": False, "installer_executed": False,
                   "message": "已有兼容的完整 CPython。点击“安装”后将使用它创建项目独立 .venv；不会在该 Python 中安装项目依赖。"}
        return 0, redact(json.dumps(details, ensure_ascii=False, indent=2), proxy), "已有兼容 Python，无需重装；点击“安装”继续。"
    plan = plan_runtime(backend="cpu", proxy=proxy, project=project)
    review(plan)
    output(json.dumps(plan, ensure_ascii=False, indent=2))
    if plan.get("status") != "ready":
        detail = "Python 安装计划受阻，未下载或执行安装器。\n" + "\n".join(str(error) for error in plan.get("errors", []))
        return 2, redact(detail, proxy), "Python 准备受阻，未执行安装；请查看具体原因。"
    if prompt("python") != "y":
        return 130, "", "已取消 Python 安装。"
    result = execute_runtime(plan, True, lambda event: output(event.get("text") or event), cancel_event, proxy)
    success = result.get("status") in ("success", "completed")
    code = 130 if cancel_event.is_set() or result.get("status") == "cancelled" else (0 if success else 2)
    return code, redact(json.dumps(result, ensure_ascii=False, indent=2), proxy), ""
