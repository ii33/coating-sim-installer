## v0.1.0-alpha.9

Windows x64 onefile installer update. Desktop shortcut failure now leaves a completed install or verified project update intact, shows the specific warning, and offers a shortcut-only retry. Windows install entry and Shell Link creation no longer depend on PowerShell; the shortcut uses native Windows APIs.

The EXE passed a frozen self-check, GUI startup and a temporary Shell Link smoke test. A real installation on the affected machine was not repeated. Unsigned prerelease; compare the downloaded EXE with SHA256SUMS.txt.

首个独立在线安装器 alpha：Windows x64 单文件 EXE，自带界面运行时，无需为启动安装器预装 Python。

- 保留 CNB 默认 / GitHub、PyPI 镜像加速、开发环境、启动脚本与快捷方式计划以及原 ICO。
- 修正已有同 minor Python 被误呈现为安装失败：发现兼容完整解释器时优先复用，项目依赖进入独立 `.venv`；不可用注册项仍安全阻止重复安装。
- 分别检查安装器和项目更新，显示本地/最新版本、来源与更新可用性。支持正式/预发布渠道、离线/超时/限流处理；检查不改变项目。有安装器更新时仅在点击后打开验证过的官方 Release 下载页面，不覆盖运行中的 EXE。
- onefile 资源只读，项目/日志/下载使用所选目标；外部 Python/Git 不继承 frozen DLL 搜索路径。

验证：离线回归 409 passed / 3 skipped / 71 subtests；最终 GUI/新增功能单独复核 48 passed。最终 EXE 的离线 Tk/资源自检、短暂原生 GUI 启动、实际复用外部 CPython 创建隔离 no-pip venv 均通过。详见 TESTING.md。

边界：未完成零 Python 机器的全流程安装与取消恢复验收，未验证科学计算；不将旧沙箱失败结果称为通过。EXE 未签名，作为 alpha 预发布。

下载 EXE 后请核对同页 SHA256SUMS.txt。源码与构建配置位于此独立仓库，原 coating-sim 项目和用户 Downloads 文件均未修改。

---

First standalone Windows x64 onefile alpha. Includes its own GUI runtime, compatible external-Python reuse, isolated project venvs, CNB/GitHub source choices and read-only installer/project update checks. Updates open a validated Release page only after a click; no automatic self-replacement.

Local regression: 409 passed, 3 skipped, 71 subtests; fresh final GUI/new-feature suite: 48 passed. Native EXE resource/Tk and GUI startup checks plus external-CPython no-pip venv creation passed. No completed clean zero-Python end-to-end installation or scientific computation acceptance is claimed. Unsigned prerelease; verify SHA256SUMS.txt.
