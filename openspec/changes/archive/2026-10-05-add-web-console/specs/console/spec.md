# Spec Delta

## Purpose

提供一个本地只读的控制台，把运行记录、判定明细、指标与轨迹集中展示出来，让团队的产出可以被看见和使用，而不是散落在终端输出与 JSONL 文件里。

## ADDED Requirements

### Requirement: Local read-only console

系统 SHALL 提供一个本地只读的展示服务，读取既有的运行产物，且 SHALL NOT 修改任何产物；默认 SHALL 绑定回环地址。

#### Scenario: 启动控制台

- **WHEN** 用户以默认参数启动控制台
- **THEN** 服务绑定回环地址并展示既有运行

#### Scenario: 只读

- **WHEN** 控制台展示运行数据
- **THEN** 它只读取既有文件，不写入、不删除、不修改任何产物

#### Scenario: 产物目录不存在

- **WHEN** 运行目录尚不存在
- **THEN** 控制台正常启动并显示空列表，而不是启动失败

### Requirement: Run list

系统 SHALL 按开始时间倒序列出运行，每条至少显示标识、开始时间、用例总数与通过、失败、错误计数。

#### Scenario: 列出多次运行

- **WHEN** 运行目录中存在多次运行
- **THEN** 列表按开始时间从新到旧排列，并逐条显示计数

#### Scenario: 缺少汇总字段的运行

- **WHEN** 某次运行的记录缺少可选字段
- **THEN** 该运行仍出现在列表中，缺失字段以零或空值显示，而不是导致整页失败

### Requirement: Run detail

系统 SHALL 提供单次运行的详情视图，逐用例展示判定状态、耗时、失败判据的名称、期望值与实际值，以及用例级错误信息。

#### Scenario: 查看失败明细

- **WHEN** 用户查看一次存在失败用例的运行
- **THEN** 界面逐条列出该用例的失败判据，并给出期望值与实际值

#### Scenario: 查看错误原因

- **WHEN** 某用例判定为 error
- **THEN** 界面显示其错误信息

### Requirement: Metrics and reports

系统 SHALL 在详情视图中展示运行级指标、预算违规清单、任务通过率与重复尝试统计（若存在）。

#### Scenario: 展示运行级指标

- **WHEN** 运行记录中包含指标
- **THEN** 界面展示调用次数、重复调用次数、耗时分位数与 token 用量，并标注用量是否被覆盖

#### Scenario: 展示任务与尝试统计

- **WHEN** 运行是一次任务运行
- **THEN** 界面展示任务通过率；若包含重复尝试，同时展示首次尝试通过率与 k 次内通过率

#### Scenario: 缺少指标

- **WHEN** 运行记录中没有指标字段
- **THEN** 界面省略该区块，而不是显示误导性的零值

### Requirement: Trace inspection

系统 SHALL 允许查看单条用例的工具调用序列，展示每次调用的目标、参数与结果状态。

#### Scenario: 展开轨迹

- **WHEN** 用户展开某条用例
- **THEN** 界面按发生顺序展示其工具调用，包含目标工具、参数与成功或失败状态

#### Scenario: 没有工具调用

- **WHEN** 某条用例的轨迹中没有工具调用
- **THEN** 界面显示空状态说明，而不是报错

### Requirement: Offline and build-free

控制台 SHALL 在不访问外部网络、不执行前端构建步骤的前提下可用。

#### Scenario: 离线打开

- **WHEN** 用户在无外网的环境打开控制台
- **THEN** 页面样式与交互正常，不依赖任何外部资源
