# Spec Delta

## ADDED Requirements

### Requirement: Structured judge tasks

系统 SHALL 提供面向单一指标的结构化裁判任务，并把任务类型限定为二元判定、单标签分类、多标签匹配、抽取比对、量表评分与成对偏好六种。每个任务 MUST 只评一个指标。

#### Scenario: 一个任务只评一个指标

- **WHEN** 用户为某个指标装配一个裁判任务
- **THEN** 该任务只承载这一个指标，其输出只用于这一个指标的判定

#### Scenario: 按指标装配任务

- **WHEN** 用户给定一组指标标识
- **THEN** 系统按默认归口表为每个指标装配一个类型正确的任务，未知指标被拒绝或显式报错

#### Scenario: 六种任务类型

- **WHEN** 任务声明的类型是六种之一
- **THEN** 系统按该类型的输出契约校验并折算判定

### Requirement: Strict structured output

裁判 SHALL 以严格 JSON 返回结果，其中 MUST 包含 `reasoning` 字段与该类型所需的结论字段。系统 MUST 校验字段与取值，且 MUST NOT 在输出无法解析或字段不合法时静默通过。

#### Scenario: 合法输出

- **WHEN** 裁判返回可解析的 JSON，且包含该类型所需字段与合法取值
- **THEN** 系统据此折算该指标的判定，并保留 reasoning 作为判定说明

#### Scenario: 非 JSON 输出

- **WHEN** 裁判返回的不是可解析的 JSON
- **THEN** 该指标判定为 `error`，说明解析失败的原因，而不是记为通过或失败

#### Scenario: 缺字段或类型不符

- **WHEN** 裁判的输出缺少该类型所需字段，或字段类型、取值不合法
- **THEN** 该指标判定为 `error`，并指出缺失或非法的字段

#### Scenario: 标签越界

- **WHEN** 分类或多标签任务声明了候选标签，而裁判返回了集合外的标签
- **THEN** 该指标判定为 `error`，并指出越界的标签

### Requirement: Per-kind verdict folding

系统 SHALL 按任务类型把结构化结论折算为 `pass` 或 `fail`，并把期望值、实际值与判定说明一并保留。

#### Scenario: 二元判定

- **WHEN** 任务类型为二元判定，且期望为 pass
- **THEN** 裁判结论为 pass 时判为通过，为 fail 时判为不通过

#### Scenario: 单标签与多标签

- **WHEN** 任务类型为单标签或多标签匹配
- **THEN** 系统按标签集合比较期望与实际，完全一致判为通过，否则判为不通过

#### Scenario: 抽取比对

- **WHEN** 任务类型为抽取比对
- **THEN** 系统按声明字段逐项比较期望与实际，全部一致判为通过

#### Scenario: 量表评分

- **WHEN** 任务类型为量表评分
- **THEN** 系统按声明的量表边界与通过阈值折算，分数达到阈值判为通过

#### Scenario: 成对偏好

- **WHEN** 任务类型为成对偏好
- **THEN** 系统按 a/b/tie 的期望结论折算通过与否

### Requirement: Upstream skip

当用例声明上游错误（如路由误触发）时，系统 SHALL 把依赖该上游证据的指标标记为跳过，且 MUST NOT 记为通过或失败。

#### Scenario: 路由错误跳过下游指标

- **WHEN** 用例证据表明路由误触发，且某指标依赖检索证据
- **THEN** 该指标判定为 skipped，既不进入分子也不进入分母，也不出现在失败断言列表

#### Scenario: 不污染无关指标

- **WHEN** 用例存在路由误触发
- **THEN** 不依赖检索证据的指标照常按裁判结论判定

### Requirement: Judge task command

系统 SHALL 提供从用例文件与外部裁判执行 Judge Task 的命令，输出逐指标判定，并写入既有运行记录，使记分卡、门禁与控制台无需改动即可消费。

#### Scenario: 执行裁判任务

- **WHEN** 用户提供用例文件与裁判工厂
- **THEN** 系统为每条用例的每个指标执行裁判任务，产出判定并写入一条运行记录

#### Scenario: 裁判异常

- **WHEN** 裁判对某条用例抛出异常
- **THEN** 该用例判定为 `error` 并记录原因，其余用例继续执行

#### Scenario: 空任务或空用例

- **WHEN** 用例文件为空或没有可执行的任务
- **THEN** 系统以可读错误结束并返回非零退出码，而不是输出空报告
