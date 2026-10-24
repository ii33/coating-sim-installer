# Coating Sim Installer

[English](README.en.md) · [Windows x64 发布](https://github.com/ii33/coating-sim-installer/releases)

独立在线安装器，为公开项目 [ii33/coating-sim](https://github.com/ii33/coating-sim) 部署源码、项目私有 Python、依赖和启动脚本。安装器自身的 Python/Tcl/Tk 与应用 Python 分开；双击 EXE 不要求预装 Python。

## alpha.11 使用方式

1. 下载 `CoatingSimInstaller-v0.1.0-alpha.11-windows-x64.exe`，核对同一 Release 的 `SHA256SUMS.txt` 后启动。
2. 安装目录默认是 EXE 所在目录；从源码运行时默认是入口脚本所在目录。可以在界面输入或浏览另选目录，明确传入的 `--project` 也优先于默认值。选择源码来源（默认 CNB，可切换 GitHub）。清华 TUNA PyPI 默认启用；取消镜像加速使用官方源。已有项目记录的源及明确选择优先，不修改系统或用户全局 pip 配置。
3. 确认计划后，安装器将 **CPython 官方 NuGet Python 3.12.10 x64** 解压到 `<目标>/.runtime/python`，只用该解释器创建 `<目标>/app/.venv`。不扫描或复用系统 Python，不运行 Python EXE/MSI，不改注册或 PATH。
4. 完成依赖安装和安装后检查后，用生成的 `app/launch-coating.cmd` 启动；该脚本只使用项目 `.venv`。已有资源、未知运行时和不属于项目私有 Python 的旧环境均保留，需要另选目录。

“检查 / 准备 Python”仅部署或核验本项目私有运行时。已由本工具部署且完整清单未变化的运行时可安全重入，不重复破坏重装。

## 下载、镜像及兼容性

Python 运行时镜像与 PyPI 包索引是两种来源。启用镜像加速时，Python 使用已实际比对的华为云 **NuGet** 镜像；下载或校验失败安全回退官方 NuGet。两个来源均须匹配固定的官方制品 SHA256，不能接受未知镜像、关闭 TLS 或改用 GitHub 任意代理。持续收到数据时允许超过 180 秒；连接/响应头等待 10 秒、无数据读取 15 秒，每来源最多两次，从头重试，可取消。保留 128 MiB 下载上限、256 MiB 解压上限、安全路径预检及校验后原子发布。

包索引默认清华 TUNA；CLI 可用 `--mirror pypi` 明确切换官方 PyPI，或选 `ustc`。包索引失败时显示原因并要求重新确认计划，不静默混用索引。配置只作用于项目命令及已审查项目计划。

Windows 桌面固定支持 DirectML 的 Python 3.12 x64，`torch-directml==0.2.5.dev240914` 对应 `torch==2.4.1`。CUDA12/13 根据设备、驱动和 wheel 判断；硬件不可用时保留 CPU。最小数值检查不代表全仿真、精度或性能合格。见[硬件策略](docs/hardware-cuda-selection.md)。

**已知限制：**私有运行时目前只提供 Windows x64。固定 3.12.10 并非最新 3.12 安全补丁。官方 NuGet 包包含 venv/pip，不含 Tcl/Tk；这不影响 EXE 自带的安装界面或项目 Qt 界面。源码 GUI 可显式指定带 Tcl/Tk 的解释器来承载安装器界面，应用仍使用项目私有运行时。不会自动修改已有不兼容环境。

## 源码入口和构建

Windows 普通用户运行发行包的 `CoatingSimInstaller-*-windows-x64.exe`，或同目录的 `install.bat`。不要求开发者模式、PowerShell 7、脚本执行策略变更或预装 Python。源码 CMD 入口在没有打包 EXE 时仅使用明确指定的 `COAT_DEPENDENCY_PYTHON` 或已有项目私有运行时；没有这些入口时会明确退出。

在独立构建环境运行：

```text
python -m pip install -r requirements-build.txt
python -m pytest -q
python -m PyInstaller --noconfirm --clean installer.spec
```

`--self-check` 和 `--smoke-gui` 为不安装的原生诊断。运行时和包安装均需联网；项目数据、下载临时目录和日志留在所选目录内，诊断输出必须在 EXE 临时解包目录之外。历史版本文件保留，新版本使用独立版本标签。

[部署与排障](docs/dependency-manager-bootstrap.md) · [下载策略](docs/private-python-runtime.md) · [许可](LICENSE)

MinGit binary downloads use the Huawei Cloud mirror first when mirrors are enabled, with official GitHub fallback. Both sources must match the same pinned official SHA-256. Choosing PyPI disables this mirror too. Project source selection is unchanged.

## alpha.11 更新

默认界面提供安装路径和一键安装，详细设置可展开。更新按钮检查并更新所选源的主程序 main；安装成功后创建并验证当前用户的桌面快捷方式，快捷方式失败不影响安装/更新结果，会显示醒目警告并可单独重试创建。完成、已是最新版、失败和取消均显示明确结果，清理结束后可直接退出安装器。

Git 软件本体的 MinGit 下载默认优先使用华为云镜像，失败时回退官方源；两者校验同一固定 SHA256。此设置与项目源码 CNB/GitHub 的选择独立。

桌面快捷方式与应用任务栏重启标识统一使用 `logolite.ico`；图标路径或内容变化时，原子刷新经验证的安装器自有快捷方式，并保留用户修改及无关快捷方式。Windows 回执测试显式使用 UTF-8，避免依赖系统默认编码。

[安装性能与验证](docs/installation-performance.md) · [主程序更新与桌面快捷方式](docs/project-update-and-desktop.md) · [完成与退出状态](docs/terminal-outcomes.md)
