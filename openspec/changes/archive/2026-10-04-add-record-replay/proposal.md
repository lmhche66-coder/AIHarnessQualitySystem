# Proposal

## Why

契约用例目前在运行时直接调用真实工具。一旦工具背后是外部服务或模型，测试就变慢、变脆，也无法在 CI 里离线重放；同一个任务两次结果不同，噪音往往来自环境而不是被测对象。把工具交互录制成 cassette 并在回放模式下完全复现，是让评测变便宜、变确定的最大杠杆，也是后续轨迹断言与端到端任务集的前置条件。

## What Changes

- 新增录制与回放能力：工具交互按「请求指纹 → 有序响应序列」写入 cassette，回放时不再触达真实工具。
- 提供三种模式：`record` 记录真实调用，`replay` 只读 cassette 且缺失即失败，`auto` 优先回放、缺失时录制。
- 请求指纹基于归一化后的目标与参数稳定生成，与参数键的书写顺序无关。
- 同一请求的重复调用按录制顺序依次回放，覆盖「相同请求、不同响应」的限流重试场景。
- 回放结束后报告未使用的交互；严格模式下将未使用交互判定为失败。
- 录制前对声明的敏感字段做脱敏，避免凭据进入仓库，且脱敏不影响指纹匹配。
- 用例轨迹记录每次调用的 cassette 命中或缺失，便于归因是 agent 多调了一步还是 cassette 过期。
- 运行器与 CLI 新增 `--cassette` 与 `--cassette-mode` 入口，并支持严格模式开关。
- **BREAKING**：无。未指定 cassette 时行为与现状完全一致。

## Capabilities

### New Capabilities

- `record-replay`: 工具交互的录制、回放与 hermetic 重放语义，包括 cassette 格式与持久化、请求指纹、重复请求的有序回放、缺失失败、未使用交互报告与敏感字段脱敏。

### Modified Capabilities

无。既有 `eval-core` 与 `tool-contract-eval` 的需求不变；新增的错误类别与轨迹事件属于本新能力的可观察行为。

## Impact

- 新增 `agenteval.cassette` 模块，以及 `ToolErrorKind` 中的缺失错误类别。
- 新增运行根目录下的 `cassettes/` 目录，用于存放录制文件。
- CLI 新增 `--cassette`、`--cassette-mode` 与严格模式开关；既有参数保持兼容。
- `ContractRunner` 增加 cassette 生命周期钩子，用于回放结束后的未使用交互检查。
- 依赖无新增，指纹与序列化使用标准库。
- 本变更是后续「LLM 调用录制」「轨迹级过程断言」与「CI 门禁」的前置依赖。
