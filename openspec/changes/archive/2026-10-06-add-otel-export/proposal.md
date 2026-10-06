# Proposal

## Why

平台把轨迹落成本地文件，但它们只能在本平台里看。团队的 trace 后端（Langfuse、Opik、Phoenix 之一）接不进来，跨工具分析、告警与排障就无从谈起。上一轮我把这块列为「未完成」并说明原因是无法对着真实后端验证——本地 OTLP 接收端可以解决这个验证问题。

## What Changes

- 新增 OTLP 轨迹导出：把一次运行转成 OTLP/HTTP JSON 并推送到指定端点。
- 转换遵循 OpenTelemetry GenAI 语义约定：工具调用用 `execute_tool` 操作名与 `gen_ai.tool.*` 属性，token 用量用 `gen_ai.usage.*`。
- 平台自有字段（判定状态、错误类别、重试次数等）放在 `agenteval.*` 命名空间下，避免与约定冲突。
- 标识确定性生成：同一份运行重复导出得到相同的 trace 与 span 标识，便于去重与比对。
- 父子关系可还原：运行、用例、工具调用三层 span 形成正确嵌套。
- 导出失败时给出可读原因与端点，且不修改任何本地产物。
- **BREAKING**：无。导出是新增的只读能力。

## Capabilities

### New Capabilities

- `otel-export`: 把运行记录转换为 OTLP/HTTP JSON 轨迹并推送，包括语义约定映射、确定性标识与失败报告。

### Modified Capabilities

无。

## Impact

- 新增 `agenteval.otel` 模块与 CLI `otel export` 子命令。
- 依赖无新增：请求用标准库 `urllib`，序列化用标准库 `json`。
- 导出只读取运行记录，不写入、不修改本地产物。
- 测试内附带一个最小 OTLP/HTTP 接收端，用于真正验证导出内容而不是只检查结构。
