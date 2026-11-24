# Windows 诊断数据契约

`core/1.0.0` 定义四种 JSON 文档。采集时，基线、目标和复测入口需要能用同一 Windows 用户解密同一份作用域文件。离线比较只读取快照。

| 文档 | 内容 | 结构 |
| --- | --- | --- |
| 作用域 | 当前 Windows 用户通过 DPAPI 保护的比较密钥 | [scope.schema.json](contracts/scope.schema.json) |
| 快照 | 一次采集的来源、策略和 11 项环境观测 | [snapshot.schema.json](contracts/snapshot.schema.json) |
| 比较报告 | 基线与目标的逐项结果及证据引用 | [comparison.schema.json](contracts/comparison.schema.json) |
| 复测报告 | 固定基线后，各项原有差异在复测中的结果 | [retest.schema.json](contracts/retest.schema.json) |

输入先经过大小、编码和 JSON 结构检查，再检查跨字段一致性。比较和复测遵循同一组主题和策略。具体规则见[模型与状态](contracts/MODEL.md)、[隐私与文件边界](contracts/PRIVACY-IO.md)和[比较、复测与退出码](contracts/OPERATIONS.md)。

[合成示例](contracts/examples/README.md)含三份快照和两份预期报告。可按 [Windows 快速上手](WINDOWS-QUICKSTART.md)在本地生成对应报告。
