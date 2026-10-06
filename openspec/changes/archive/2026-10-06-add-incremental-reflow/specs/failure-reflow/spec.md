# Spec Delta

## ADDED Requirements

### Requirement: Incremental failure collection

系统 SHALL 能扫描已存储的全部运行，并在无需人工指定运行标识的前提下发现失败样本。

#### Scenario: 扫描全部运行

- **WHEN** 用户执行一次增量回流
- **THEN** 系统遍历已存储的运行，并对其中的失败与错误逐条评估是否可回流

#### Scenario: 没有新的失败

- **WHEN** 已存储的运行里没有此前未处理过的失败
- **THEN** 系统报告扫描结果并保持数据集不变

### Requirement: Failure fingerprinting

系统 SHALL 为每个失败推导指纹，指纹 MUST 由用例标识与该次失败的判据组成，使同一失败模式只被回流一次、不同失败模式互不吞并。

#### Scenario: 同一失败重复出现

- **WHEN** 同一个用例以完全相同的失败判据在多次运行中失败
- **THEN** 系统只回流一次，后续出现计为重复

#### Scenario: 同一用例的不同失败模式

- **WHEN** 同一个用例以不同的失败判据失败
- **THEN** 系统把它们视为两个样本，各自产出候选用例且标识互不冲突

### Requirement: Reflow ledger

系统 SHALL 持久化已处理指纹与其首次发现时间，使增量回流可重复执行且幂等。

#### Scenario: 重复执行

- **WHEN** 用户在没有新失败的情况下重复执行增量回流
- **THEN** 数据集不新增任何用例，报告显示全部命中已处理

### Requirement: Cumulative regression dataset

系统 SHALL 把通过自证的候选用例累积写入数据集文件，重复执行只追加不覆盖既有内容。

#### Scenario: 累积写入

- **WHEN** 一次回流产出新的候选用例
- **THEN** 它们被追加到数据集，既有用例保持不变

#### Scenario: 不写入未通过自证的候选

- **WHEN** 某条候选用例未能在证据轨迹上复现原失败
- **THEN** 该候选不进入数据集，并在报告中说明

### Requirement: Reflow reporting

系统 SHALL 报告本次扫描的运行数、失败数、新增数、重复数、不可回流数与最终写入数，使回流过程可审计。

#### Scenario: 产出报告

- **WHEN** 一次增量回流结束
- **THEN** 报告包含上述计数与每条新增样本的来源运行
