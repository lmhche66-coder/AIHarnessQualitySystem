# agent-bridge Specification

## Purpose
让任意语言、任意进程、任意主机的 agent 都能接入平台的评测能力，而不要求它成为平台进程内可导入的 Python 对象。

## Requirements

### Requirement: Bridge wire protocol

系统 SHALL 用一份自描述的 JSON 请求与响应与 agent 通信。请求 MUST 标明协议版本、应用标识、能力、用例数据与可用工具清单；响应 MUST 能表达文本输出、工具调用与用量，并 SHALL 支持可选的模块信号列表；模块信号 MUST 标识所属模块，载荷与耗时字段可缺省。

#### Scenario: 发起一次评测调用

- **WHEN** 平台对某个 agent 发起一次评测调用
- **THEN** 请求中带有协议版本、能力标识与对应的用例数据

#### Scenario: 解析 agent 响应

- **WHEN** agent 返回文本输出、工具调用与用量
- **THEN** 平台把三者解析为统一结构供各层消费

#### Scenario: agent 上报模块信号

- **WHEN** agent 响应包含模块信号列表
- **THEN** 平台在用例执行期把每条信号写入当前轨迹，供模块级断言读取

#### Scenario: agent 未上报模块信号

- **WHEN** agent 响应不包含模块信号列表
- **THEN** 平台照常处理该响应，既有协议行为不变

### Requirement: HTTP transport

系统 SHALL 支持以 HTTP POST 调用远端 agent endpoint，并 SHALL 支持自定义请求头与超时。

#### Scenario: 远端调用成功

- **WHEN** agent 以 HTTP endpoint 形式暴露且返回合法响应
- **THEN** 平台取得并解析该响应

#### Scenario: 端点不可达或超时

- **WHEN** agent endpoint 不可达或在超时内没有响应
- **THEN** 该用例判定为 error，并在错误信息中说明原因

### Requirement: Subprocess transport

系统 SHALL 支持以子进程方式启动本地 agent，并 SHALL 通过 stdin/stdout 的按行 JSON 协议与其通信，且 SHALL 在结束时回收子进程。

#### Scenario: 子进程调用成功

- **WHEN** 本地 agent 以子进程方式启动并返回合法响应
- **THEN** 平台取得并解析该响应，随后回收子进程

#### Scenario: 子进程崩溃

- **WHEN** 子进程在返回响应前退出
- **THEN** 该用例判定为 error，不留下悬挂进程

### Requirement: Capability adaptation

系统 SHALL 让同一 agent 通过同一份协议接入 task、dialogue、redteam、selection 四类评测能力，由平台负责在各能力的数据结构与协议请求之间转换。

#### Scenario: 一个 endpoint 复用四类能力

- **WHEN** 同一个 agent 被用于不同能力类别的评测
- **THEN** 平台用同一份协议发起调用，并按能力填充对应的用例数据

#### Scenario: agent 未声明支持该能力

- **WHEN** agent 的能力声明不包含当前评测类别
- **THEN** 该用例判定为 error，并指出不支持的能力

### Requirement: Tool execution modes

系统 SHALL 支持两种工具执行模式。平台执行模式下 agent 返回工具调用、平台执行并把结果回传以驱动多步循环；agent 执行模式下 agent 自行执行工具并回报调用记录，平台 SHALL NOT 重复执行这些工具，而是把调用记入当前用例的轨迹，使过程断言可用。两种模式 SHALL 都适用于 task 能力。

#### Scenario: 平台执行模式

- **WHEN** agent 返回工具调用且模式为平台执行
- **THEN** 平台调用对应工具、记录调用，并把结果回传给 agent 继续该用例

#### Scenario: agent 执行模式

- **WHEN** agent 自行执行工具并回报调用记录
- **THEN** 平台不重复执行工具，只记录调用供过程断言使用

#### Scenario: task 能力使用 agent 执行模式

- **WHEN** 一个自带工具与审计的 agent 以 agent 执行模式接入 task 能力
- **THEN** 该用例照常运行，且它回报的调用出现在该用例的轨迹里，可被调用顺序与多余调用断言消费

#### Scenario: 多步循环超过上限

- **WHEN** 平台执行模式下的工具调用循环达到步数上限仍未结束
- **THEN** 该用例判定为失败，并说明循环被截断

### Requirement: Usage reporting

agent 上报的输入与输出用量 SHALL 并入该次运行的指标。

#### Scenario: 用量计入指标

- **WHEN** agent 在响应中报告 token 用量
- **THEN** 该用量出现在对应判定的指标与运行汇总中

### Requirement: Protocol version and failure isolation

系统 SHALL 在协议版本不受支持时拒绝该调用；单条用例的 agent 故障 SHALL NOT 终止同一次运行的其他用例。

#### Scenario: 协议版本不受支持

- **WHEN** 请求中的协议版本不被 agent 支持
- **THEN** 平台把该用例判为 error，并指出协议版本不匹配

#### Scenario: 单条用例故障隔离

- **WHEN** 某条用例因 agent 故障被判为 error
- **THEN** 其余用例照常执行并各自产出判定

### Requirement: Results enter the standard run record

Bridge 运行结论 SHALL 写入既有运行记录与轨迹，使基线与门禁无需改动即可消费。

#### Scenario: 产出运行记录

- **WHEN** 一次 Bridge 运行结束
- **THEN** 运行记录包含每条用例的判定与所产生的工具调用轨迹
