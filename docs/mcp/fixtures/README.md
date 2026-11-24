# MCP 合成适配材料

数据材料版本 `mcp-adapter/0.1.0`，最终契约冻结由总控记录；状态 data-only，不代表运行验收通过。全部内容为公开、确定性的合成材料，没有运行 MCP 服务、SDK、真实 scope 或 DPAPI。

| 文件 | 用途 |
| --- | --- |
| [capture.input.schema.json](capture.input.schema.json) | 2020-12，仅接受空对象参数 |
| [capture.output.schema.json](capture.output.schema.json) | 2020-12，成功快照与安全错误的严格联合信封 |
| [capture.tool.json](capture.tool.json) | 唯一工具及完整内嵌 input/outputSchema、四个 annotations |
| [capture-request.modern.json](capture-request.modern.json) | 2026-07-28 tools/call，含每请求版本、能力及虚构 clientInfo |
| [capture-result.modern.json](capture-result.modern.json) | 合成成功响应；core baseline 只改 declared_context=mcp，原公开 token 不变 |
| [capture-error.modern.json](capture-error.modern.json) | 合成 A_BUSY 错误响应，origin=adapter、stage=dispatch、pointer=/request |
| [projection-manifest.json](projection-manifest.json) | 原 schema 哈希、纯投影哈希、内嵌一致性及安全错误数据来源 |

契约依据为 [ADAPTER](../ADAPTER.md)、[PROTOCOL](../PROTOCOL.md)，核心依据为 [MODEL](../../contracts/MODEL.md)。[review-vectors.json](../review-vectors.json) 共 145 项，覆盖参数、错误边界、五个协议版本、取消/超时单槽、来源/scope、包名冲突与投影。

## 结构与协议语义

schema 约束的是工具 arguments 或 structuredContent，不能单凭它验证 JSON-RPC 封装、isError、文本副本一致性、消息大小或实际来源。成功快照的 complete/partial/failed 均对应 ok=true 和 isError=false；resultType=complete 表示调用结束，与 core.completion 分层。

只有省略 arguments 才归一为空对象；显式 null、数组、标量为 JSON-RPC -32602，不能变为 A_ARGUMENT。SDK 可能在 handler 前拒绝数组/标量；显式 null 与省略须按公开 ctx.params 的字段存在性区分。仅合法对象内的额外键返回 isError=true、adapter A_ARGUMENT。参数 schema 的结构接受/拒绝结果不因此改变。

短完整帧的普通 JSON 语法错误由适配边界返回 -32700/Parse error/id:null；合法 JSON 的无效 RPC 外壳返回 -32600/Invalid Request，合法字符串/整数 ID 可保留，bool/null 等非法或缺失 ID 为 null。先检查原始 ID 字段是否存在，不能让 Notification 模型吞掉非法 ID；有效无 ID 通知的参数畸形或未知通知不回复、不调用 capture。无法安全分类的响应/请求响应混合以及安全限额拒绝关闭连接；这些边界拒绝不送 runner、不选择时代。统一 writer 必须保留显式 id:null。

时代由首个送入 SDK 的 JSONRPCRequest 选择，并非由通知或首次成功决定。首个无版本 discover 走 legacy -32601；首个无版本 tools/list 走未初始化 legacy -32602。已锁 modern 的 ping 有合法元数据为 -32601，缺元数据先 -32602；initialize 优先 -32022。已锁 legacy 的非 initialize 请求含现代版本键为 -32600；legacy initialize 提议现代版本仍回退握手版本 2025-11-25。非日期自由文本版本由边界 -32602 且不选时代，检查同时覆盖 _meta 版本键和 initialize 的 params.protocolVersion。null/数字元数据版本有能力字段时先选 modern 再 -32602；未知日期元数据则 -32022，必须保留 supported 与 requested。已锁 modern 的 initialize 特殊错误若参数版本为日期字符串也保留这两项，若缺失/非字符串则仅保留 supported，不凭空补 requested。以上是 G0 设计预期，不是 SDK 运行证据。

成功和错误响应各有一个 TextContent，其 text 是同一信封的紧凑 JSON。这里的现代请求/结果文件为便于审阅而采用缩进排版，**不能原样作为 stdio 多行帧发送**；真正出站须先紧凑序列化整个 JSON-RPC 对象，再追加单个 LF，并检查完整行大小。此规则的静态数据关系可离线检查，不能替代 SDK/Windows stdio 实测。

## 核心投影

原 core schema 不修改。仅删除其根 $schema、definitions→$defs、重定位本地引用；遇到 tuple-items、dependencies、未知关键字或新方言即停止生成。纯投影在 $defs.coreSnapshot，保持核心允许的来源枚举；工具成功分支通过单独 allOf 将 declared_context 收窄为 mcp。

独立及内嵌 inputSchema/outputSchema 均显式声明根 type=object，以满足 legacy Tool 描述的必需根类型。联合成功/错误分支保持不变；纯 coreSnapshot 投影哈希固定为 `b5de73d5ab4690a74adda93f3cb60cd1230068be894e1334533544e73d92486f`，不因信封根类型修订而变化。

核心 36 个结构向量全部重核，其中 23 个 snapshot 向量比较 Draft-07 与纯 2020 投影的接受/拒绝结果；另外 13 个 comparison/retest/scope 向量不适用 capture 投影，不能冒充成功快照。工具描述的嵌入 schema 与独立文件应逐值相同。

## 安全错误数据

core detail 的 code/stage/pointer 复制自当前 cli/shared/errors.py 的白名单与正则数据；生成辅助只作离线静态提取，不执行或引入运行时私有依赖。adapter detail 另用 PROTOCOL 的五个 A_*、四个 stage 和三个 pointer，不能注入 core SafeError 构造器。A_FRAME_REJECTED 是固定 stderr 事件，不是工具错误 code。

## 验证限制

所有向量标记 design-only。可运行的静态检查包括 schema 正负例、原结构向量、23 个投影等价结果、描述内嵌一致性、来源 overlay、哈希以及同信封文本副本；这些不证明 SDK 已安装、五版本 wire 可用、scope 可解密、取消可抢占或物理槽正确排空。core 的跨字段语义校验仍必须保留。

## 可复现辅助

唯一权威的离线[生成器](../tools/generate_materials.py)及[检查器](../tools/verify_materials.ps1)纳入版本材料；脚本从自身位置定位仓库，不依赖调用 cwd。临时检查 JSON 只写被忽略的 `.planning/bootstrap/mcp-schema-checks/`。旧 `.planning/bootstrap` 入口仅转发，不保留第二份生成逻辑。所有辅助仅处理公开合成数据，不提供 MCP 业务实现。
