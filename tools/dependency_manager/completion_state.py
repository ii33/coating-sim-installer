"""Explicit operation outcomes, independent of Tk and progress percentages."""

# The desktop shortcut is a noncritical post-step after a verified install/update.
# A failure keeps the installation/updated source and is reported as success-with-warning.
SHORTCUT_WARNING_TITLES = {
    "install": "⚠ 安装完成，桌面快捷方式待重试",
    "project-update": "⚠ 更新完成，桌面快捷方式待重试",
    "shortcut": "⚠ 桌面快捷方式仍未完成",
}


def terminal_outcome(operation, code, completion=None, reason="", *, requires_shortcut=False):
    completion = completion or {}
    shortcut = completion.get("shortcut") or {}
    if code == 130:
        return {"state": "cancelled", "code": 130, "title": "■ 已取消", "retry": False,
                "detail": "操作已取消，已写入的文件已保留。可查看日志后重新操作。"}
    if requires_shortcut and code == 0 and shortcut.get("status") == "failed":
        # Noncritical: the installation or updated source is retained. Report a
        # concrete cause and offer only the dedicated shortcut retry, never a
        # full install/update rerun.
        detail = "; ".join(shortcut.get("errors", [])) or reason or "原因未知"
        return {"state": "warning", "code": 0,
                "title": SHORTCUT_WARNING_TITLES.get(operation, "⚠ 操作完成，桌面快捷方式待重试"),
                "retry": False, "shortcut_retry": True,
                "detail": "桌面快捷方式未完成（安装或更新已保留）：" + detail}
    if code != 0:
        if operation == "shortcut":
            return {"state": "failed", "code": code, "title": "✕ 桌面快捷方式仍未完成",
                    "retry": False, "shortcut_retry": True,
                    "detail": reason or "桌面快捷方式未完成，请查看日志中的原因后重试。"}
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
    elif operation == "shortcut":
        title = "✓ 桌面快捷方式已就绪"
    else:
        title = "✓ 检查完成"
    return {"state": "success", "code": 0, "title": title, "retry": False, "detail": ""}
