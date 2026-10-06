# Spec Delta

## Purpose

把「评测所依赖的外部环境」变成可启动、可检查、可快照、可重置的对象，消除环境状态漂移带来的评测噪音。

## ADDED Requirements

### Requirement: Declarative sandbox definition

系统 SHALL 支持以声明式文件定义沙箱，声明 compose 文件、项目名、参与的服务、重置策略与健康要求，并 SHALL 在缺少必要字段时拒绝载入。

#### Scenario: 载入沙箱定义

- **WHEN** 用户提供包含 compose 文件与项目名的沙箱定义
- **THEN** 系统解析出该定义并可用于启动与重置

#### Scenario: 定义不完整

- **WHEN** 定义缺少 compose 文件或项目名
- **THEN** 系统在载入时拒绝并指明缺失字段

### Requirement: Sandbox lifecycle commands

系统 SHALL 支持对沙箱执行启动、停止、健康检查、重置与快照，并 SHALL 在命令失败时给出包含底层输出的可读错误。

#### Scenario: 启动沙箱

- **WHEN** 用户对沙箱执行启动
- **THEN** 系统以定义中的 compose 文件与项目名启动声明的服务

#### Scenario: 停止沙箱

- **WHEN** 用户对沙箱执行停止
- **THEN** 系统停止该项目，并可按请求一并移除数据卷

#### Scenario: 命令失败

- **WHEN** 底层 docker 命令以非零状态结束
- **THEN** 系统判失败，并在错误信息中保留命令与底层输出

### Requirement: Health check

系统 SHALL 能在指定超时内等待声明的服务就绪，并 SHALL 区分「就绪」「未就绪」与「命令失败」三种结果。

#### Scenario: 服务就绪

- **WHEN** 声明的服务在超时内全部进入运行且健康的状体
- **THEN** 健康检查通过

#### Scenario: 服务未就绪

- **WHEN** 超过超时仍有服务未健康
- **THEN** 健康检查失败，并列出未就绪的服务与当前状态

### Requirement: Reset strategies

系统 SHALL 支持三种重置策略：销毁数据卷后重建、从已保存的卷快照恢复、以及只检查健康不做重置。

#### Scenario: 重建式重置

- **WHEN** 策略为重建
- **THEN** 系统移除该项目的数据卷并重新启动，使环境回到全新状态

#### Scenario: 快照式重置

- **WHEN** 策略为快照且有可用的快照
- **THEN** 系统从该快照恢复数据卷并重新启动

#### Scenario: 快照缺失

- **WHEN** 策略为快照但快照不存在
- **THEN** 系统判失败并提示先生成快照，而不是静默退化为重建

#### Scenario: 只检查

- **WHEN** 策略为不做重置
- **THEN** 系统只执行健康检查，不改变环境

### Requirement: Volume snapshot

系统 SHALL 能把沙箱的数据卷保存为可恢复的快照，并在恢复时把内容写回对应卷。

#### Scenario: 生成快照

- **WHEN** 用户对沙箱生成快照
- **THEN** 系统把该项目的数据卷内容保存到指定位置，并报告涉及的卷

#### Scenario: 从快照恢复

- **WHEN** 用户以某快照重置沙箱
- **THEN** 数据卷内容被恢复为该快照的状态
