# windoctor

同一台 Windows 电脑上，`node --version` 在普通 PowerShell 里能运行，从 Codex 启动的 PowerShell 却提示找不到 `node`。windoctor 可以分别记录两个入口看到的环境，比较命令搜索路径、代理设置和工作目录，等你调整环境后再从原入口复测。

## 先看一份报告

在 Windows PowerShell 中打开仓库根目录，使用 Python 3.11 或更高版本运行下面一行。它只读取仓库自带的合成快照，使用 Python 标准库。

```powershell
python -B -m diff compare --baseline "$PWD\docs\contracts\examples\baseline.json" --target "$PWD\docs\contracts\examples\target.json" --format text
```

这个样本有四项差异。在普通入口能找到 `node`，在目标入口找不到它。另三项是工作目录、`PATH` 和 `HTTP_PROXY`。跟着快速上手再生成一份 HTML 复测报告，可以看到 `PATH` 恢复后，为什么仍需要核对 `node` 能否运行。[Windows 快速上手](docs/WINDOWS-QUICKSTART.md)

## 排查自己的问题

1. 在正常的 PowerShell 采一份基线快照，在发生故障的入口采目标快照。两次使用同一份比较密钥文件（scope），快照只记录采集进程自己能观察到的信息。
2. 离线比较两份快照。核心报告检查工作目录、`PATH`、`PATHEXT`、代理变量和 `node`、`npx`、`codex`、`python` 的可执行文件搜索结果。请结合两个入口的实际执行结果，判断哪些差异与故障有关。
3. 调整环境后，保留最初的两份快照，从原目标入口再采一份。复测报告逐项显示哪些差异消失、哪些仍在、哪些缺少证据。要确认故障是否解决，请在目标入口用原参数运行原命令。

采集和比较均在本地完成，报告可保存为 JSON、文本或离线 HTML。环境差异能缩小排查范围，具体根因仍需结合故障命令验证。[采集、比较与复测](docs/WINDOWS-QUICKSTART.md)

## 本地 MCP 入口

本地 stdio MCP 可采集服务进程继承的环境。已在 Windows 10 x64、Windows PowerShell 5.1 上验证终端采集、MCP 采集和离线复测。安装方法、启动参数和快照导出见 [MCP 使用说明](mcp/README.md)。

[许可证](LICENSE)
