# Spec Delta

## MODIFIED Requirements

### Requirement: Tool execution modes

系统 SHALL 支持两种工具执行模式。平台执行模式下 agent 返回工具调用、平台执行并把结果回传以驱动多步循环；agent 执行模式下 agent 自行执行工具并回报调用记录，平台 SHALL NOT 重复执行这些工具，而是把调用记入当前用例的轨迹，使过程断言可用。两种模式 SHALL 都适用于 task 能力。

#### Scenario: 平台执行模式

- **WHEN** agent 返回工具调用且模式为平台执行
- **THEN** 平台调用对应工具、记录调用，并把结果回传给 agent 继续该用例

#### Scenario: agent 执行模式

- **WHEN** agent 自行执行工具并回报调用记录
- **THEN** 平台不重复执行工具，只记录调用供过程断言使用

#### Scenario: task 能力使用 agent 执行模式

- **WHEN** 一个自带工具与审计的 agent 以 agent 执行模式接入 task 能力
- **THEN** 该用例照常运行，且它回报的调用出现在该用例的轨迹里，可被调用顺序与多余调用断言消费

#### Scenario: 多步循环超过上限

- **WHEN** 平台执行模式下的工具调用循环达到步数上限仍未结束
- **THEN** 该用例判定为失败，并说明循环被截断
