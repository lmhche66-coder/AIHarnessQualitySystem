# repeated-attempts Specification

## Purpose
让任务可以重复尝试，从而区分「做不成」与「不稳定」：每次尝试从干净状态开始并独立判定，最终给出任务在 k 次内至少成功一次的比例与首次即成功的比例。

## Requirements

### Requirement: Repeated attempts

系统 SHALL 支持任务声明尝试次数，每次尝试 MUST 从全新的环境与全新的 agent 开始，且 SHALL NOT 复用上一次尝试的残留状态。

#### Scenario: 多次尝试互相隔离

- **WHEN** 一条任务声明了多次尝试
- **THEN** 每次尝试开始时环境处于初始状态，不包含先前尝试留下的任何改动

#### Scenario: 尝试次数非法

- **WHEN** 任务声明的尝试次数小于一
- **THEN** 系统拒绝该定义，而不是把它当作一次

### Requirement: Per-attempt verdicts

系统 SHALL 为每次尝试产出独立的判定结果，其标识 MUST 包含尝试序号，使门禁可按尝试维度设置阈值。

#### Scenario: 逐次判定

- **WHEN** 一条任务执行三次
- **THEN** 运行记录中出现三条判定，标识分别对应第一、第二与第三次尝试

#### Scenario: 单次尝试的标识

- **WHEN** 一条任务只执行一次
- **THEN** 其判定标识与既有行为一致，不追加尝试序号

### Requirement: pass@k and pass@1 reporting

系统 SHALL 报告任务在全部尝试内至少解决一次的比例，以及首次尝试即解决的比例。

#### Scenario: 波动任务

- **WHEN** 某任务首次尝试未解决、后续尝试解决
- **THEN** 该任务计入解决，且首次通过比例为不通过

#### Scenario: 单次尝试任务

- **WHEN** 一条任务只执行一次
- **THEN** 其解决比例与首次通过比例相同

### Requirement: Attempt report

系统 SHALL 在运行元信息中记录逐任务的尝试数与成功次数，便于判断稳定性而不只是能力上限。

#### Scenario: 逐任务尝试明细

- **WHEN** 一次运行结束
- **THEN** 运行元信息中包含每条任务的尝试数、成功次数与是否解决
