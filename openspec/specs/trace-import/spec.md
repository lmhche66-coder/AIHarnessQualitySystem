# trace-import Specification

## Purpose
把外部 agent 的工具调用审计导入成平台可直接求值的轨迹，使跑在别处、无法被包装的 agent 也能接受过程断言、指标与门禁，而不需要改动它自己的代码。

## Requirements

### Requirement: Audit record ingestion

系统 SHALL 支持从工具调用审计记录导入轨迹，接受 JSON 数组与 CSV 两种形态，并按开始时间排序。

#### Scenario: 从 JSON 审计导入

- **WHEN** 用户提供一份工具调用审计的 JSON 数组
- **THEN** 系统按开始时间排序并生成一条包含全部调用的轨迹

#### Scenario: 从 CSV 审计导入

- **WHEN** 用户提供一份工具调用审计的 CSV 导出
- **THEN** 系统解析相同的字段并生成等价轨迹

#### Scenario: 空审计输入

- **WHEN** 审计输入中没有任何记录
- **THEN** 系统拒绝导入并说明输入为空，而不是生成空轨迹

### Requirement: Lifecycle mapping

系统 SHALL 把审计记录的生命周期状态映射为调用结果：完成映射为成功，失败映射为结构化错误，只有开始没有结束的记录映射为未结束。

#### Scenario: 完成的调用

- **WHEN** 审计记录的状态为完成
- **THEN** 该调用在轨迹中记为成功

#### Scenario: 失败的调用

- **WHEN** 审计记录的状态为失败
- **THEN** 该调用在轨迹中记为结构化失败，并保留其安全错误消息

#### Scenario: 未结束的调用

- **WHEN** 审计记录只有开始时间而没有结束时间
- **THEN** 该调用在轨迹中记为未结束，且系统 SHALL NOT 臆造结果或耗时

### Requirement: Field validation

系统 SHALL 校验导入记录的必需字段，缺失时拒绝并指明位置。

#### Scenario: 缺少工具名

- **WHEN** 某条审计记录没有工具名
- **THEN** 系统拒绝导入并指出该记录的位置

#### Scenario: 缺少开始时间

- **WHEN** 某条审计记录没有开始时间
- **THEN** 系统拒绝导入并指出该记录的位置

#### Scenario: 参数不可解析

- **WHEN** 某条审计记录的参数不是可解析的对象
- **THEN** 系统拒绝导入并指出该记录的位置

### Requirement: Fidelity reporting

系统 SHALL 在导入后报告保真度限制，列明因源数据缺失而无法评估的判据。

#### Scenario: 缺少原始返回值

- **WHEN** 审计记录只提供有界结果摘要而没有原始返回值
- **THEN** 系统在报告中标注状态传递判据无法评估，而不是让使用者以为它可以评估

#### Scenario: 完整记录

- **WHEN** 审计记录包含原始返回值
- **THEN** 系统在报告中不列出该限制

### Requirement: Imported traces are ordinary traces

导入产生的轨迹 SHALL 与平台自身的轨迹具有同等地位，可用于过程求值、指标统计与门禁，且求值时不调用任何工具。

#### Scenario: 对导入轨迹求值

- **WHEN** 用户以导入的轨迹运行过程用例
- **THEN** 系统按既有语义产出判定，且不调用任何工具

#### Scenario: 指标可用

- **WHEN** 对导入轨迹求值
- **THEN** 调用次数、重复调用次数与耗时指标按导入的时间戳推导
