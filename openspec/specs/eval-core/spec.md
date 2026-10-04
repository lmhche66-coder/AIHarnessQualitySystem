# eval-core Specification

## Purpose
提供评测平台的最小核心：统一的用例、轨迹、判定与运行记录模型，不依赖 LLM 的确定性断言执行，以及可复现、可回读的运行结果持久化。

## Requirements

### Requirement: Canonical evaluation data model

系统 SHALL 定义统一的评测数据模型，至少包含评测用例（Case）、执行轨迹（Trace）、判定结果（Verdict）与运行记录（Run）四类对象，并对字段类型与必填项进行校验。评测用例 SHALL 至少区分工具契约用例与过程用例两类，两类用例使用同一套运行与判定载体。

#### Scenario: 用例定义被结构化解析

- **WHEN** 用户提供一份评测用例定义
- **THEN** 系统将其解析为 Case 对象，并在字段缺失或类型非法时给出指明字段的错误

#### Scenario: 用例类型可区分

- **WHEN** 用户提供的用例声明了工具契约或过程类型
- **THEN** 系统解析为对应类型的用例，且该类型专属字段被校验

#### Scenario: 运行记录包含完整判定

- **WHEN** 一次运行结束
- **THEN** 系统产出 Run 对象，包含运行标识、起止时间、逐用例 Verdict 与通过/失败/错误计数

### Requirement: Deterministic assertion execution

系统 SHALL 在不调用 LLM 的前提下执行确定性断言，并仅依据断言结果判定用例状态。

#### Scenario: 全部断言通过

- **WHEN** 某用例的所有确定性断言均满足
- **THEN** 该用例判定为 pass

#### Scenario: 存在不满足的断言

- **WHEN** 某用例存在任一不满足的断言
- **THEN** 该用例判定为 fail，并记录失败断言名称、期望值与实际值

#### Scenario: 执行过程抛出非预期异常

- **WHEN** 用例执行过程中出现非预期异常
- **THEN** 该用例判定为 error，异常信息写入判定结果，且整轮运行继续执行其余用例

### Requirement: Run result persistence

系统 SHALL 将运行结果持久化到项目内的运行目录，格式需支持机器读取与逐条追加。

#### Scenario: 运行结果可被重新读取

- **WHEN** 用户执行一次运行，随后按运行标识查询
- **THEN** 系统从运行目录恢复该次运行的完整判定记录

#### Scenario: 运行目录不存在

- **WHEN** 目标运行目录尚不存在
- **THEN** 系统自动创建目录并完成写入，不要求用户手工准备

#### Scenario: 运行目录可配置

- **WHEN** 用户通过环境变量指定运行目录
- **THEN** 系统将结果写入该目录，而不是默认目录
