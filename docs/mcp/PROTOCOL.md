# MCP 协议、错误与 stdio 运行规则

本页说明适配器使用的 MCP 协议及 Python SDK `mcp==2.3.0` 的行为。[适配器契约](ADAPTER.md) · [协议与 SDK 来源](SOURCES.md)

## 协议时代与兼容矩阵

| 版本 | 协议行为 | 适配器行为 |
| --- | --- | --- |
| modern 2026-07-28 | 每个请求在 _meta 中携带版本和 clientCapabilities，clientInfo 为可选建议项 | server/discover、tools/list 和工具调用直接使用请求元数据 |
| legacy 2025-11-25 | initialize → initialized 后使用工具 | SDK 将工具描述和结果转换为 legacy 模型，文本项提供同一结果的 JSON 副本 |
| legacy 2024-11-05、2025-03-26、2025-06-18 | 使用旧式初始化 | 固定 SDK 将这些日期统一映射到 legacy 处理路径 |

modern宣告tools能力，listChanged=false；无resources/prompts/logging/Tasks等能力。控制面按时代分开：modern使用server/discover、tools/list；legacy使用tools/list、ping。modern方法表不含ping，不把ping当跨时代通用探针。工具schema相同，时代形状由SDK公开模型转换；注册表支持五版本不证明所有旧客户端都能处理转换结果。

### 首帧、时代锁定与跨时代请求

以下均指已通过适配传输边界、送入runner的JSONRPCRequest。边界拒绝的原帧及边界产生的错误不送SDK入站，不选择时代；通知不决定时代。runner依据首个收到的请求选择并锁定时代，不等请求成功才锁定。

| 状态/输入 | 固定SDK路径与本项目预期 |
| --- | --- |
| 首请求initialize（不论是否带版本_meta） | 选择legacy，再由SDK执行旧式初始化协商 |
| 首个非initialize请求的params._meta是对象且含io.modelcontextprotocol/protocolVersion键 | 选择modern，然后校验版本/能力；键值为null、非字符串或未知版本也先锁modern再返回相应错误 |
| 上项键值为null/数字等非字符串且已送runner | -32602；即使请求失败也已锁modern。未知的日期形字符串则-32022 |
| 首个非initialize请求无上述版本键 | 选择legacy；首个无版本server/discover按legacy未知方法返回-32601；首个无版本tools/list因未初始化为-32602，不将两者合并 |
| 已锁定modern，普通请求缺必须版本/能力元数据 | -32602；不支持的现代版本为-32022，保留安全supported/requested标准数据 |
| 已锁定modern，ping | 合法modern元数据时-32601；缺元数据先-32602 |
| 已锁定modern，initialize | runner优先返回-32022，无论是否携带_meta；不回到legacy握手。params.protocolVersion为日期字符串则data含supported/requested，缺失或非字符串则此特殊分支只有supported |
| 已锁定legacy，非initialize请求带上述版本键 | -32600；不会升级为modern，元数据键不可被静默忽略 |
| legacy的initialize提议2026-07-28 | 仍走legacy协商，回退SDK的最新握手版本2025-11-25；不切换时代 |
| 任一时代后续请求 | 同连接时代锁定；不同版本/跨时代方法按上表和该时代模型处理，不由适配层另造协商 |

上表描述进入SDK后的路径。额外项目入站策略：版本字段为字符串却不匹配`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`时，边界先以-32602/Invalid params拒绝，不附data，不送runner、不选择时代（连接已锁定则保持原时代）；合法ID保留，有效通知不回复。该策略同时覆盖`params._meta`内的版本键及`method=initialize`时的`params.protocolVersion`，防止自由文本从任一位置进入requested错误字段。不把任意字符串伪装成受支持版本。非字符串仍交SDK按上表处理；普通modern请求的版本键为null/数字时锁modern再-32602；日期形状但不支持的值仍交SDK报-32022。

采用官方低层Server的on_list_tools/on_call_tool处理器、stdio_server和公开流接口；handler形状为async(ctx,params)。低层不自动强制应用工具input/output schema，适配层必须自行检查arguments和结果。SDK默认输入替换非法UTF-8，以及部分legacy异常/日志回显风险，不能当作本项目的安全保证。

## 工具错误与协议错误

| 情况 | 输出层级与映射 |
| --- | --- |
| 短小完整LF帧的JSON语法错误 | 适配传输边界构造-32700，message=Parse error，id=null；不送SDK入站 |
| JSON合法但JSON-RPC外壳无效（如method非字符串、jsonrpc错误、非法id） | 适配传输边界构造-32600，message=Invalid Request；ID规则见下文；不是工具参数错误 |
| 有效JSON-RPC请求的未知方法 | SDK按时代路由产生-32601，出站固定message=Method not found，不回显原method |
| 有效RPC请求中tools/call方法参数无效、name缺失/类型错误、未知工具、arguments显式null/数组/标量 | JSON-RPC -32602，固定message=Invalid params；协议_meta按时代表处理 |
| 有效无ID通知的方法参数畸形或未知通知 | 不发任何响应、不调用capture；取消无法关联则安全忽略，不记录原参数 |
| 已知工具的arguments是合法对象但含额外键 | 正常CallToolResult，isError=true，adapter A_ARGUMENT |
| 有效core快照complete/partial/failed | isError=false，成功信封保留core.completion；不得把CLI exit3机械转成MCP错误 |
| core抛出SafeError | isError=true，origin=core，detail=固定code/stage/pointer，不转发CLI stderr |
| 单工作槽未释放 | isError=true，adapter A_BUSY，不排队、不开始第二个采集 |
| 服务端10秒应用时限已到、请求尚未被peer取消 | isError=true，adapter A_TIMEOUT；不能伪造core失败快照 |
| 完整出站行超过4MiB | isError=true，adapter A_OUTPUT_LIMIT；发送小型错误信封 |
| 已知工具内部非预期异常或适配信封/文本副本不一致 | isError=true，adapter A_INTERNAL，固定安全字段；core校验抛SafeError仍按core错误分支 |
| 请求已接受peer取消 | 不再为此请求写结果/错误/进度；仅内部丢弃迟到结果 |
| scope启动读取/DPAPI失败、依赖缺失 | 启动失败，无stdout协议内容；stderr固定安全JSON，不能伪装为某次工具结果 |

适配error detail固定三键：code为A_ARGUMENT/A_BUSY/A_TIMEOUT/A_OUTPUT_LIMIT/A_INTERNAL；stage为arguments/dispatch/capture/serialize；pointer仅/arguments、/request、/result。origin=core沿用原SafeError，不注入A_*或stage=mcp。

数组/标量arguments可能在SDK调用handler前已被协议模型拒绝，不能要求其变为工具结果。pinned SDK对null与省略均可给typed params.arguments=None；本项目只接受省略，不接受显式null，因此须用公开ctx.params或模型字段存在性区分后发送固定-32602，禁止用`params.arguments or {}`把显式null悄悄接纳。空对象内额外键才进入工具schema的A_ARGUMENT分支。

所有普通工具结果在modern使用resultType=complete。resultType只表示请求已结束，与core的completion及isError分开。错误分支也属于outputSchema的一部分，不能把错误对象塞入仅允许Snapshot的schema。

协议层其他标准错误如-32021/-32022由SDK处理，但出站边界须保留code及必要的已校验标准数据、重建固定message。普通不支持版本字符串的-32022要求supported和requested，不能只删requested；modern连接收到initialize的特殊分支则按上表保留原有字段，缺版本时只有supported，不能凭空补requested。requested的日期形状通过上述两处入站策略保证，supported来自固定版本表。若出站发现不满足对应分支必需形状，应安全停止并报告边界失效，不能发送残缺标准错误。不将任意SDK data、方法、取消reason、clientInfo或异常str回显。JSON-RPC关联ID是相关性元数据，不当作诊断正文或日志；不承诺阻止攻击者在允许标识中隐蔽编码数据。

### 三层处理与单一出站路径

1. 适配传输边界先有界读取一条LF帧，执行严格字节/Unicode/复杂度检查。超长、非法UTF-8、BOM、重复键、非法surrogate、非有限数、批量数组或无法安全继续的输入，关闭连接，仅stderr固定A_FRAME_REJECTED事件；不无限丢弃、不返回半帧。
2. 对在限额内且完整分帧的普通JSON语法错误，边界用公开`mcp_types.jsonrpc.JSONRPCError`、`ErrorData`及`mcp.shared.message.SessionMessage`构造固定安全响应；合法JSON但外壳不符合公开`JSONRPCMessage`模型时，边界分类为无效请求。不得把解码Exception送入SDK后期待默认dispatcher替项目回错：固定SDK在未配置观察器时只记录并丢弃它，双时代runner未提供该保证。
3. 合法消息才进入SDK公开流，由SDK处理方法路由、方法参数模型、时代选择、协商和取消相关性。边界错误与SDK输出汇入同一个有界出站通道，唯一writer负责错误白名单、通知不回复、取消/EOF封锁、完整序列化预算和stdout写入。不得在reader/handler另开stdout捷径、使用私有观察器或修改SDK。

ID规则：JSON语法错误固定id:null；外壳无效时，只有原对象里唯一、在限额内的字符串或整数id可原样关联（bool不是整数ID），非法/缺失/不可确定则id:null。先按原始对象中id是否存在区分请求/通知，再选公开具体类型校验；不能只以JSONRPCMessage联合模型校验成功就认作通知，避免非法id被Notification分支当额外字段忽略。合法方法请求的参数错误保留原ID。有效无ID外壳才是通知，其方法参数错误也不回复；缺method、method类型错或jsonrpc错误的无ID对象不是有效通知，返回-32600/id:null。响应形状无效或请求/响应字段混合而无法安全分类时关闭连接，避免对坏响应制造错误循环。合法响应交SDK关联，不作为工具调用。

边界自身产生的错误不携带任意data、_meta或原错误文字；`JSONRPCError.id`公开允许None并序列化为null，不能用全局exclude_none把必需的id:null删除。所有路径都须在最终写端再次检查连接和请求状态，尤其输出背压时收到取消、EOF后SDK尝试发送关闭错误；已取消/已关闭的输出不得因早先排队而漏出。

SDK低层文档与pinned legacy兜底源码存在差异：后者可能logger.exception并发送str(e)。适配层必须捕获应用异常，过滤第三方日志/traceback，且对最终协议error做白名单输出；不能只把raise_exceptions=False当作脱敏。不得静默修改标准必需字段。

运行期由核心校验器和适配器固定信封字段校验工具结果。

## 时限、取消与资源

固定策略：capture应用时限10秒，单一物理工作槽，无无界队列；一次额外调用立即A_BUSY。modern的discover/list、legacy的list/ping以及取消仍须可响应，不能把同步capture直接跑在事件循环阻塞整个协议。

在同进程工作线程执行原capture。其resolver五秒预算只在Windows调用之间检查，GetDriveTypeW/os.stat/DPAPI没有硬取消保证。可以停止等待并抑制结果，但不可声称底层调用已经结束。

槽状态为idle→running→（正常完成回idle，或超时/取消进入draining）→底层函数实际完成后才回idle。超时/取消不能在finally提前释放物理槽或再派一个线程。draining期间capture仍A_BUSY，控制请求仍可处理；不自动重启、不修改环境。

收到与未完成请求关联的取消通知后，适配器抑制该请求后续输出。已经提交给操作系统的响应无法撤回。未知、重复或已完成 ID 的取消通知可安全忽略，适配器不记录原始 reason。

客户端建议15秒绝对等待上限，不因进度无限延长；本服务不发送进度。启动/发现也建议15秒宿主等待上限。这些是宿主/测试参数，不是服务端硬中止Windows调用的承诺。

EOF停止接收新请求并关闭输出，不再发布迟到结果；空闲时应及时退出。若底层调用尚阻塞，服务不保证有界退出，客户端可在2秒关闭宽限后按其策略终止服务进程。服务自己不终止用户进程、不杀工作线程、不开放网络。

## stdio 与日志

只使用本地stdin/stdout的UTF-8换行JSON-RPC；每行一条消息，无真实内嵌换行（JSON字符串内换行转义），stdout无banner/print/CLI帮助/traceback。应用错误不结束服务；启动错误不发伪协议响应。

入站在SDK前执行ADAPTER的字节/深度/Unicode/重复键限量与上文错误分类。使用SDK公开自定义流能力作有界预检，最小解析错误响应由边界负责；不重写方法路由、版本协商或取消相关性，不调用core私有_decode，也不为解析写临时文件。

适配器在导入 SDK 前接管本进程的 Windows 标准句柄及 CRT 文件描述符。入站经自定义有界 UTF-8 流处理，仅私有协议写端可输出 JSON-RPC。

stderr只允许适配层固定安全事件（如启动失败、frame拒绝、内部失败）；第三方日志默认抑制，不输出原参数、scope/DPAPI blob、原路径、环境值、异常堆栈或取消原因。即使只读工具也不启用遥测导出/HTTP/OAuth。

出站响应先完整序列化并检查 4 MiB 上限。stdout 写入失败时安全关闭连接。
