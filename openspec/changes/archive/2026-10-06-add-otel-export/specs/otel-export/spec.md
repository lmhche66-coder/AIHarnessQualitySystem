# Spec Delta

## Purpose

把本平台的运行轨迹转成标准的 OTLP 数据并推送给外部 trace 后端，使轨迹可以在团队已有的可观测工具里被检索、关联与告警，而不是只能留在本地文件里。

## ADDED Requirements

### Requirement: OTLP payload construction

系统 SHALL 把一次运行转换为 OTLP/HTTP JSON 结构，包含资源属性、作用域与 span 列表。

#### Scenario: 结构完整

- **WHEN** 用户对一次运行请求导出
- **THEN** 产出包含资源属性、作用域名称与全部 span 的 OTLP 结构

#### Scenario: 标识合法

- **WHEN** 系统生成 trace 与 span 标识
- **THEN** trace 标识为 32 位十六进制、span 标识为 16 位十六进制

### Requirement: Semantic convention mapping

系统 SHALL 按 OpenTelemetry GenAI 语义约定映射工具调用：操作名使用 `execute_tool`，并携带 `gen_ai.tool.name`。

#### Scenario: 工具调用 span

- **WHEN** 一次用例轨迹中包含工具调用
- **THEN** 该调用对应一个操作名为 `execute_tool` 的 span，并带有目标工具名属性

#### Scenario: token 用量

- **WHEN** 轨迹中记录了 token 用量
- **THEN** span 上以 `gen_ai.usage.*` 命名空间携带输入与输出 token 数

#### Scenario: 平台自有字段

- **WHEN** 系统需要表达判定状态、错误类别或重试次数
- **THEN** 这些字段放在 `agenteval.*` 命名空间下，不占用语义约定保留名

### Requirement: Deterministic identifiers

系统 SHALL 为同一份运行生成稳定的 trace 与 span 标识，重复导出得到相同结果。

#### Scenario: 重复导出

- **WHEN** 对同一份运行连续导出两次
- **THEN** 两次产出的标识与 span 数量完全一致

#### Scenario: 不同运行可区分

- **WHEN** 导出两次不同的运行
- **THEN** 它们的 trace 标识不同

### Requirement: Span hierarchy

系统 SHALL 以三层结构表达父子关系：运行级 span 为根，用例级 span 为其子，工具调用级 span 为用例级 span 的子。

#### Scenario: 父子关系

- **WHEN** 导出一次包含若干用例与工具调用的运行
- **THEN** 每个子 span 的父标识指向其所属的上级 span

#### Scenario: 根 span 无父

- **WHEN** 导出运行级 span
- **THEN** 它不携带父标识

### Requirement: Export delivery

系统 SHALL 通过 OTLP/HTTP 把构造好的结构推送到用户指定的端点，并区分成功与失败。

#### Scenario: 推送成功

- **WHEN** 端点接受该请求并返回成功状态
- **THEN** 系统报告导出成功与 span 数量

#### Scenario: 推送失败

- **WHEN** 端点返回错误状态或连接失败
- **THEN** 系统报告失败并给出端点与原因，且退出码非零

#### Scenario: 端点缺失

- **WHEN** 用户未提供端点
- **THEN** 系统以用法错误结束，而不是静默丢弃

### Requirement: Export is read-only

导出 SHALL NOT 写入、移动或删除任何本地产物。

#### Scenario: 导出不修改产物

- **WHEN** 用户执行一次导出
- **THEN** 运行记录与结论记录的目录内容保持不变
