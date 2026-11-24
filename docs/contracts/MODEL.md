# core/1.0.0 模型与状态

核心数据使用 JSON Schema Draft-07。通过结构校验后，还需核对完成状态、跨字段约束和证据引用。四份 schema 见[数据契约索引](../CONTRACTS.md)。

## 快照根字段

schema_version 固定 `windoctor.snapshot/1`。根对象仅允许下表字段，全部必需：

| 字段 | 约束 |
| --- | --- |
| snapshot_id | 每次采集新建的小写 UUID v4，不是机器标识 |
| captured_at | 有效 UTC 日期，严格 YYYY-MM-DDTHH:MM:SSZ |
| collector | name 固定 windoctor，version 为三段数字版本 |
| source | declared_context 为 terminal/agent/mcp/unspecified；observed_via 固定 current_process；process_instance_id 为进程启动时生成、进程内复用的 UUID v4 |
| policy | privacy/normalization/resolver 为 1..255 整数；scope_id 为 64 位小写 hex；本版支持 1/1/1 |
| os | platform 固定 win32，version 为 <=32 字符的数字点分版本 |
| completion | complete/partial/failed，必须与观测状态重新计算的结果一致 |
| observations | 下表 11 个键，全部必需，不允许未知键 |
| path_entries | 有序数组，最多 256 项，index 必须等于数组下标 |
| resolutions | node/npx/codex/python 四键，全部必需 |

schema_version 本身也必需。入口只有声明标签，没有自由文本或 daemon 选项；实际来源由程序生成。导入文件仍可伪造来源，报告不作来源认证承诺。

observations 的固定顺序和对象：cwd；env.PATH；env.PATHEXT；env.HTTP_PROXY；env.HTTPS_PROXY；env.ALL_PROXY；env.NO_PROXY；exec.node；exec.npx；exec.codex；exec.python。不额外读取自定义变量或完整配置。

## 观测原子

每个 observation 精确包含 state/display/token/reason/auth_present 五键，没有原始值或自由文本。

| state | display | token | reason | 含义 |
| --- | --- | --- | --- | --- |
| present | REDACTED | 64 位小写 hex | NONE | 成功得到可比较值 |
| empty | EMPTY | null | NONE | 成功观测空字符串；exec.* 和 cwd 不使用 |
| missing | MISSING | null | NOT_SET 或 NOT_FOUND | 变量未设置或完整搜索未找到程序；cwd 不使用 |
| inaccessible | UNAVAILABLE | null | ACCESS_DENIED | 确认访问被拒绝 |
| skipped | SKIPPED | null | 规定原因 | 因策略/不完整检查跳过 |
| error | ERROR | null | 规定原因 | 采集读取、解析或预算失败 |

原因全集：NONE/NOT_SET/NOT_FOUND/ACCESS_DENIED/EMPTY_ENTRY/RELATIVE_PATH/NETWORK_PATH/REPARSE_POINT/UNSUPPORTED_PATH/INCOMPLETE_SEARCH/INVALID_VALUE/LIMIT_EXCEEDED/READ_FAILED/TIME_BUDGET/INTERNAL。

- missing：env.* 只能 NOT_SET，exec.* 只能 NOT_FOUND。
- skipped：仅 EMPTY_ENTRY/RELATIVE_PATH/NETWORK_PATH/REPARSE_POINT/UNSUPPORTED_PATH/INCOMPLETE_SEARCH/TIME_BUDGET。
- error：仅 INVALID_VALUE/LIMIT_EXCEEDED/READ_FAILED/TIME_BUDGET/INTERNAL。
- 三个代理项在 present 时 auth_present 为 boolean，表示 URL userinfo 是否存在；其它状态或非代理项必须 null。认证内容不保存或比较。
- 漏掉键是输入无效，不是 missing。禁止用 null 代替整个原子。

completion 按 11 项推导：全为 present/empty/missing 则 complete；至少一项成功状态且至少一项 inaccessible/skipped/error 则 partial；全部为后三者则 failed。确定地未找到程序是成功观测。

## PATH 与解析

path_entries 每项精确包含 index/token/status/reason。index 为 0..255；token 是 HMAC 或 null。status 为 searched/missing/skipped/inaccessible/error；searched→NONE，missing→NOT_FOUND，inaccessible→ACCESS_DENIED，其余使用上表对应原因。空 PATH 项为 skipped/EMPTY_ENTRY，token=null；其他合法长度的非空项只保存词法 HMAC。

env.PATH 为 present 时，数组对应完整分项，顺序与重复项保留；其余状态数组为空。空字符串 PATH 用 empty，未设置用 missing。超出 256 项、32767 总码点或单项 4096 码点时为 error/LIMIT_EXCEEDED，不截断后假装完整。

每个 resolutions 值精确包含 certainty/first_observed/entry_index/reason；first_observed 明确为 64 位小写 hex 或 null，entry_index 为 0..255 整数或 null：

- found：token 为该 exec 主题首选路径的 HMAC，index 有效，reason=NONE；对应 observation=present 且 token 相同。
- not_found：token/index 均 null，reason=NOT_FOUND；对应 observation=missing。
- indeterminate：reason=INCOMPLETE_SEARCH，token/index 可同时有值或同时 null；对应 observation=skipped/INCOMPLETE_SEARCH。首个已观测候选不是确定值。

上表中的 token 指 first_observed。path_entries 中非空且长度合法的项 token 必须为 hex，空项或超限/非法字符串无法生成条目时不写 token；后者是整个 PATH 的 error，不制造部分条目。

PATH missing/empty 时不隐式搜索当前目录，四程序 not_found；PATH 不可读/错误时均 indeterminate。按每程序的目录及扩展名顺序，候选前有未完成项则 indeterminate；候选后的未完成项不影响首选。path_entries 为目录聚合摘要，不替代每程序的前缀证据；聚合失败可能属于另一程序，不能直接推翻已有完整前缀。input 中的 index 必须指向现存 path_entries。

## 比较与复测文档

比较 schema_version=`windoctor.comparison/1`；其余根字段 report_id/baseline_id/target_id/completion/comparisons/findings 全部必需。comparisons 恰好按规定顺序覆盖 11 主题，每项精确含 subject/result/reason/auth_changed/evidence。证据有两项，依次引用基线/目标，精确含 snapshot_id 和受限 JSON Pointer `/observations/<规定主题>`。

比较及复测的 report_id 都是新建的小写 UUID v4，输入 ID 和 evidence.snapshot_id 也按同格式验证且必须对应实际输入；completion 只允许 complete/partial/failed，并按操作规则重新计算。

finding 只对 different 生成，精确含 finding_id/rule_id/subject/evidence。rule_id 为 VALUE_DIFFERENCE/STATE_DIFFERENCE/AUTH_PRESENCE_DIFFERENCE；finding_id=`rule_id + ':' + subject`，每主题最多一个。

复测 schema_version=`windoctor.retest/1`；其余根字段 report_id/baseline_id/original_target_id/retest_target_id/completion/items 全部必需。每个 item 精确含 finding_id/subject/outcome/reason/evidence；outcome 为 persists/no_longer_observed/unverifiable，证据有 B/T/R 三项。所有引用必须可解引用、ID 正确、主题不重复。

错误对象精确为 code/stage/pointer；stage 为 arguments/read/validate/scope/capture/compare/retest/write/internal。pointer 只能为受限已知字段路径或 /input、/output、/scope、/arguments；不能回显未知键名、原始路径/值和异常。[错误码和退出码](OPERATIONS.md)

## 严格输入

所有输入 kind（snapshot/comparison/retest/scope）共用有界词法预检与严格 JSON 规则。快照/报告最多 1 MiB，scope 最多 16 KiB；strict UTF-8、无 BOM，嵌套深度 <=16，字符串 <=4096 Unicode 码点，数组 <=2048 项，对象 <=128 键，schema 的更小限额优先。拒绝重复键、非有限数、未配对 surrogate、尾随内容、未知字段和不支持的 schema_version。

先限量读取和有界词法预检，再严格 JSON 解析、结构校验、跨字段语义校验；不能无限读取后才判断上限。输出从白名单重建，不相信输入自称已脱敏。schema/HMAC 不能证明来源真实或阻止有意的隐蔽编码，不作这些承诺。

规则来源：[JSON Schema Draft-07 validation](https://json-schema.org/draft-07/draft-handrews-json-schema-validation-01)。只使用本地 definitions 和标准校验关键字，运行时不下载远程引用。
