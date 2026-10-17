# Coating Sim 在线安装器

Windows 托管应用安装包固定为 3.12.10（最后传统官方完整安装包，非最新安全补丁）；优先复用已有较新的完整 Python 3.12。

未发布源码候选：Windows 默认安装 DirectML，应用环境优先 Python 3.12，沉积依赖按硬件选择 CUDA12/13。
详情及验证边界见 [硬件选择规则](docs/hardware-cuda-selection.md)；已发布 alpha.1 EXE 不包含这些改动。

[English](README.en.md) · [下载 Windows x64 单文件 EXE](https://github.com/ii33/coating-sim-installer/releases)

独立的标准库 Tk 安装器，为公开项目 [ii33/coating-sim](https://github.com/ii33/coating-sim) 准备源码、完整 Python、Git、隔离依赖环境和启动入口。本仓库不包含镀膜算法、项目数据或预装的科学计算依赖。

## 使用

1. 从本仓库 Release 下载 `CoatingSimInstaller-v0.1.0-alpha.1-windows-x64.exe`，核对 `SHA256SUMS.txt` 后双击。EXE 自带运行时与 Tcl/Tk，启动界面不要求预装 Python。
2. 选择安装目标；默认 `%LOCALAPPDATA%\CoatingSim`。来源默认 [CNB](https://cnb.cool/nonereason/coating-sim)，也可选公开 GitHub。镜像加速仅切换受支持的 PyPI 镜像；开发环境为可选项。
3. “检查 / 准备 Python”会优先复用兼容的完整 CPython。已注册但不可用的同 minor Python 仍会阻止重复安装，并显示具体原因；不会维护或破坏已有 Python。
4. 点击“安装”，分别核对 Python/Git 及完整项目依赖计划后确认。项目放在 `<目标>/app`，依赖仅安装到 `app/.venv`。使用生成的 `launch-coating.cmd`；启动入口保留原图标，快捷方式按计划显式创建且不覆盖既有文件。

安装器内置 Python 只运行界面和安装逻辑，永远不作为创建项目 venv 的解释器。项目使用另行发现/经确认安装的正常完整 CPython。项目文件、运行时、缓存及日志都在选定目标内；官方 Python 安装器还会建立其标准用户级维护记录。PyInstaller 临时解包目录只保存随 EXE 解出的只读资源，不保存项目、下载或日志，也不能选作安装目标。

EXE 是未签名的 alpha 预发布。它完成了离线回归、实际单文件启动/Tk/资源自检及既有 Python 的隔离 venv 验证；没有把先前失败的零 Python 沙箱端到端验收算作通过。完整真实下载、安装、取消/恢复及科学计算仍需独立验收。

## 检查更新

“检查更新（只读）”分别显示安装器与所选项目的本地/最新版本、来源和更新可用性。

- 安装器支持 `stable` 和 `preview` 渠道，按 SemVer 比较版本，不按字符串排序；草稿及不属于渠道的预发布会被排除。预发布安装器默认检查 `preview`。
- 安装器有新版时，仅启用“打开安装器新版下载页”。点击才打开经校验的本仓库 Release 页面；请核对新版 Release 的 SHA256。不会自动下载、替换或覆盖运行中的 EXE。
- 项目检查比较版本文档与 main 的确切 commit；`dev.YYYYMMDD.NN` 按日期/数字比较。同版本不同 commit 不声称更新或领先。CNB 的版本文档只从 GitHub 上**相同 SHA**的公开副本读取，无法核验时显示未知，不替换成 GitHub main。
- 检查不 fetch、checkout 或安装。dirty 项目明确提示保留本地修改，绝不覆盖。离线、超时、API 限流及无发布均显示独立结果。

## 源码入口与构建

保留 `install-gui.bat` / `sh install-gui.sh` 以及终端 `install.bat` / `sh install.sh`。源码 GUI 需要带 Tcl/Tk 的 CPython 3.10–3.13；Windows bat 使用原生 PowerShell 5.1，引导脚本不修改执行策略。

在 Windows x64、完整 CPython 3.13 环境中创建**新的**构建 venv，然后：

```powershell
python -m pip install -r requirements-build.txt
python -m pytest -q
python -m PyInstaller --noconfirm --clean installer.spec
```

PyInstaller 固定版本及完整构建依赖见 `requirements-build-lock.txt`。`installer.spec` 只包含标准库、Tk、清单、图标及许可证。Python/Git 清单读取模块旁的只读资源；启动外部 Python/Git 时清除 onefile DLL 搜索路径与提取目录环境，随后恢复安装器自身上下文。

离线 EXE 自检（输出必须是正常绝对路径）：

```powershell
.\CoatingSimInstaller-v0.1.0-alpha.1-windows-x64.exe --self-check --self-check-output C:\Temp\installer-check.json
```

`--smoke-gui` 仅短暂显示窗口后退出，不安装软件。开发验证选项 `--verify-venv <新目录> --python <完整CPython.exe> --self-check-output <JSON>` 只在指定新目录创建不含 pip 的 venv；不安装项目依赖或连接网络。

仓库根部的项目 requirements 声明用于继承的依赖分组合同与测试；实际安装以下载项目中经过确认的声明为准，安装器本身不依赖这些科学计算包。更多事务边界见 [合同](docs/dependency-manager-contract.md)。

## 许可证

安装器源码沿用 [MIT](LICENSE)。原 ICO 与安装逻辑来自公开 coating-sim 候选源码；保留原版权说明。发行文件包含 CPython、Tcl/Tk、OpenSSL 及 PyInstaller 许可与 bootloader exception，见 `tools/dependency_manager/licenses`。第三方运行时不因源码的 MIT 许可证而重新授权。

PyInstaller 参考：[运行时资源路径](https://pyinstaller.org/en/stable/runtime-information.html)、[启动外部程序的环境处理](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)。
