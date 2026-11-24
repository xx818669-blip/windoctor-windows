# windoctor MCP 工具契约

适配器版本 `mcp-adapter/0.1.0`，快照契约版本 `core/1.0.0`。

服务的启动和导出方式见 [MCP 使用说明](../../mcp/README.md)，错误和传输规则见 [协议说明](PROTOCOL.md)。

## 适配范围

本地 stdio 服务采集自身进程的环境。比较、复测、文本及 HTML 报告由 CLI/Diff 在本机生成。

## 唯一工具

| 项目 | 冻结约定 |
| --- | --- |
| name | capture_environment（区分大小写） |
| 参数 | 只能是空对象 `{}`；省略 arguments 归一为 `{}`；显式null/数组/标量为协议参数错误；对象内额外键为工具参数错误 |
| inputSchema | 显式 JSON Schema 2020-12，type=object，properties={}，additionalProperties=false，maxProperties=0 |
| 禁止参数 | path/out/scope/scope_file/context/env/command/timeout，以及全部未声明键 |
| annotations | readOnlyHint=true、destructiveHint=false、idempotentHint=false、openWorldHint=false；这些是提示，不是权限边界 |
| 固定顺序 | tools/list 始终只有此一项；无分页nextCursor，无动态工具变更通知 |
| 不提供 | run_command/find_exec/fix/compare/retest、resources、prompts、sampling、elicitation、subscriptions、Tasks、Apps及网络传输 |

tools/list 可在采集工作槽繁忙时响应；capture 请求没有排队，只有一个工作槽。未知工具或arguments类型无效属于协议错误；已知工具的合法对象内额外键属于工具错误，详见 PROTOCOL。

## 启动与复用

宿主使用隔离环境的 Python 运行 `-I -B <repo>/mcp/run_stdio.py --scope-file <absolute-local.wdscope>`。scope 来自受信启动配置。项目包名为 `windoctor_mcp`，SDK 包名为 `mcp`。[安装与配置](../../mcp/README.md)

启动先设置安全日志边界和参数检查，再只读调用 `load_scope(path)` 一次。失败时 stdout 无协议或诊断内容，stderr 仅固定安全启动错误；不创建/轮换 scope、不明文回退、不自动修改客户端配置。加载完成后再接受协议请求。

唯一业务调用链：`capture('mcp', startup_scope)` → `validate_snapshot(snapshot)` → `serialize_document(snapshot, 'snapshot')` → 适配信封和协议输出。无 shell/CLI 子进程、不调用 writer、不写报告、不修改 cwd/os.environ、不读取认证或完整配置。依赖 SDK 和适配层前后出现的异常同样经过安全边界。

## 来源与 scope 生命周期

- 声明固定 mcp，observed_via 固定 current_process；真实观察者就是服务所在进程，采集通过同进程线程，不另启采集子进程冒充服务来源。
- 同服务多次采集的新 snapshot_id 不同，process_instance_id 复用；不证明读取后台宿主内部环境，也不证明宿主环境刚被重新继承。
- scope_id 与启动 scope 相同；磁盘 scope 删除后，进程已载入的密钥不会自动撤销。更换 scope 或验证新的继承环境须由用户/宿主重新启动服务。
- 仅支持能够读取并解密同一 CurrentUser scope 的入口；失败即安全停止，无权限绕过或环境修复。未实测的 Codex/沙箱跨入口支持不能据此宣称通过。

## 输出与核心模型

structuredContent 使用适配器自己的严格联合信封，不修改 core 快照：

```json
{"ok":true,"snapshot":{"schema_version":"windoctor.snapshot/1"}}
```

上例只展示信封位置，完整快照须满足原 schema 与语义。失败信封为 `{"ok":false,"error":{"origin":"core|adapter","detail":{...}}}`；具体 schema 和完整合成例放在 fixtures，不把省略例当有效结果。

- 成功时 core 的 completion 无论 complete/partial/failed，都保留原值，isError=false；这表示完成观测调用，不等于环境健康或证据完整。
- core SafeError 的 detail 仅复制其 as_dict()，origin=core；适配器故障使用独立A_*枚举，绝不传给core SafeError构造器或扩展核心枚举。
- 错误输出 isError=true，structuredContent 必须匹配错误分支。一个 TextContent 只含同一信封的紧凑JSON，不添加原始日志、路径、凭证或自由文本异常。
- 客户端要交给 Diff 的是成功信封中的 snapshot，并按原核心的明确输出流程导出；整个信封不是 Snapshot，不能直接作为 Diff 输入。MCP 服务本身不提供导出文件工具。
- 结构化和文本副本必须一致；无图像、资源链接、HTML或额外_data。modern 的 resultType=complete 与 core.completion 是不同层级；SDK对legacy按2025-11-25时代模型转换，不手工塞入现代字段，也不声称逐历史日期精确裁剪。

## Schema 方言与大小

MCP input/outputSchema 显式使用 2020-12。联合outputSchema的根级固定`type: object`，满足SDK legacy OutputSchema的必需字段；oneOf成功/错误分支和独立/内嵌schema须一致。核心 Draft-07 schema 文件保持不变；适配输出 schema 的 snapshot 分支仅作机械投影：definitions→$defs、重定位本地$ref、删除核心$schema声明。本次仅投影的 snapshot.schema.json 没有需改写的tuple-items/dependencies等关键字；comparison/retest并非如此，也不在投影范围。发现新的不兼容关键字必须停止生成并复核，不能静默改变语义。

运行期仍以 core validate_snapshot 和 serialize_document 为权威，适配 schema 不另写一套核心业务校验器。fixtures 中记录核心来源哈希，投影需与全部核心Snapshot样例及对应snapshot正负结构向量作双向一致性检查；其它模型不计入capture投影通过数。

项目限额（不是 MCP 标准强制数值）：入站每行最多64 KiB（含LF）、深度16、单字符串4096码点、对象128键；严格UTF-8无BOM、拒绝重复键/非法surrogate/非有限数/批量数组。必须在SDK解码前限制字节和执行有界预检；不能先用默认无限readline或errors=replace解码再声称完成。

core 快照序列化仍最多1 MiB。加入structuredContent、转义后的JSON文本副本及协议封装后，整个出站行最多4 MiB，先完整序列化计字节，再写一行。超限返回A_OUTPUT_LIMIT小错误，不能截断快照、丢掉文本副本或输出半条正常消息。
