# agent-registry Specification

## Purpose
让平台管理公司内部多个 agent 应用：按标识解析接入方式、把评测绑定到具体版本、声明各自支持的能力，并在评测前发现不可用。

## Requirements

### Requirement: Declarative agent registry

系统 SHALL 支持以声明式文件注册多个 agent 应用，每个条目 MUST 声明标识、版本与接入方式，并 MAY 声明能力矩阵、请求头与超时。

#### Scenario: 载入注册表

- **WHEN** 用户提供一个包含多个 agent 条目的注册表文件
- **THEN** 系统解析出每个应用的标识、版本与接入方式

#### Scenario: 注册表缺少必要字段

- **WHEN** 某条目缺少标识、版本或接入方式
- **THEN** 系统在载入时拒绝并指明问题条目

### Requirement: Reference resolution

系统 SHALL 支持以应用标识引用注册表中的 agent，并 SHALL 在标识不存在时给出可读错误。

#### Scenario: 按标识解析

- **WHEN** 命令行以应用标识引用 agent
- **THEN** 系统从注册表取出对应条目并按该条目建立连接

#### Scenario: 标识不存在

- **WHEN** 命令行引用的应用标识不在注册表中
- **THEN** 系统在开始评测前报错并列出可用标识

### Requirement: Version binding

系统 SHALL 把被评测应用的标识与版本写入运行记录，使结论可追溯到具体部署版本。

#### Scenario: 记录应用版本

- **WHEN** 一次针对注册表中 agent 的运行结束
- **THEN** 运行记录中带有该应用的标识与版本

### Requirement: Capability declaration

注册条目 SHALL 能声明该应用支持的能力类别；当评测类别不在声明内时，系统 SHALL 拒绝该用例并说明。

#### Scenario: 能力已声明

- **WHEN** 评测类别包含在应用的能力声明中
- **THEN** 该用例照常执行

#### Scenario: 能力未声明

- **WHEN** 评测类别不在应用的能力声明中
- **THEN** 该用例判定为 error，并指出不支持的能力

### Requirement: Availability check

系统 SHALL 能在开始评测前检查注册条目是否可用，并 SHALL 以可读错误报告不可用的原因。

#### Scenario: 应用可用

- **WHEN** 注册的 endpoint 或命令可被访问
- **THEN** 可用性检查通过

#### Scenario: 应用不可用

- **WHEN** 注册的 endpoint 不可达或命令无法启动
- **THEN** 可用性检查失败并说明原因

### Requirement: Results enter the standard run record

注册表驱动的评测 SHALL 复用既有运行记录、基线与门禁，无需为 Bridge 单独建立一套结论格式。

#### Scenario: 复用既有结论格式

- **WHEN** 一次注册表驱动的评测结束
- **THEN** 结论以既有运行记录格式落盘，并可直接用于基线与门禁
