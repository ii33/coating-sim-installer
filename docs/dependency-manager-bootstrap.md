# Windows 源码入口与排障（alpha.2）

推荐使用 Release 的单文件 EXE：界面自带 Python/Tcl/Tk，应用始终使用安装目录内官方 NuGet Python3.12.10 x64。

普通 Windows 安装入口使用打包 EXE；`install.bat` 在 EXE 同目录时直接转交它。源码 `dependency_manager.cmd` 不执行 PowerShell 脚本：没有打包 EXE 时，只接受明确指定的 `COAT_DEPENDENCY_PYTHON` 或现有 `.runtime/python/python.exe`。安装阶段由 Python 管理器下载、校验并部署项目私有 NuGet 运行时。企业应用控制、下载策略或桌面写入策略仍可能阻止执行；安装器不会更改执行策略或关闭安全措施。

```text
dependency_manager.cmd -BootstrapProjectRoot C:\CoatingTest
dependency_manager.cmd -BootstrapProjectRoot C:\CoatingTest -CheckOnly
dependency_manager.cmd -Cli -BootstrapProjectRoot C:\CoatingTest runtime-plan --mirror pypi --json
```

这些源码 CLI 示例需先在当前进程设置 `COAT_DEPENDENCY_PYTHON` 为可信 Python 的完整路径；普通用户使用打包 EXE。旧 `.ps1` 保留为开发诊断资料，不再由 Windows 安装入口调用。

`-CheckOnly` 为只读核验，不进入下载。可显式用 `-BootstrapPython <完整Python路径>` 或 `COAT_DEPENDENCY_PYTHON` 指定承载源码安装器界面的工具解释器；它不会成为应用环境基础。源码 GUI 需要 Tcl/Tk，NuGet 包不含 Tk，未显式提供工具解释器时请使用已封装 GUI EXE。应用私有解释器与工具解释器的版本可以不同。

日志在 `<目标>/.dependency-manager/logs`。运行时临时目录及锁在 `<目标>/.runtime`；只清理本次创建的目录，不删除未知内容。重入要求所有原始文件与固定清单一致。若已有旧 `.venv` 依赖系统 Python，保留旧环境并选择新目录。

“Runtime download exceeded the 180-second bound”是 alpha.1 的整体下载时限错误，发生在安装器启动之前。alpha.2 改为连接/响应头10秒、无数据读取15秒，有数据可持续下载。运行时镜像失败会回退官方源，仍需固定hash。网络完全停滞可取消或在有界重试结束后重试。哈希失败、ZIP路径不安全或超限文件不会运行。

清华 TUNA 是首次包索引默认；已有审查记录和明确选择优先。依赖镜像失败时重新审查计划，CLI `--mirror pypi` 切换官方源；不会修改全局 pip 配置。代理仅作用当前进程（`-BootstrapProxyMode http -BootstrapProxy http://127.0.0.1:10808`，或受支持环境变量），不写系统设置，不在参数中传密码。

实际验收在原生独立目录执行，不需要 Windows Sandbox、容器或虚拟机。DirectML 需 Python3.12 x64 与固定 torch 组合；设备探针失败会给出独立诊断，不能把安装成功解释为所有 GPU 功能合格。
