# 合成契约样例

这些合成文件可以直接作为比较和复测的输入。

| 文件 | 用途 |
| --- | --- |
| [baseline.json](baseline.json) | 基线 B：完整采集，HTTP_PROXY 存在，node 可解析 |
| [target.json](target.json) | 原目标 T：工作目录不同、PATH 移除 Node 目录、HTTP_PROXY 缺失、node 确定未找到 |
| [retest.json](retest.json) | 复测 R：PATH 和 HTTP_PROXY 恢复，工作目录仍不同，Node 目录不可访问，因此 node 不确定 |
| [comparison-expected.json](comparison-expected.json) | B→T 的 11 项比较与四项 finding，completion=complete |
| [retest-expected.json](retest-expected.json) | 四项旧 finding 的 B/T/R 复测：cwd 仍存在、PATH/HTTP_PROXY 不再观测到、node 无法验证，completion=partial |

规范见 [MODEL](../MODEL.md)、[PRIVACY-IO](../PRIVACY-IO.md)、[OPERATIONS](../OPERATIONS.md)。结构分别由 [snapshot](../snapshot.schema.json)、[comparison](../comparison.schema.json)、[retest](../retest.schema.json) schema 约束；跨字段一致性仍需语义校验。所有 `$ref` 仅指向当前 schema 的 definitions，不下载远程引用。

## 重算示例指纹

- 测试 K 为 `SHA256(UTF8('windoctor-public-fixture/1'))` 的 32 字节摘要，**公开且不可用于生产**。
- scope_id 为 `HMAC-SHA256(K, UTF8('windoctor-scope/1'))`，值为 `192dc49861428938df4113f0e490a15fe999700eedcfc5b43b2dc3029e784f61`。
- token 为 HMAC-SHA256，输入是 UTF-8 紧凑 JSON `["windoctor-compare/1",1,subject,canonical_value]`，无额外空格，`ensure_ascii=false`。
- 三份快照共享 scope 及 1/1/1 策略；fixture 版本 `0.0.0`、时间、OS 版本和 UUID 全部为虚构。
- 不提供真实 scope 文件、明文生产密钥或真实 DPAPI blob。[scope schema](../scope.schema.json) 只能验证结构，不能证明解密可用。

## 合成场景的逻辑

仅为解释测试向量：目录为 `C:\Synthetic\Tools` 与 `C:\Synthetic\Node`；npx/codex/python 在前者，node 在后者。B 的 PATH 按该顺序排列；T 只保留 Tools；R 恢复两项，但第二项不可访问。R 中前三个工具的首选候选在前项已确定，因此仍为 found；node 为 skipped/INCOMPLETE_SEARCH，与 resolution.indeterminate 对应。对这些路径不进行文件访问或命令执行。

B 的 cwd 为 `C:\Synthetic\Baseline`，T/R 为 `C:\Synthetic\Target`；HTTP_PROXY 规范端点是 `["http","proxy.synthetic.invalid",8080]`，无认证。PATHEXT 为 `.EXE;.CMD;.BAT`；NO_PROXY 为 empty，HTTPS_PROXY/ALL_PROXY 为 missing。原值仅出现在此合成说明和生成辅助材料中，不出现在快照或预期报告的展示字段。

## 边界用例

[boundary-vectors.json](../boundary-vectors.json) 共 141 项，含结构正负样例、语义约束、36 项状态矩阵、scope/隐私、解析和文件安全场景。其 execution_status 为 design-only；生成或结构校验通过不代表 DPAPI、路径访问、I/O、渲染或产品测试通过。schema 仅以 pattern 约束时间字符串形状，不使用不同校验器支持程度不一的可选 format；真实 UTC 日期必须另做语义校验，2026-02-30 因此归 E_SEMANTIC。

每次生成报告都会分配新的 `report_id`。与预期文件核对时，应比较各项结果、finding ID 和快照引用。
