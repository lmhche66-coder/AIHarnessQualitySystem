# failure-reflow Specification

## Purpose
把一次真实失败变成可反复执行的回归资产：给出结构化归因，保存证据轨迹，并生成能在无 agent 环境下重跑并复现同一失败的候选用例，让质量闭环从「发现问题」接到「守住问题」。

## Requirements

### Requirement: Failure triage report

系统 SHALL 对一次已存储的运行产出归因报告，逐条列出未通过用例的标识、状态、失败判据与失败原因。

#### Scenario: 列出失败用例

- **WHEN** 一次运行中存在失败或出错的用例
- **THEN** 报告逐条列出这些用例，并给出其失败判据的名称、期望值与实际值

#### Scenario: 没有失败用例

- **WHEN** 一次运行中所有用例都通过
- **THEN** 报告为空且不生成任何候选用例

### Requirement: Reproducibility classification

系统 SHALL 对每条失败判断能否仅凭轨迹复现，并在不能复现时给出原因。

#### Scenario: 轨迹可复现的失败

- **WHEN** 某次失败的全部失败判据都只依赖执行轨迹
- **THEN** 该失败被标记为可回流

#### Scenario: 依赖环境状态的失败

- **WHEN** 某次失败存在依赖环境状态的失败判据
- **THEN** 该失败被标记为不可自动回流，并说明它需要原始环境才能复现

#### Scenario: 判定为错误而非失败

- **WHEN** 某用例的判定为 error 而不是 fail
- **THEN** 该用例被标记为不可自动回流，并说明它缺少可复现的判定

### Requirement: Trace evidence capture

系统 SHALL 为可回流的失败保存其证据轨迹，并记录该轨迹的来源运行与用例。

#### Scenario: 保存证据轨迹

- **WHEN** 某条失败被判定为可回流
- **THEN** 系统把该用例的轨迹保存为命名轨迹，名称与来源在报告中可见

#### Scenario: 可回流失败缺少轨迹

- **WHEN** 某条被判定为可回流的失败在运行记录中没有对应轨迹
- **THEN** 系统将其标记为不可回流，并说明缺少轨迹

### Requirement: Candidate case generation

系统 SHALL 为可回流的失败生成候选用例，候选用例 MUST 只保留原用例中依赖轨迹的判据，并绑定其证据轨迹。

#### Scenario: 生成候选用例

- **WHEN** 某条失败被判定为可回流
- **THEN** 系统生成一条绑定证据轨迹的候选用例，沿用原用例中依赖轨迹的判据

#### Scenario: 判据中没有轨迹相关项

- **WHEN** 某条失败的原用例没有任何依赖轨迹的判据
- **THEN** 系统不生成候选用例，并说明原因

### Requirement: Reproduction self-check

系统 SHALL 在产出候选用例前，对证据轨迹重新求值并核对失败判据，未能复现原失败时 MUST 报告为未验证。

#### Scenario: 复现成功

- **WHEN** 候选用例对证据轨迹求值得到与原失败相同的失败判据集合
- **THEN** 该候选用例被标记为已验证

#### Scenario: 复现失败

- **WHEN** 候选用例对证据轨迹求值没有得到相同的失败判据集合
- **THEN** 该候选用例被标记为未验证，并在报告中列出，而不是被当作可用回归用例

### Requirement: Report forms

系统 SHALL 提供机器可读与人类可读两种形式的归因报告，内容 MUST 包含可回流样本、不可回流样本及各自原因。

#### Scenario: 机器可读报告

- **WHEN** 用户以 JSON 形式请求归因结果
- **THEN** 输出包含失败清单、可回流标记、原因、证据轨迹名称与候选用例标识的结构化数据

#### Scenario: 人类可读报告

- **WHEN** 用户以文本形式查看归因结果
- **THEN** 输出按用例分组列出失败判据与可回流结论
