# Spec Delta

## Purpose

对 agent 服务施加可控并发，测量真实完成情况、延迟分布、扇出与成本，并按非功能预算判定是否达标，产出可被控制台与门禁统一消费的运行记录。

## ADDED Requirements

### Requirement: Load scenario definition

系统 SHALL 支持以声明方式定义压测场景，至少包含目标地址、请求方式、并发数，以及请求总量与持续时间中的至少一项。

#### Scenario: 载入场景

- **WHEN** 用户提供一份场景定义
- **THEN** 系统解析为场景对象，并在并发数非法或既无请求总量也无持续时间时拒绝

#### Scenario: 复用场景指向不同环境

- **WHEN** 用户提供基础地址覆盖
- **THEN** 系统把场景中的相对地址解析到该基础地址，无需改动场景文件

### Requirement: Concurrent execution

系统 SHALL 按声明的并发数并发施压，并在达到请求总量或持续时间后停止。

#### Scenario: 达到请求总量

- **WHEN** 完成的请求数达到声明的总量
- **THEN** 施压停止，且不再发出新的请求

#### Scenario: 达到持续时间

- **WHEN** 持续时间耗尽
- **THEN** 施压停止，已发出的请求等待结束后计入结果

### Requirement: Latency and responsiveness metrics

系统 SHALL 报告端到端耗时的 p50、p90、p95、p99；对标记为流式的场景 SHALL 另外报告首字节时间。

#### Scenario: 报告延迟分位数

- **WHEN** 一次施压结束
- **THEN** 报告中包含四个分位数的端到端耗时

#### Scenario: 流式响应

- **WHEN** 场景标记为流式
- **THEN** 系统记录首个数据块到达的时间，并在报告中给出其分位数

#### Scenario: 非流式响应

- **WHEN** 场景未标记为流式
- **THEN** 系统不报告首字节时间，而不是用总耗时冒充

### Requirement: Fan-out and usage observation

系统 SHALL 支持按字段路径从响应体提取扇出与用量指标，并给出均值与 p95；未提取到任何数据时 MUST 标注为未观测，SHALL NOT 以零代替。

#### Scenario: 提取到扇出

- **WHEN** 响应体中包含被声明的字段
- **THEN** 系统汇总该指标的均值与 p95

#### Scenario: 未观测到扇出

- **WHEN** 声明了提取规则但没有任何响应提供可解析的数值
- **THEN** 报告标注该指标未被观测到，并说明原因

#### Scenario: 未声明提取规则

- **WHEN** 场景没有声明任何提取规则
- **THEN** 报告省略扇出区块，而不是报告零

### Requirement: Error attribution

系统 SHALL 按原因分类统计失败请求，至少区分超时、限流、服务端错误、客户端错误与传输失败。

#### Scenario: 限流被单独计数

- **WHEN** 目标返回 429
- **THEN** 该请求计入限流，而不是笼统计入客户端错误

#### Scenario: 超时被单独计数

- **WHEN** 请求超过声明的超时时间
- **THEN** 该请求计入超时，并记录其耗时

### Requirement: Load budget verdict

系统 SHALL 支持声明非功能预算：延迟分位数上限、错误率上限与吞吐下限；任一不满足时判定不通过并逐条列出原因。

#### Scenario: 全部达标

- **WHEN** 实测指标均满足声明预算
- **THEN** 该场景判定为通过

#### Scenario: 未达标

- **WHEN** 任一指标超出预算
- **THEN** 该场景判定为不通过，并记录指标名称、实测值与上限或下限

#### Scenario: 未声明预算

- **WHEN** 场景没有声明任何预算
- **THEN** 施压照常执行并产出全部指标，但不对通过与否下结论

### Requirement: Results enter the standard run record

压测结果 SHALL 写入既有运行记录并填充标准指标字段，使控制台与门禁无需改动即可消费。

#### Scenario: 产出运行记录

- **WHEN** 一次压测结束
- **THEN** 运行记录中包含每个场景的判定，以及含吞吐、延迟分位数与错误分类的压测报告

#### Scenario: 控制台可见

- **WHEN** 用户在控制台查看该运行
- **THEN** 该运行出现在运行列表中，且详情包含标准指标字段
