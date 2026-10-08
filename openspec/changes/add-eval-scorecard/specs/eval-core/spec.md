# Spec Delta

## MODIFIED Requirements

### Requirement: Canonical evaluation data model

系统 SHALL 定义统一的评测数据模型，至少包含评测用例（Case）、执行轨迹（Trace）、判定结果（Verdict）与运行记录（Run）四类对象，并对字段类型与必填项进行校验。评测用例 SHALL 至少区分工具契约用例、过程用例与端到端任务三类，三类用例使用同一套运行与判定载体。用例与判定结果 SHALL 支持可选的归属字段：场景、数据集类型与指标标识；这些字段缺省时 MUST NOT 改变既有判定行为。

#### Scenario: 用例定义被结构化解析

- **WHEN** 用户提供一份评测用例定义
- **THEN** 系统将其解析为 Case 对象，并在字段缺失或类型非法时给出指明字段的错误

#### Scenario: 用例类型可区分

- **WHEN** 用户提供的用例声明了工具契约、过程或端到端任务类型
- **THEN** 系统解析为对应类型的用例，且该类型专属字段被校验

#### Scenario: 运行记录包含完整判定

- **WHEN** 一次运行结束
- **THEN** 系统产出 Run 对象，包含运行标识、起止时间、逐用例 Verdict 与通过/失败/错误计数

#### Scenario: 未标注归属的用例

- **WHEN** 用例未声明场景、数据集类型或指标标识
- **THEN** 系统照常解析与执行，判定结果中相关字段保持为空，既有行为不变

### Requirement: Deterministic assertion execution

系统 SHALL 在不调用 LLM 的前提下执行确定性断言，并仅依据断言结果判定用例状态。断言结果 SHALL 支持可选的指标归口与跳过标记；被标记为跳过的断言 MUST NOT 计入失败断言。

#### Scenario: 全部断言通过

- **WHEN** 某用例的所有确定性断言均满足
- **THEN** 该用例判定为 pass

#### Scenario: 存在不满足的断言

- **WHEN** 某用例存在任一不满足且未被跳过的断言
- **THEN** 该用例判定为 fail，并记录失败断言名称、期望值与实际值

#### Scenario: 存在被跳过的断言

- **WHEN** 某用例的某条断言被标记为因上游错误跳过
- **THEN** 该断言不出现在失败断言列表中，且判定结果保留其跳过标记

#### Scenario: 执行过程抛出非预期异常

- **WHEN** 用例执行过程中出现非预期异常
- **THEN** 该用例判定为 error，异常信息写入判定结果，且整轮运行继续执行其余用例
