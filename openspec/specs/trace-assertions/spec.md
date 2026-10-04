# trace-assertions Specification

## Purpose
对 agent 的执行轨迹做过程级断言：把工具调用归一化为可检查的序列，验证工具选得对不对、顺序是否合理、有没有多余步骤、失败后是否恢复，以及前一步的状态有没有传到下一步。

## Requirements

### Requirement: Tool call trace normalization

系统 SHALL 把执行轨迹中的工具调用归一化为结构化序列，每条记录 MUST 包含目标工具、调用参数、成功与否与错误类别。

#### Scenario: 从轨迹提取工具调用序列

- **WHEN** 轨迹中包含若干次工具调用事件
- **THEN** 系统按发生顺序输出结构化调用序列，索引从零开始递增

#### Scenario: 轨迹中没有工具调用

- **WHEN** 轨迹中不含任何工具调用事件
- **THEN** 系统输出空序列，而不是报错

### Requirement: Tool call instrumentation

系统 SHALL 提供工具包装器，使任意 agent 的工具调用被记录为轨迹中的结构化事件。

#### Scenario: 包装后记录调用

- **WHEN** 用户用该包装器包装工具并发起调用
- **THEN** 当前轨迹中出现一条包含目标、参数与结果的调用事件

#### Scenario: 没有绑定轨迹

- **WHEN** 包装器被调用但未绑定任何轨迹
- **THEN** 调用正常返回结果，不因缺少轨迹而失败

### Requirement: Tool sequence assertion

系统 SHALL 支持断言工具调用的顺序，并提供子序列与完全匹配两种模式。

#### Scenario: 子序列匹配

- **WHEN** 期望序列按顺序出现在实际调用中，且中间存在其他调用
- **THEN** 该断言通过

#### Scenario: 完全匹配

- **WHEN** 实际调用序列与期望序列逐项一致
- **THEN** 完全匹配断言通过；存在顺序差异或多出调用时失败

### Requirement: Extra call assertion

系统 SHALL 支持断言除允许集合之外没有其他工具被调用。

#### Scenario: 存在多余调用

- **WHEN** 实际调用中出现不在允许集合内的工具
- **THEN** 该断言失败并列出多余的工具

### Requirement: Recovery assertion

系统 SHALL 支持断言某次失败之后存在成功的后续调用。

#### Scenario: 失败后恢复

- **WHEN** 目标工具先失败、随后出现一次成功调用
- **THEN** 该断言通过，并记录失败位置与恢复位置

#### Scenario: 失败后未恢复

- **WHEN** 目标工具失败之后没有任何成功调用
- **THEN** 该断言失败

#### Scenario: 未观察到失败

- **WHEN** 目标工具从未失败
- **THEN** 该断言失败，因为恢复路径未被实际验证

### Requirement: State continuity assertion

系统 SHALL 支持断言前一次调用的产出被传递到后续调用的参数中。

#### Scenario: 状态被传递

- **WHEN** 生产者的产出值出现在后续消费者的调用参数中
- **THEN** 该断言通过

#### Scenario: 状态丢失

- **WHEN** 消费者的调用参数中不包含生产者的任何产出值
- **THEN** 该断言失败，并给出生产值与实际参数

#### Scenario: 缺少必要的调用

- **WHEN** 轨迹中没有成功的生产者调用，或没有任何消费者调用
- **THEN** 该断言失败并说明缺少哪一侧

### Requirement: Process case execution

系统 SHALL 支持过程用例声明要执行的工具调用步骤与要验证的过程断言，并与其他用例一样产出判定结果。

#### Scenario: 过程用例产出判定

- **WHEN** 运行一条过程用例
- **THEN** 系统按声明顺序执行步骤、记录轨迹，并依据过程断言产出 pass 或 fail

#### Scenario: 步骤引用未注册工具

- **WHEN** 过程用例的某个步骤引用了未注册的工具
- **THEN** 该用例判定为 error，并指明未注册的工具名

#### Scenario: 结果可被门禁消费

- **WHEN** 过程用例参与一次运行
- **THEN** 其判定与其他用例一样进入运行记录与门禁统计
