# Spec Delta

## MODIFIED Requirements

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
