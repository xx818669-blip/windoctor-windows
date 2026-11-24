# 在 Windows 上比较两个 PowerShell 入口

需要 Windows、PowerShell 和可用的 Python 3.11 或更高版本。从仓库根目录运行命令。前三步只读取公开合成样本并在本机临时目录写 HTML，用不到 MCP 或第三方 Python 包。[样本说明](contracts/examples/README.md)

## 1. 准备样本

保持同一个 PowerShell 窗口打开，依次运行各段。先确认 Python 的版本，再创建一个新的报告目录。每次演练的报告会保存在不同的目录。

```powershell
python --version
$src = (Resolve-Path -LiteralPath '.\docs\contracts\examples').Path
$outDir = (New-Item -ItemType Directory -Path (Join-Path $env:TEMP ('windoctor-' + [guid]::NewGuid().ToString('N'))) -ErrorAction Stop).FullName
$outDir
```

样本中，`baseline.json` 是普通入口的基线（B），`target.json` 是出现问题的入口（T），`retest.json` 是从目标入口重新采集的快照（R）。

## 2. 比较两个入口

```powershell
python -B -m diff compare --baseline "$src\baseline.json" --target "$src\target.json" --format html --out "$outDir\compare.html"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$outDir\compare.html" -PathType Leaf)"
```

预期显示 `exit=0 saved=True`。用浏览器打开输出目录中的 `compare.html`。在 **Findings** 下能看到四项差异，其中普通入口有 `HTTP_PROXY` 且可以找到 `node`，目标入口则缺少该变量，也找不到 `node`。报告记录这两个入口的区别。要判断哪项影响使用，还要查故障命令的行为。

## 3. 在原目标入口复测

```powershell
python -B -m diff retest --baseline "$src\baseline.json" --original-target "$src\target.json" --retest-target "$src\retest.json" --format html --out "$outDir\retest.html"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$outDir\retest.html" -PathType Leaf)"
```

这次预期显示 `exit=3 saved=True`。打开 `retest.html`，查看 **Outcome** 列。

| 复测项 | 样本结果 | 如何理解 |
| --- | --- | --- |
| 工作目录 | `persists` | 目标入口的工作目录依然和基线不同。 |
| `PATH` 和 `HTTP_PROXY` | `no_longer_observed` | 两项在这次复测中均与基线一致。 |
| `node` 的搜索结果 | `unverifiable` | 复测时有一个目录不可访问，现有证据不足以确定能否找到 `node`。 |

比较报告中的 `unknown` 也表示缺少可靠观测。采集、比较和复测退出 `0` 表示结果完整，发现差异也可以退出 `0`。退出 `3` 表示结果不完整，报告中仍可能有可用观测。对于附带 `--out` 的命令，请同时确认退出码和本次文件是否写入。其他错误请查看终端里的 `E_...` 代码，例如目标文件已存在会返回 `E_OUTPUT_EXISTS`。

## 采集自己的快照

例如 `node --version` 在普通 PowerShell 正常，从 Codex 启动的 PowerShell 却找不到 `node`。先把两边能否运行这条命令记下来。两个入口都在仓库根目录执行下列采集命令，以便比较同一工作目录下的行为。采集只观测当前 Python 进程继承的环境。

**在普通 PowerShell 采基线。** 为本次诊断建立目录，创建一份由当前 Windows 用户保护的比较密钥文件（scope）。首次运行后保留它，相同案例的后续采集沿用这一份。

```powershell
$caseDir = [IO.Directory]::CreateDirectory((Join-Path $PWD ('artifacts\case-' + [guid]::NewGuid().ToString('N')))).FullName
python -B -m cli scope-init --out "$caseDir\trial.wdscope"
if ($LASTEXITCODE -ne 0) { throw 'Could not create the comparison scope.' }
python -B -m cli capture --context terminal --scope-file "$caseDir\trial.wdscope" --out "$caseDir\baseline.json"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$caseDir\baseline.json" -PathType Leaf)"
$caseDir
```

**在出现故障的 PowerShell 采目标。** 使用同一 Windows 用户、同一仓库和同一份 scope。将上一步打印的完整目录粘贴到提示处。若目标入口找不到 `python`，可将下面的 `python` 换成 `& '解释器的完整路径'`。

```powershell
$caseDir = (Resolve-Path -LiteralPath (Read-Host 'Full path to this case directory') -ErrorAction Stop).Path
python -B -m cli capture --context agent --scope-file "$caseDir\trial.wdscope" --out "$caseDir\target.json"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$caseDir\target.json" -PathType Leaf)"
```

如果故障出在本地 stdio MCP 服务，可改由目标客户端用同一份 scope 启动服务并调用 `capture_environment({})`，从成功响应中单独导出 `result.structuredContent.snapshot` 为 `target.json`。这条路径观测服务进程的环境。[MCP 配置和导出说明](../mcp/README.md)

**生成首轮报告。** 确认上述 B 和 T 都已写入后，在普通 PowerShell 中运行以下命令。如果同一条命令在两边表现不同，分别记下它的参数、退出状态和错误提示，复测时用相同参数再运行一次。

```powershell
python -B -m diff compare --baseline "$caseDir\baseline.json" --target "$caseDir\target.json" --format html --out "$caseDir\compare.html"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$caseDir\compare.html" -PathType Leaf)"
```

**验证调整效果。** 在原目标入口用原参数重跑故障命令，记录结果，再从同一入口采 R。以下示例沿用 CLI；若原目标由 MCP 采集，复测也由同一 MCP 入口采集、导出为 `retest.json`。如需重启目标以继承新设置，重开后重新设置 `$caseDir`。

```powershell
python -B -m cli capture --context agent --scope-file "$caseDir\trial.wdscope" --out "$caseDir\retest.json"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$caseDir\retest.json" -PathType Leaf)"
```

保留 B 和最初的 T。R 写入后，在普通 PowerShell 中生成复测报告。报告判断的是环境差异是否仍在，故障命令是否恢复以刚记录的执行回执为准。本机案例目录含 scope 和诊断数据，留在本机。

```powershell
python -B -m diff retest --baseline "$caseDir\baseline.json" --original-target "$caseDir\target.json" --retest-target "$caseDir\retest.json" --format html --out "$caseDir\retest.html"
"exit=$LASTEXITCODE saved=$(Test-Path -LiteralPath "$caseDir\retest.html" -PathType Leaf)"
```
