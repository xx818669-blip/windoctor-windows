# MCP 协议及 SDK 来源

以下链接对应适配器使用的协议版本和 Python SDK v2.3.0。协议语义参考官方规范，SDK 实现细节参考固定 tag 或 commit。

| ID | 来源 | 与适配器相关的内容 |
| --- | --- | --- |
| P1 | [协议版本说明](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning) | 2026-07-28 和 2025-11-25 的协议版本说明 |
| P2 | [Versioning](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning.md) | 现代逐请求版本/capabilities，无初始化握手；双时代是显式兼容选择 |
| P3 | [stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio) | 换行JSON-RPC、stdout纯协议、取消后不再发该请求消息、EOF/客户端终止规则 |
| P4 | [Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) | tools能力、空对象参数、schema方言、structuredContent与工具/协议错误分层 |
| P5 | [Discovery](https://modelcontextprotocol.io/specification/2026-07-28/server/discover) | modern服务实现server/discover、客户端可直接请求 |
| P6 | [Cancellation](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation) | 客户端取消通知与请求时限；通用页措辞与stdio严格规则需分别看 |
| P7 | [旧生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) | legacy初始化路径，不能套用为modern要求 |
| S1 | [v2.3.0 release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0) | v2.3.0 发布记录 |
| S2 | [精确commit](https://github.com/modelcontextprotocol/python-sdk/commit/2118f14f8a19bc158d8a1cf90af58d85d187f849) | v2.3.0核验ref |
| S3 | [tag pyproject](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/pyproject.toml) | Python>=3.10、MIT、动态声明的直接运行依赖及条件 |
| S4 | [版本注册表](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/version.py) | modern2026-07-28和四个legacy版本 |
| S5 | [低层Server](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/lowlevel/server.py) | on_list_tools/on_call_tool及公开stream/run入口 |
| S6 | [低层说明](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/advanced/low-level-server.md) | arguments/output schema由应用负责校验 |
| S7 | [stdio源码](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/stdio.py) | 默认errors=replace、流接管/自定义流差异，不等于严格边界已满足 |
| S8 | [dispatcher](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/shared/jsonrpc_dispatcher.py) | 取消可抑制响应；legacy非预期异常兜底可能回显str(e)和日志 |
| S9 | [双时代runner](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/runner.py) | 首帧选择时代，不能把所有连接称握手 |
| S10 | [SDK取消说明](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/handlers/cancellation.md) | 异步取消与同步线程的合作性取消；没有强杀Win32调用保证 |
| S11 | [结构化输出](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/servers/structured-output.md) | Python snake_case与wire字段不同，需按版本模型输出 |
| S12 | [v2变化](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/whats-new.md) | FastMCP→MCPServer，不使用旧示例导入 |
| S13 | [CallToolRequestParams](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp-types/mcp_types/_types.py#L1321-L1324) | SDK允许arguments省略/null，非对象在handler前被类型校验拒绝；项目必须另行区分显式null |
| S14 | [legacy OutputSchema](https://github.com/modelcontextprotocol/python-sdk/blob/2118f14f8a19bc158d8a1cf90af58d85d187f849/src/mcp-types/mcp_types/_v2025_11_25/__init__.py#L1347) | 根级type为必需字段；oneOf分支的type不代替根字段 |
| S15 | [方法表与时代转换](https://github.com/modelcontextprotocol/python-sdk/blob/2118f14f8a19bc158d8a1cf90af58d85d187f849/src/mcp-types/mcp_types/methods.py) | modern没有ping；pre-2026统一按legacy时代模型转换，不等于逐日期精确裁剪 |
| J1 | [JSON-RPC 2.0](https://www.jsonrpc.org/specification) | 语法/外壳/参数错误分层，响应ID关联与无法确定时null；有效通知不回复。MCP对请求ID和批量另有限制 |
| S16 | [公开JSON-RPC类型](https://github.com/modelcontextprotocol/python-sdk/blob/2118f14f8a19bc158d8a1cf90af58d85d187f849/src/mcp-types/mcp_types/jsonrpc.py) | JSONRPCError.id允许None且必须输出null；入站须先按原始id存在性选具体类型，不能让联合类型将非法ID吞成通知 |
| S17 | [现代入站分类](https://github.com/modelcontextprotocol/python-sdk/blob/2118f14f8a19bc158d8a1cf90af58d85d187f849/src/mcp/shared/inbound.py#L435) | 已选modern时非字符串版本-32602、未知字符串-32022；后者标准data含requested，项目边界策略须与其相容 |
| S18 | [runner版本/初始化分支](https://github.com/modelcontextprotocol/python-sdk/blob/2118f14f8a19bc158d8a1cf90af58d85d187f849/src/mcp/server/runner.py#L472) | modern后的initialize先-32022；无字符串请求版本时只有supported；首请求锁时代及legacy跨时代guard详见PROTOCOL表 |

协议要求与项目自选数值分别标在PROTOCOL。单工具、64KiB/4MiB、10秒/15秒、单槽、禁止文件工具、固定错误和日志策略均为本项目约定，不冒充官方强制数值。

低层 dispatcher 的默认处理方式及适配器使用的固定错误响应见 [协议说明](PROTOCOL.md)。
