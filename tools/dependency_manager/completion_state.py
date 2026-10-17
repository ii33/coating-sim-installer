"""Explicit operation outcomes, independent of Tk and progress percentages."""


def terminal_outcome(operation, code, completion=None, reason="", *, requires_shortcut=False):
    completion = completion or {}
    shortcut = completion.get("shortcut") or {}
    if code == 130:
        return {"state": "cancelled", "code": 130, "title": "■ 已取消", "retry": False,
                "detail": "操作已取消，已写入的文件已保留。可查看日志后重新操作。"}
    if requires_shortcut and code == 0 and shortcut.get("status") == "failed":
        code = 2
        reason = "桌面快捷方式未完成：" + "; ".join(shortcut.get("errors", []))
    if code != 0:
        return {"state": "failed", "code": code, "title": "✕ 操作失败", "retry": True,
                "detail": reason or "当前步骤未完成，请查看日志中的原因后重试。"}
    if requires_shortcut and shortcut.get("status") != "success":
        return {"state": "unconfirmed", "code": 0, "title": "ℹ 操作结束，结果待确认", "retry": True,
                "detail": "尚未收到桌面快捷方式的成功确认，请检查日志或重试安装。"}
    if operation == "install":
        title = "✓ 安装完成"
    elif operation == "project-update":
        title = "✓ 已是最新版" if completion.get("up_to_date") else "✓ 更新完成"
    elif operation == "python":
        title = "✓ 私有 Python 已就绪"
    else:
        title = "✓ 检查完成"
    return {"state": "success", "code": 0, "title": title, "retry": False, "detail": ""}
