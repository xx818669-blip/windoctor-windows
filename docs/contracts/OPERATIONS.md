# 比较、复测与退出码

## 比较

`compare` 验证基线 B 与目标 T 的结构、完成状态和快照 ID。两份快照的 ID 相同且内容不同时返回 `E_ID_CONFLICT`，ID 和内容都相同时返回 `E_SEMANTIC`。版本不受支持时返回 `E_VERSION`。

每个主题按下表从上往下判定，采用第一项匹配的规则。每条结果都引用 B、T 中的同一个 `/observations/<subject>`。11 个主题的顺序见[模型与状态](MODEL.md)。

| 条件 | result | reason |
| --- | --- | --- |
| 作用域或策略版本不兼容 | not_comparable | POLICY_MISMATCH |
| 至少一侧为 inaccessible、skipped 或 error | unknown | OBSERVATION_UNAVAILABLE |
| 两侧成功观测，状态不同 | different | STATE_CHANGED |
| 同为 empty 或同为 missing | equal | STATE_EQUAL |
| 同为 present，token 不同 | different | VALUE_CHANGED |
| 代理同为 present、token 相同，只有认证信息的存在性不同 | different | AUTH_PRESENCE_CHANGED |
| 同为 present，token 和认证信息的存在性均相同 | equal | VALUE_EQUAL |

两个代理观测都为 `present` 时，`auth_changed` 表示有无认证信息发生变化，其余情况为 `null`。端点和认证存在性同时变化时，原因取 `VALUE_CHANGED`，`auth_changed` 仍为 `true`。报告无法比较密码内容。

每个 `different` 生成一条 finding，对应规则为 `STATE_DIFFERENCE`、`VALUE_DIFFERENCE` 或 `AUTH_PRESENCE_DIFFERENCE`。`equal` 和 `different` 均为可判定结果。11 项全部可判定时报告为 `complete`，部分可判定时为 `partial`，全部无法判定时为 `failed`。

## 三快照复测

`retest` 接受基线 B、原目标 T 和复测目标 R，三个 ID 必须不同。它比较 B 与 T、B 与 R，逐一追踪首次比较出现的 finding，保留原 finding ID。每项证据按 B、T、R 顺序引用同一主题。

| B 与 R 同主题的比较 | outcome | reason |
| --- | --- | --- |
| equal | no_longer_observed | RETEST_EQUAL |
| different | persists | RETEST_DIFFERENT |
| unknown 或 not_comparable | unverifiable | RETEST_UNAVAILABLE |

同一主题仍有差异时，即使触发的规则已变，结果也是 `persists`。需要检查新出现的主题时，请另行比较 B 与 R。同一 MCP 进程的重复观测仅反映该进程当时的状态。

所有旧差异都可判定时复测为 `complete`，部分无法判定时为 `partial`，全部无法判定时为 `failed`。如果首次比较没有 finding，则 `items=[]` 且 `completion=complete`，表示没有旧差异可追踪。

## CLI

命令在项目根目录运行，文件参数使用绝对本地路径。方括号表示可选参数，竖线表示可选值。

```text
python -m cli scope-init --out <scope.wdscope>
python -m cli capture [--context terminal|agent|mcp|unspecified] --scope-file <scope.wdscope> [--out <snapshot.json>]
python -m diff compare --baseline <B.json> --target <T.json> --format json|text|html [--out <file>]
python -m diff retest --baseline <B.json> --original-target <T.json> --retest-target <R.json> --format json|text|html [--out <file>]
```

采集默认入口为 `unspecified`。省略输出文件时，采集将 UTF-8 JSON 写到标准输出，比较和复测可将 JSON 或文本写到标准输出。HTML 需要指定一个新输出文件。错误通过标准错误返回单个 JSON 对象，仅含 `code`、`stage` 和 `pointer`。

| 退出码 | 含义 |
| --- | --- |
| 0 | 操作成功，快照或报告为 complete。发现差异也可以返回 0。 |
| 2 | 参数、版本、大小、JSON、作用域格式、结构或语义无效。 |
| 3 | 成功写出有效结果，completion 为 partial 或 failed。 |
| 4 | 输入输出错误，包括作用域不可读或无法解密、同名输出或文件别名冲突。 |
| 5 | 内部失败。 |

错误码集合由 [SafeError](../../cli/shared/errors.py) 定义。MCP 调用遵循[适配协议](../mcp/PROTOCOL.md)。

## Python 接口

`compare(B, T)` 和 `retest(B, T, R)` 接受已加载的文档，返回比较或复测文档。`render(report, format)` 返回 UTF-8 字节。这些操作不访问快照记录的路径，也无需读取作用域。[比较](../../diff/compare.py) · [复测](../../diff/retest.py) · [渲染](../../diff/render.py)

`load_document(path, kind)` 返回校验后的字典，`serialize_document(doc, kind)` 校验后输出 UTF-8 字节。`kind` 可以是 `snapshot`、`comparison`、`retest` 或 `scope`。文件写入前，入口先通过 `check_output_aliases` 检查显式输入与输出的文件身份。`write_output(data, path, output_kind)` 校验输出种类、扩展名与大小，以排他方式创建文件。[共享 I/O](../../cli/shared/json_io.py) · [文件边界](PRIVACY-IO.md)
