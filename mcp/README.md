# windoctor 本地 stdio MCP

`capture_environment` 采集 MCP 服务进程实际继承的 Windows 环境，返回脱敏快照。需要比较普通 PowerShell 和 MCP 入口时，先在普通 PowerShell 里用 CLI 保存基线，再让宿主启动本服务。两端使用同一份 scope 文件。[Windows 快速上手](../docs/WINDOWS-QUICKSTART.md)

## 安装可选的 MCP 依赖

在仓库根目录打开 PowerShell。准备好 Windows x64 的 CPython 和 `py` 启动器，按已安装的版本**任选一组**。两组都将所选解释器的隔离环境建在 `mcp/.venv`，从全新检出开始执行，无须激活环境。CLI 与 Diff 可以直接使用 Python 标准库。

**CPython 3.11 x64**（锁文件验证版本 3.11.17）

```powershell
py -3.11-64 -m venv .\mcp\.venv
if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python 3.11 environment.' }
& .\mcp\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r .\mcp\requirements-py311-win_amd64.lock
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the locked Python 3.11 wheels.' }
```

**CPython 3.14 x64**（锁文件验证版本 3.14.2）

```powershell
py -3.14-64 -m venv .\mcp\.venv
if ($LASTEXITCODE -ne 0) { throw 'Failed to create the Python 3.14 environment.' }
& .\mcp\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: --require-hashes -r .\mcp\requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Failed to install the locked Python 3.14 wheels.' }
```

安装命令从 PyPI 下载并核对锁定的 wheel。已有离线 wheel 目录时，用 `--no-index --find-links <wheel目录>` 替换 `--index-url https://pypi.org/simple`，其余参数照旧。[依赖及许可证来源](../docs/mcp/DEPENDENCIES.md)

## 配置 stdio 服务

在仓库根目录准备本机诊断目录和 scope。以下示例为新一轮诊断生成专用目录。如果已经保存基线，请把 `$scope` 指向生成该基线时用过的 scope，跳过创建步骤。

```powershell
$repo = (Get-Location).Path
$python = (Resolve-Path -LiteralPath '.\mcp\.venv\Scripts\python.exe').Path
$entry = (Resolve-Path -LiteralPath '.\mcp\run_stdio.py').Path
$caseDir = Join-Path $repo ('artifacts\mcp-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $caseDir -Force -ErrorAction Stop | Out-Null
$scope = Join-Path $caseDir 'case.wdscope'
& $python -B -m cli scope-init --out $scope
if ($LASTEXITCODE -ne 0) { throw 'Failed to create the scope.' }
```

如需检查入口，运行 `& $python -I -B $entry --help`。帮助写入 stderr，退出码为 0。正式使用时，由目标宿主以 **stdio 子进程**启动服务。将下面的输出填入客户端对应的 command 和 args 字段。客户端应将各参数分别传给进程。

```powershell
[ordered]@{
    command = $python
    args = @('-I', '-B', $entry, '--scope-file', $scope)
} | ConvertTo-Json
```

服务只在启动时读取一次 scope。宿主必须以能够读取该文件并通过 DPAPI CurrentUser 解密的 Windows 用户运行。将 scope 保存在本机，提供给宿主的是路径。要捕获宿主调整后的环境，请让该入口重新启动服务。启动参数错误返回 2，依赖缺失返回 4，其它启动或运行错误返回 5。诊断信息只以固定事件写入 stderr，stdout 专供协议使用。

## 调用、导出与复测

1. 让本地客户端列出工具，调用 `capture_environment`，arguments 传 `{}` 或直接省略。显式 `null`、数组和标量返回协议错误；对象里的额外键返回工具错误。
2. 由可信的本地客户端核对响应对应本次请求，`isError` 为假或省略，结果的 `structuredContent.ok` 为 `true`，并且唯一文本项中的 JSON 与 `structuredContent` 一致。核对通过后，将 **`structuredContent.snapshot`** 作为独立 JSON 保存到本地新文件 `target.json`。快照的 `completion` 记录采集完整度。服务没有文件导出工具。
3. Python 客户端可调用 `cli.shared.json_io.serialize_document(snapshot, "snapshot")` 验证并序列化快照，用 `check_output_aliases` 核对输出路径与既有输入文件，再用 `write_output(data, path, "snapshot-json")` 独占创建文件。输出父目录须已存在。调整后，从同一目标入口采集到另一个新文件，再做离线复测。[CLI 比较与复测](../docs/WINDOWS-QUICKSTART.md)

## 运行限制

同一服务只有一个采集槽。额外调用返回 `A_BUSY`。采集等待 10 秒后返回 `A_TIMEOUT`，取消请求会抑制未发出的结果。底层 Windows 调用结束后才会释放采集槽。宿主可使用 15 秒等待上限及 2 秒关闭宽限。入站帧含 LF 上限 64 KiB，核心快照上限 1 MiB，完整出站帧上限 4 MiB。[工具契约](../docs/mcp/ADAPTER.md) · [协议与错误](../docs/mcp/PROTOCOL.md)
