# Proposal

## Why

`agent-bridge` 定义了两种工具执行模式，但 task 能力此前只接受平台执行模式：适配层遇到 `tool_mode=agent` 就直接拒绝。于是「工具与审计都在自己后端的真实 agent」无法接入任务评测——而这类 agent 恰恰是最需要被评測的。接入 PhysioAIOps 后端时暴露了这个实现缺口。

## What Changes

- task 能力接受 agent 执行模式：agent 自行执行工具，平台不重复执行，只把它回报的调用记入当前用例的轨迹，使调用顺序与多余调用断言可用。
- 新增「当前用例轨迹」的上下文暴露，供拿不到工具注册表的适配器（例如 Bridge）写入已发生的调用。
- 新增 PhysioAIOps 适配桥：通过后端自身的账号、会话、流式对话与工具调用审计完成一次任务，并把审计转换成 Bridge 的工具调用记录。
- 新增配套示例资产：agent 注册表、真实业务任务集、`reset: snapshot` 沙箱定义与 baseline 快照说明。
- **BREAKING**：无。平台执行模式与既有接入方式不变；此前被拒绝的 `tool_mode=agent` + task 组合现在可用。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-bridge`: `Tool execution modes` 要求明确 task 能力也支持 agent 执行模式，并要求该模式下的调用进入用例轨迹。

## Impact

- `agenteval.process` 暴露当前用例轨迹；`agenteval.bridge` 的 task 适配按工具模式分派。
- 新增 `bridges/` 目录存放针对具体系统的适配桥；示例资产位于 `examples/`。
- 依赖无新增。
