# Spec Delta

## MODIFIED Requirements

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
