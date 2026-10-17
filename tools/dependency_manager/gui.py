"""Small stdlib Tk front end for the existing interactive installation flow."""

from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .app_info import VERSION, icon_path
from .interactive import project_directory, run_interactive
from .mirrors import ProxyConfig, redact
from .python_setup import prepare_python
from .runtime_paths import unlinked_local_path
from .updates import check_installer_updates, check_project_updates, validated_release_page

# Tk is optional for terminal-only hosts; load the GUI capability independently.
tk: Any
filedialog: Any
ttk: Any
try:
    import tkinter as tk
    from tkinter import filedialog, ttk
except ImportError:
    tk = None
    filedialog = None
    ttk = None

PHASES = {
    "python": "检查兼容 Python",
    "recommend": "检测硬件与计算后端",
    "source": "准备源码：下载所选来源到新 app 目录（此阶段会写入源码）",
    "dependencies": "安装依赖：正在运行已确认的安装计划",
    "verify": "核验目标环境与计算后端",
    "launch": "创建启动脚本：短事务正在完成，取消将等待安全收尾",
}
QUESTIONS = {
    "python": "安装独立 Python？请核对下方官方来源、SHA256、许可和目标目录。已有兼容 Python 可以跳过。",
    "git": "安装目录内缺少可用 Git。是否按下方官方来源、校验值和目录安装独立 Git？",
    "install": "源码已准备。是否按下方源码版本、目录、完整依赖和启动脚本计划开始安装？",
}


@dataclass
class Confirmation:
    key: str
    answered: threading.Event = field(default_factory=threading.Event)
    answer: str = "n"


class InstallerSession:
    """One operation's immutable choices and cancellable queue-based prompts."""

    def __init__(self, choices, events=None):
        self.choices = dict(choices)
        self.events = events if events is not None else queue.Queue(maxsize=512)
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self.pending: Confirmation | None = None

    def send(self, kind, value):
        while not self.cancel_event.is_set() or kind == "done":
            try:
                self.events.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                continue
        # Cleanup can still report partial-installation or termination warnings
        # after cancellation. Preserve available log records without waiting for
        # a full queue, which would prevent an abandoned worker from exiting.
        if kind == "log":
            try:
                self.events.put_nowait((kind, value))
            except queue.Full:
                pass

    def prompt(self, key, unused_prompt="", unused_default=""):
        if self.cancel_event.is_set():
            raise KeyboardInterrupt
        if key in self.choices:
            return self.choices[key]
        if key not in QUESTIONS:
            raise RuntimeError("Unknown installation confirmation: " + key)
        request = Confirmation(key)
        with self._lock:
            if self.cancel_event.is_set():
                raise KeyboardInterrupt
            self.pending = request
        self.send("confirm", request)
        while not request.answered.wait(0.1):
            if self.cancel_event.is_set():
                raise KeyboardInterrupt
        with self._lock:
            if self.pending is request:
                self.pending = None
        if self.cancel_event.is_set():
            raise KeyboardInterrupt
        return request.answer

    def respond(self, request: Confirmation, accepted: bool) -> bool:
        with self._lock:
            if self.cancel_event.is_set() or self.pending is not request or request.answered.is_set():
                return False
            request.answer = "y" if accepted else "n"
            request.answered.set()
            return True

    def cancel(self):
        with self._lock:
            self.cancel_event.set()
            if self.pending is not None:
                self.pending.answer = "n"
                self.pending.answered.set()
                self.pending = None


class DependencyManagerWindow:
    def __init__(self, window, project):
        self.window = window
        window.title("Coating Sim · 在线安装器 " + VERSION)
        try:
            window.iconbitmap(str(icon_path()))
        except (OSError, tk.TclError):
            pass
        window.geometry("800x680")
        window.minsize(460, 380)
        window.protocol("WM_DELETE_WINDOW", self.close)
        self.directory = tk.StringVar(value=str(Path(project).absolute()))
        self.source = tk.StringVar(value="cnb")
        self.mirror = tk.BooleanVar(value=False)
        self.development = tk.BooleanVar(value=False)
        self.update_channel = tk.StringVar(value="preview")
        self.update_page = None
        self.status = tk.StringVar(value="选择安装目录和来源，然后安装。Python 与项目 Qt 环境相互独立。")
        self.log_path = tk.StringVar(value="日志将在开始操作后保存到安装目录。")
        self.session = None
        self.worker = None
        self.confirmation = None
        self.closing = False
        self.controls = []
        self.last_code = None
        self.current_phase = "等待开始"
        self.current_plan = ""
        self.log_file = None
        self._build()
        window.after(50, self._drain)

    @property
    def busy(self):
        return self.worker is not None

    def _build(self):
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, highlightthickness=0, width=1, height=1)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        body = ttk.Frame(canvas, padding=(0, 0, 8, 8))
        body.columnconfigure(0, weight=1)
        item = canvas.create_window(0, 0, anchor="nw", window=body)
        body.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        ttk.Label(body, text="在线安装", font=("Microsoft YaHei UI", 17, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(body, text="安装目录").grid(row=1, column=0, sticky="w")
        paths = ttk.Frame(body)
        paths.grid(row=2, column=0, sticky="ew", pady=(4, 12))
        paths.columnconfigure(0, weight=1)
        entry = ttk.Entry(paths, textvariable=self.directory)
        entry.grid(row=0, column=0, sticky="ew")
        browse = ttk.Button(paths, text="选择…", command=self.choose_directory)
        browse.grid(row=0, column=1, padx=(8, 0))
        self.controls.extend((entry, browse))
        sources = ttk.LabelFrame(body, text="源码来源", padding=8)
        sources.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        for column, (label, value) in enumerate((("CNB（默认）", "cnb"), ("GitHub", "github"))):
            button = ttk.Radiobutton(sources, text=label, variable=self.source, value=value)
            button.grid(row=0, column=column, sticky="w", padx=(0, 16))
            self.controls.append(button)
        for row, label, variable in (
            (4, "镜像加速（清华 PyPI）", self.mirror),
            (5, "开发环境（含测试与检查工具）", self.development),
        ):
            control = ttk.Checkbutton(body, text=label, variable=variable)
            control.grid(row=row, column=0, sticky="w", pady=4)
            self.controls.append(control)
        hint = ttk.Label(body, text="新安装会先准备源码，再确认依赖计划；已有 app 和虚拟环境会接受检查。代理沿用本次进程环境。", wraplength=680)
        hint.grid(row=6, column=0, sticky="ew", pady=(8, 12))
        ttk.Label(body, text="安装日志").grid(row=7, column=0, sticky="w")
        log_frame = ttk.Frame(body)
        log_frame.grid(row=8, column=0, sticky="ew", pady=4)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", height=10, width=1, state="disabled")
        self.log.grid(row=0, column=0, sticky="ew")
        log_scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scroll.set)
        log_label = ttk.Label(body, textvariable=self.log_path, wraplength=680)
        log_label.grid(row=9, column=0, sticky="ew", pady=4)
        actions = ttk.Frame(outer, padding=(0, 10, 0, 0))
        actions.grid(row=1, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure((0, 1), weight=1)
        python = ttk.Button(actions, text="检查 / 准备 Python", command=lambda: self.start("python"))
        python.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=3)
        install = ttk.Button(actions, text="安装", command=self.start)
        install.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=3)
        self.controls.extend((python, install))
        self.log_button = ttk.Button(actions, text="打开日志", command=self.open_log, state="disabled")
        self.log_button.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=3)
        self.cancel_button = ttk.Button(actions, text="取消", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=3)
        updates = ttk.Button(actions, text="检查更新（只读）", command=self.check_updates)
        updates.grid(row=2, column=0, sticky="ew", padx=(0, 4), pady=3)
        self.update_button = ttk.Button(actions, text="打开安装器新版下载页", command=self.open_update_page, state="disabled")
        self.update_button.grid(row=2, column=1, sticky="ew", padx=(4, 0), pady=3)
        channel = ttk.Frame(body)
        channel.grid(row=10, column=0, sticky="ew", pady=4)
        ttk.Label(channel, text="安装器更新渠道：").pack(side="left")
        channels = ttk.Combobox(channel, textvariable=self.update_channel, values=("stable", "preview"), state="readonly", width=10)
        channels.pack(side="left")
        ttk.Label(channel, text="stable＝正式版；preview＝含预发布").pack(side="left", padx=6)
        self.controls.append(updates)
        status = ttk.Label(outer, textvariable=self.status, wraplength=700)
        status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        def wrap(event):
            status.configure(wraplength=max(150, event.width - 24))

        def fit_body(event):
            canvas.itemconfigure(item, width=event.width)
            for label in (hint, log_label):
                label.configure(wraplength=max(150, event.width - 24))

        outer.bind("<Configure>", wrap)
        canvas.bind("<Configure>", fit_body)
        self.window.bind("<Escape>", lambda event: self.cancel())

    def choose_directory(self):
        selected = filedialog.askdirectory(parent=self.window, initialdir=self.directory.get())
        if selected:
            self.directory.set(selected)

    def _append(self, message, *, complete=False):
        redacted = redact(str(message), ProxyConfig("env"))
        message = redacted[:32000] + "\n"
        self.log.configure(state="normal")
        self.log.insert("end", message)
        # Keep the live widget bounded; final recovery diagnostics are saved in
        # full even when their displayed excerpt is shorter.
        if int(self.log.index("end-1c").split(".")[0]) > 1800:
            self.log.delete("1.0", "400.0")
        self.log.see("end")
        self.log.configure(state="disabled")
        if self.log_file:
            try:
                with Path(self.log_file).open("a", encoding="utf-8") as handle:
                    handle.write(redacted + "\n" if complete else message)
            except OSError as exc:
                self.status.set("日志写入失败：" + redact(str(exc)))

    def start(self, operation="install"):
        if self.busy or self.closing:
            return
        try:
            root = Path(self.directory.get()).expanduser().absolute()
            project_directory(root)
            logs = root / ".dependency-manager" / "logs"
            if not unlinked_local_path(logs):
                raise ValueError("日志目录不能使用链接或重解析点")
            logs.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="gui-", suffix=".log", dir=logs, delete=False) as handle:
                self.log_file = handle.name
            self.log_path.set(self.log_file)
            self.log_button.configure(state="normal")
        except (OSError, ValueError) as exc:
            self.status.set("无法开始：" + redact(str(exc)))
            self._append(self.status.get())
            return
        self.session = InstallerSession({
            "directory": str(root), "source": "1" if self.source.get() == "cnb" else "2",
            "mirror": "y" if self.mirror.get() else "n",
            "development": "y" if self.development.get() else "n",
        })
        session = self.session
        for control in self.controls:
            control.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.last_code = None
        self.current_plan = ""
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.status.set("检查安装计划…")
        self.current_phase = "检查 Python 安装计划" if operation == "python" else "准备安装"
        self._append("开始：" + operation + "；目录：" + str(root))

        def work():
            code = 2
            completion_details = ""
            summary = ""
            def output(value):
                session.send("log", redact(str(value), ProxyConfig("env")))

            def review(plan):
                session.send("plan", redact(json.dumps(plan, ensure_ascii=False, indent=2), ProxyConfig("env")))
            try:
                if operation == "python":
                    proxy = ProxyConfig("env")
                    code, completion_details, summary = prepare_python(root, proxy, session.cancel_event, review, output, session.prompt)
                else:
                    code = run_interactive(root, output=output, prompt_fn=session.prompt,
                                           phase_fn=lambda value: session.send("phase", value),
                                           plan_fn=review, cancel_event=session.cancel_event)
            except (KeyboardInterrupt, EOFError):
                code = 130
            except Exception as exc:
                completion_details = "安装失败：" + redact(str(exc), ProxyConfig("env"))
            finally:
                if session.cancel_event.is_set():
                    code = 130
                session.send("done", {"code": code, "details": completion_details, "summary": summary})

        self.worker = threading.Thread(target=work, name="coating-installer")
        self.worker.start()

    def _confirm(self, request):
        session = self.session
        if session is None or session.cancel_event.is_set() or session.pending is not request:
            return
        dialog = tk.Toplevel(self.window)
        self.confirmation = dialog
        dialog.title("确认安装计划")
        dialog.transient(self.window)
        dialog.geometry("680x440")
        dialog.minsize(400, 300)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        question = ttk.Label(frame, text=QUESTIONS[request.key], wraplength=640)
        question.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        details = tk.Text(frame, wrap="word", width=1, height=8)
        # Plans are independently delivered as complete redacted JSON. The bounded
        # live log is a progress view and must never truncate the review itself.
        details.insert("end", self.current_plan or self.log.get("1.0", "end"))
        details.configure(state="disabled")
        details.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, command=details.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        details.configure(yscrollcommand=scrollbar.set)

        def respond(accepted):
            session.respond(request, accepted)
            if self.confirmation is dialog:
                self.confirmation = None
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(buttons, text="确认", command=lambda: respond(True)).pack(side="right")
        ttk.Button(buttons, text="跳过" if request.key == "python" else "不安装", command=lambda: respond(False)).pack(side="right", padx=8)
        dialog.protocol("WM_DELETE_WINDOW", lambda: respond(False))
        frame.bind("<Configure>", lambda event: question.configure(wraplength=max(150, event.width - 20)))
        self.status.set("等待确认；可以取消，关闭窗口会安全结束本次操作。")

    def _drain(self):
        session = self.session
        if session is not None:
            for _unused in range(100):
                try:
                    kind, value = session.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "log":
                    self._append(value)
                elif kind == "plan":
                    self.current_plan = value
                elif kind == "phase" and not session.cancel_event.is_set():
                    self.current_phase = PHASES.get(value, value)
                    self.status.set(self.current_phase)
                    self._append("阶段：" + self.current_phase)
                elif kind == "confirm":
                    self._confirm(value)
                elif kind == "updates" and not session.cancel_event.is_set():
                    self.update_page = value
                elif kind == "done":
                    if value["details"]:
                        self._append(value["details"], complete=True)
                    self.last_code = value["code"]
                    self.status.set(value.get("summary") or {0: "操作完成。详见日志中的目标环境与后端核验结果。", 130: "已取消；已写入文件保留，请查看日志后重试。"}.get(self.last_code, "安装失败（" + self.current_phase + "）；请查看日志中的原因。"))
                    self._append(self.status.get())
        if self.worker is not None and self.last_code is not None and not self.worker.is_alive():
            self.worker.join(timeout=0)
            self.worker = None
            self.session = None
            for control in self.controls:
                control.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.update_button.configure(state="normal" if self.update_page else "disabled")
            if self.closing:
                self.window.destroy()
                return
        self.window.after(50, self._drain)

    def check_updates(self):
        if self.busy or self.closing:
            return
        root = Path(self.directory.get()).expanduser().absolute()
        provider, channel = self.source.get(), self.update_channel.get()
        self.log_file = None
        self.log_path.set("更新检查仅显示结果；不会下载、安装或改写项目。")
        self.update_page = None
        self.update_button.configure(state="disabled")
        self.log_button.configure(state="disabled")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.session = InstallerSession({})
        session = self.session
        self.last_code = None
        self.status.set("正在分别检查安装器和项目更新…")
        for control in self.controls:
            control.configure(state="disabled")
        self.cancel_button.configure(state="normal")

        def work():
            code, details, summary = 0, [], "更新检查完成；项目仅检查，安装器下载需点击打开页面。"
            try:
                for label, result in (
                    ("安装器", check_installer_updates(channel, proxy=ProxyConfig("env"), cancel_event=session.cancel_event)),
                    ("项目", check_project_updates(root, provider, proxy=ProxyConfig("env"), cancel_event=session.cancel_event)),
                ):
                    labels = {"up_to_date": "已是最新", "update_available": "有可用更新", "not_installed": "尚未安装",
                              "no_releases": "该渠道尚无发布", "rate_limited": "API 请求受限", "timed_out": "检查超时",
                              "unavailable": "暂时无法检查", "cancelled": "已取消", "different_or_current": "版本未变新，提交可能不同",
                              "incomparable_versions": "版本格式不同，无法判断新旧"}
                    details.append(label + "：" + labels.get(result["status"], result["status"]))
                    details.append("本地版本：" + str(result.get("local_version") or "未安装 / 未知"))
                    details.append("最新版本：" + str(result.get("latest_version") or "未知"))
                    source = result.get("source")
                    details.append("来源：" + str(source.get("url") if isinstance(source, dict) else source))
                    details.append("更新可用：" + ("是" if result.get("update_available") is True else "否 / 无法确定"))
                    details.append(result.get("action", ""))
                    for digest in result.get("asset_sha256", []):
                        details.append("新版 EXE SHA256（GitHub 发布资产摘要）：" + digest)
                    details.extend(result.get("errors", []))
                    details.append("")
                    if label == "安装器" and result.get("update_available") and result.get("download_page"):
                        session.send("updates", (result["latest_version"], result["download_page"]))
                if session.cancel_event.is_set():
                    code, summary = 130, "更新检查已取消；文件未更改。"
            except Exception as exc:
                code, summary = 2, "无法检查更新；文件未更改。"
                details.append(redact(str(exc)))
            finally:
                session.send("done", {"code": code, "details": "\n".join(details), "summary": summary})

        self.worker = threading.Thread(target=work, name="coating-update-check")
        self.worker.start()

    def open_update_page(self):
        if self.busy or not self.update_page:
            return
        import webbrowser
        try:
            tag, page = self.update_page
            webbrowser.open(validated_release_page(tag, page))
            self.status.set("已请求打开官方 Release 下载页；请核对 SHA256。当前 EXE 不会被覆盖。")
        except (ValueError, OSError) as exc:
            self.status.set("无法打开更新页面：" + redact(str(exc)))

    def cancel(self):
        if self.session is not None:
            self.session.cancel()
            if self.confirmation is not None:
                self.confirmation.destroy()
                self.confirmation = None
            self.status.set("正在取消；等待本次操作的子进程清理。启动脚本短事务会先完成收尾。")

    def close(self):
        if self.busy:
            self.closing = True
            self.cancel()
        else:
            self.window.destroy()

    def open_log(self):
        if self.log_file:
            try:
                if sys.platform == "win32":
                    os.startfile(self.log_file)
                else:
                    self.status.set("日志文件：" + self.log_file)
            except OSError as exc:
                self.status.set("无法打开日志：" + redact(str(exc)))


def run_gui(project):
    if tk is None:
        print("GUI unavailable: install a full Python with Tcl/Tk, or use install.bat / install.sh for the terminal installer.", file=sys.stderr)
        return 2
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        print("GUI unavailable: " + redact(str(exc)) + "; use install.bat / install.sh for the terminal installer.", file=sys.stderr)
        return 2
    DependencyManagerWindow(window, project)
    window.mainloop()
    return 0
