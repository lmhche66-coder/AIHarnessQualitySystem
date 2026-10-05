# e2e-tasks Specification

## Purpose
让一批业务真实任务可以被可复现地执行并统计通过率：每个任务从干净环境开始，由被测 agent 尝试完成，再依据过程断言与终态断言判定是否解决，最终给出可对业务方交代的成功率。

## Requirements

### Requirement: Task definition

系统 SHALL 支持以声明方式定义端到端任务，每个任务 MUST 至少包含唯一标识、可读描述与至少一条成功判据。

#### Scenario: 任务被结构化解析

- **WHEN** 用户提供一份任务定义
- **THEN** 系统将其解析为任务对象，并在标识缺失或判据为空时给出指明字段的错误

#### Scenario: 任务没有成功判据

- **WHEN** 某任务未声明任何成功判据
- **THEN** 系统拒绝该定义，而不是把它当作默认通过

### Requirement: Reproducible environment lifecycle

系统 SHALL 为每个任务构建一份独立的环境，且 SHALL NOT 在任务之间复用带状态的环境。

#### Scenario: 任务之间环境隔离

- **WHEN** 连续执行两个任务
- **THEN** 第二个任务开始时环境处于初始状态，不包含第一个任务留下的任何改动

#### Scenario: 环境构建失败

- **WHEN** 某任务的环境构建抛出异常
- **THEN** 该任务判定为 error 并记录原因，其余任务继续执行

### Requirement: Agent runner protocol

系统 SHALL 通过统一协议把环境与任务交给被测 agent，且 SHALL NOT 要求把 agent 的解法写进任务定义。

#### Scenario: 调用被测 agent

- **WHEN** 执行一个任务
- **THEN** 系统把该任务的环境与任务定义交给被测 agent，并记录 agent 期间的每一次工具调用

#### Scenario: agent 抛出异常

- **WHEN** 被测 agent 在执行过程中抛出异常
- **THEN** 该任务判定为 error 并记录异常信息，运行继续处理其余任务

### Requirement: Final state assertion

系统 SHALL 支持断言环境中的最终状态等于期望值，并在状态不可读取时给出明确失败而不是崩溃。

#### Scenario: 终态匹配

- **WHEN** 任务结束后被观察工具的状态字段等于期望值
- **THEN** 该终态断言通过

#### Scenario: 终态不匹配

- **WHEN** 任务结束后被观察工具的状态字段不等于期望值
- **THEN** 该终态断言失败，并记录期望值与实际值

#### Scenario: 状态不可读取

- **WHEN** 被观察的工具未注册、未提供状态读取能力，或不含该字段
- **THEN** 该终态断言失败，并说明不可读取的原因

### Requirement: Success evaluation

系统 SHALL 以任务的全部成功判据是否满足来判定该任务是否解决，其中判据可以包含过程断言与终态断言。

#### Scenario: 全部判据满足

- **WHEN** 某任务的过程断言与终态断言全部通过
- **THEN** 该任务被标记为已解决

#### Scenario: 任一判据不满足

- **WHEN** 某任务存在任一不满足的判据
- **THEN** 该任务被标记为未解决，并记录失败判据的名称、期望值与实际值

### Requirement: Resolved rate reporting

系统 SHALL 在运行结束后报告任务通过率与首次尝试通过率，并分别列出未解决与出错的任务标识；任务声明多次尝试时，只要任一次尝试解决即视为该任务已解决。

#### Scenario: 报告通过率

- **WHEN** 一次任务运行结束
- **THEN** 运行记录中包含任务总数、已解决数、通过率、首次尝试通过率，以及未解决与出错的任务清单

#### Scenario: 任意一次尝试解决

- **WHEN** 某任务在多次尝试中至少有一次判定为通过
- **THEN** 该任务计入已解决，且不出现在未解决清单中

#### Scenario: 没有任何任务

- **WHEN** 一次任务运行不包含任何任务
- **THEN** 通过率为零且运行被标记为不通过，而不是空集上的满分

### Requirement: Integration with existing result pipeline

任务结果 SHALL 使用既有的判定与运行记录格式，使质量门禁无需改动即可消费。

#### Scenario: 门禁消费任务运行

- **WHEN** 对一次任务运行执行门禁判定
- **THEN** 阈值与回归判定按既有语义生效，无需针对任务类型做特殊处理
