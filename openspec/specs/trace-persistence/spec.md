# trace-persistence Specification

## Purpose
把 agent 的执行轨迹保存为可复用的命名资产，让过程断言既能作用于当场驱动的步骤，也能反复作用于一份固定的真实轨迹，从而在没有 agent 的环境里稳定复评。

## Requirements

### Requirement: Named trace persistence

系统 SHALL 支持把一条执行轨迹以名称保存到运行根目录下的轨迹目录，并能按名称重新读取与列举。

#### Scenario: 保存后可重新读取

- **WHEN** 用户以名称保存一条轨迹
- **THEN** 系统写入该轨迹的事件序列与元信息，且随后能按该名称完整读回

#### Scenario: 列举已保存轨迹

- **WHEN** 轨迹目录中存在若干条已保存轨迹
- **THEN** 系统按名称排序列举它们

#### Scenario: 读取不存在的轨迹

- **WHEN** 用户按一个不存在的名称读取轨迹
- **THEN** 系统报错并指明该名称，而不是返回空轨迹

### Requirement: Trace capture from a run

系统 SHALL 支持从一次已存储的运行中提取指定用例的轨迹，并保存为命名轨迹。

#### Scenario: 按用例标识提取

- **WHEN** 用户指定运行标识与用例标识
- **THEN** 系统提取该用例的轨迹并保存为指定名称

#### Scenario: 未指定用例标识且运行含多条轨迹

- **WHEN** 用户未指定用例标识，而被引用的运行包含多条轨迹
- **THEN** 系统报错并要求用户指明用例，而不是任意挑选一条

#### Scenario: 提取的轨迹记录来源

- **WHEN** 系统从运行中提取轨迹并保存
- **THEN** 该轨迹的元信息记录来源运行标识，便于追溯

### Requirement: Evaluating process cases against a stored trace

系统 SHALL 支持过程用例引用一条已保存轨迹并直接对该轨迹执行过程断言，引用可以来自用例自身，也可以来自运行级参数，用例级引用优先。

#### Scenario: 对已保存轨迹求值

- **WHEN** 用户引用一条已保存轨迹运行过程用例
- **THEN** 系统不调用任何工具，直接对该轨迹执行过程断言并产出判定

#### Scenario: 用例自带轨迹引用

- **WHEN** 某条过程用例自己引用了已保存轨迹
- **THEN** 该用例使用自己的引用，即使本次运行另外指定了运行级轨迹

#### Scenario: 引用的轨迹不存在

- **WHEN** 某条过程用例引用的已保存轨迹不存在
- **THEN** 该用例判定为 error 并说明缺失的轨迹名称，其余用例继续执行

#### Scenario: 运行记录标注来源

- **WHEN** 一次运行使用了已保存轨迹
- **THEN** 运行元信息记录该轨迹的名称，判定所依据的轨迹同时进入运行记录

### Requirement: Mutually exclusive trace sources

系统 SHALL 拒绝同时提供步骤与已保存轨迹的过程用例，以避免两个来源被悄悄混用。

#### Scenario: 步骤与外部轨迹同时存在

- **WHEN** 引用已保存轨迹运行，而某条过程用例又声明了步骤
- **THEN** 该用例判定为 error 并说明两个来源冲突

#### Scenario: 既无步骤也无轨迹

- **WHEN** 运行一条既未声明步骤、又没有引用已保存轨迹的过程用例
- **THEN** 该用例判定为 error 并说明缺少轨迹来源

### Requirement: Missing trace reference is rejected

系统 SHALL 在引用的轨迹不存在时报错并以非零退出码结束，且不写入运行记录。

#### Scenario: 引用不存在的轨迹

- **WHEN** 用户引用一条不存在的轨迹运行
- **THEN** 系统报错、以非零退出码结束，且不产生新的运行记录
