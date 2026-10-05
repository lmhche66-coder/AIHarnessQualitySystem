# Spec Delta

## Purpose

把 agent 的代价变成一等判据：从轨迹推导调用次数、重复调用次数与耗时，允许用例声明上限并在超限时失败，同时汇总已上报的 token 用量，让「做对了但代价失控」不再隐形。

## ADDED Requirements

### Requirement: Per-case runtime metrics

系统 SHALL 从执行轨迹推导每次用例的运行指标，至少包含工具调用次数、重复调用次数与耗时，并把它们记入该用例的判定结果。

#### Scenario: 记录运行指标

- **WHEN** 一条用例执行结束
- **THEN** 其判定结果中包含调用次数、重复调用次数与耗时三个指标

#### Scenario: 轨迹为空

- **WHEN** 一条用例没有产生任何工具调用
- **THEN** 调用次数与重复调用次数为零，耗时不因此变成未知值

### Requirement: Repeated call accounting

重复调用次数 SHALL 按「相同目标工具与相同调用参数」的重复次数统计，用于观测成本随重试膨胀。

#### Scenario: 相同调用重复出现

- **WHEN** 同一目标工具以相同参数被调用三次
- **THEN** 调用次数计为三，重复调用次数计为二

#### Scenario: 参数不同不算重复

- **WHEN** 同一目标工具以不同参数被多次调用
- **THEN** 这些调用不计入重复调用次数

### Requirement: Budget assertions

系统 SHALL 支持用例或任务声明非功能上限，至少覆盖最大调用次数、最大重复调用次数与最大耗时。

#### Scenario: 未超出任何上限

- **WHEN** 实测指标均不超过声明上限
- **THEN** 该预算判据通过

#### Scenario: 超出上限

- **WHEN** 任一实测指标超过其声明上限
- **THEN** 该预算判据失败，并记录指标名称、实测值与上限

#### Scenario: 未声明任何上限

- **WHEN** 一条预算判据没有声明任何上限
- **THEN** 该判据失败并说明未配置，而不是默认通过

#### Scenario: 未声明的指标不参与判定

- **WHEN** 预算判据只声明了部分上限
- **THEN** 只有已声明的指标参与判定，未声明的指标不影响结论

### Requirement: Token usage reporting

系统 SHALL 支持工具结果携带 token 用量，并汇总用例与运行中已上报的用量；对未上报用量的用例，系统 SHALL NOT 臆造数字，且 MUST 标明该运行未覆盖用量统计。

#### Scenario: 汇总已上报用量

- **WHEN** 用例中的工具结果携带了 token 用量
- **THEN** 该系统用例与整次运行分别汇总输入与输出 token 数

#### Scenario: 完全没有上报用量

- **WHEN** 整次运行中没有任何工具结果携带 token 用量
- **THEN** 运行报告中明确标注用量未被覆盖，而不是给出零消耗的结论

### Requirement: Run level metrics report

系统 SHALL 在运行结束后产出运行级指标，至少包含调用总数、重复调用总数、耗时分位数、token 汇总与预算违规清单。

#### Scenario: 产出运行级指标

- **WHEN** 一次运行结束
- **THEN** 运行记录中包含调用总数、重复调用总数、耗时分位数与预算违规清单

#### Scenario: 耗时分位数的定义

- **WHEN** 系统报告耗时分位数
- **THEN** 系统使用确定的算法并在报告中标注所采用的分位数口径
