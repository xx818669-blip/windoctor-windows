# MCP 运行依赖

MCP 适配器通过官方 Python SDK 的低层 Server 及 stdio 接口处理协议，使用 AnyIO 管理异步流、取消及超时。CLI 和 Diff 使用 Python 标准库。[SDK release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0) · [低层接口](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/advanced/low-level-server.md)

| Windows x64 解释器 | wheel 锁文件 |
| --- | --- |
| CPython 3.11，验证版本 3.11.17 | [requirements-py311-win_amd64.lock](../../mcp/requirements-py311-win_amd64.lock) |
| CPython 3.14，验证版本 3.14.2 | [requirements.lock](../../mcp/requirements.lock) |

两份锁文件记录各自依赖的完整包名、版本和 SHA-256。直接依赖声明为 `mcp==2.3.0` 和 `anyio>=4.10,<5`，两份锁均选用 `anyio==4.16.0`。从仓库根目录创建对应 Python 版本的隔离环境，按照锁文件校验 wheel 后安装。[安装命令](../../mcp/README.md) · [直接依赖](../../mcp/requirements.in)

SDK 自身声明了 mcp-types、Pydantic、JSON Schema 校验库、Windows 支持库以及 HTTP、OAuth、遥测相关依赖。本适配器只启动本地 stdio 服务。SDK 固定版本使用 MIT 许可证，传递依赖各自使用其上游许可证。[SDK 项目元数据](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/pyproject.toml) · [mcp-types 元数据](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/pyproject.toml) · [SDK 许可证](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/LICENSE)
