# Spec Delta

## MODIFIED Requirements

### Requirement: Tool call trace normalization

系统 SHALL 把执行轨迹中的工具调用归一化为结构化序列。每条记录 MUST 包含目标工具、调用参数、成功标志与错误类别，并 SHALL 区分「值被记录为空」与「值未被记录」两种情形。

#### Scenario: 从轨迹提取工具调用序列

- **WHEN** 轨迹中包含若干次工具调用事件
- **THEN** 系统按发生顺序输出结构化调用序列，索引从零开始递增

#### Scenario: 轨迹中没有工具调用

- **WHEN** 轨迹中不含任何工具调用事件
- **THEN** 系统输出空序列，而不是报错

#### Scenario: 区分空值与未记录

- **WHEN** 某条调用事件没有携带返回值字段
- **THEN** 该调用被标记为返回值未被记录，与返回值明确为空区分开
