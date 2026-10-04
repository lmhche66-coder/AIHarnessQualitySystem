# Spec Delta

## MODIFIED Requirements

### Requirement: Process case execution

系统 SHALL 支持过程用例声明要执行的工具调用步骤或引用一条已保存轨迹，并据此执行要验证的过程断言，与其他用例一样产出判定结果。

#### Scenario: 过程用例产出判定

- **WHEN** 运行一条声明了步骤的过程用例
- **THEN** 系统按声明顺序执行步骤、记录轨迹，并依据过程断言产出 pass 或 fail

#### Scenario: 对已保存轨迹求值

- **WHEN** 运行一条过程用例并引用一条已保存轨迹
- **THEN** 系统直接对该轨迹执行过程断言，不调用任何工具

#### Scenario: 步骤引用未注册工具

- **WHEN** 过程用例的某个步骤引用了未注册的工具
- **THEN** 该用例判定为 error，并指明未注册的工具名

#### Scenario: 结果可被门禁消费

- **WHEN** 过程用例参与一次运行
- **THEN** 其判定与其他用例一样进入运行记录与门禁统计
