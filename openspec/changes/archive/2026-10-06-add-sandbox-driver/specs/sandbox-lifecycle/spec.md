# Spec Delta

## Purpose

把沙箱接到评测运行上：整轮开始前确认环境可用，每个用例前把环境重置到已知状态，并把环境版本写进结论，使端到端结果可以追溯到具体环境。

## ADDED Requirements

### Requirement: Pre-run readiness gate

系统 SHALL 在开始评测前确认沙箱可用；沙箱不可用时该轮运行 SHALL 直接失败，而不是让用例在坏环境上产出不可信结论。

#### Scenario: 环境可用

- **WHEN** 沙箱在超时内就绪
- **THEN** 评测照常开始

#### Scenario: 环境不可用

- **WHEN** 沙箱在超时内未就绪或启动失败
- **THEN** 该轮运行失败并说明环境问题，用例不产出判定

### Requirement: Per-case reset

系统 SHALL 在每个用例开始前按定义的重置策略把沙箱恢复到已知状态，使用例之间不相互污染。

#### Scenario: 用例之间隔离

- **WHEN** 一轮运行包含多个用例
- **THEN** 每个用例开始前沙箱被重置到已知状态

#### Scenario: 重置失败

- **WHEN** 某次重置失败
- **THEN** 该用例判为 error，并说明重置失败的原因，其余用例继续

### Requirement: Sandbox version binding

系统 SHALL 把沙箱的项目名、compose 文件内容哈希与镜像标签写入运行记录。

#### Scenario: 记录环境版本

- **WHEN** 一次带沙箱的运行结束
- **THEN** 运行记录中带有沙箱标识、compose 哈希与镜像标签

### Requirement: Teardown policy

系统 SHALL 支持在整轮结束后按配置保留或销毁沙箱，且 SHALL NOT 在默认情况下销毁用户的环境。

#### Scenario: 默认保留

- **WHEN** 用户未声明销毁
- **THEN** 运行结束后沙箱保持运行

#### Scenario: 显式销毁

- **WHEN** 用户声明运行结束后销毁
- **THEN** 系统在运行结束后停止该项目

### Requirement: Backward compatibility

未提供沙箱时，任务运行的行为 SHALL 与引入该能力之前完全一致。

#### Scenario: 不使用沙箱

- **WHEN** 运行任务时未指定沙箱
- **THEN** 不执行任何环境启停或重置动作，行为与之前一致
