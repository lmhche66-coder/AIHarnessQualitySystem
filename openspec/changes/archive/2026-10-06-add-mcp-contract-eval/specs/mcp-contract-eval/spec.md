# Spec Delta

## Purpose

以确定性方式验证 MCP server 的协议行为与工具契约，在不依赖 LLM 与云端服务的前提下覆盖会话握手、能力协商、工具清单、参数校验，以及「声明的 inputSchema 与实际行为是否一致」。

## ADDED Requirements

### Requirement: MCP session handshake

系统 SHALL 通过 stdio 以 JSON-RPC 2.0 建立 MCP 会话：发送 `initialize` 请求、随后发送 `initialized` 通知，并取得 server 返回的协议版本与能力声明。

#### Scenario: 握手成功

- **WHEN** 对一个正常响应的 MCP server 发起会话
- **THEN** 会话完成初始化，并记录 server 返回的协议版本与能力声明

#### Scenario: server 不可用

- **WHEN** server 进程无法启动或在超时内没有响应
- **THEN** 该用例判定为 error，并在错误信息中说明失败原因，其余用例继续

### Requirement: Capability negotiation

系统 SHALL 验证协商结果声明了 tools 能力；未声明时对应判据失败并说明原因。

#### Scenario: 声明 tools 能力

- **WHEN** server 在初始化结果中声明 tools 能力
- **THEN** 能力协商判据通过

#### Scenario: 未声明 tools 能力

- **WHEN** server 的初始化结果中没有 tools 能力
- **THEN** 能力协商判据失败，并指出缺少该能力

### Requirement: Tool inventory and schema declaration

系统 SHALL 通过 `tools/list` 获取工具清单，并 SHALL 验证用例期望的工具存在，且每个列出的工具都声明了输入 schema。

#### Scenario: 期望工具存在且声明 schema

- **WHEN** 用例期望的工具出现在清单中且带有输入 schema
- **THEN** 该判据通过

#### Scenario: 期望工具缺失

- **WHEN** 用例期望的工具没有出现在清单中
- **THEN** 该判据失败，并列出实际可见的工具名

#### Scenario: 工具缺少输入 schema

- **WHEN** 清单中某个工具没有声明输入 schema
- **THEN** 该判据失败，并指出缺少 schema 的工具名

### Requirement: Tool argument validation

系统 SHALL 通过 `tools/call` 验证非法参数被结构化拒绝，覆盖必填参数缺失与参数类型错误，并验证合法参数被接受。

#### Scenario: 必填参数缺失被拒绝

- **WHEN** 调用时省略输入 schema 声明的必填参数
- **THEN** 该判据期望调用返回结构化错误，而不是成功或未处理异常

#### Scenario: 参数类型错误被拒绝

- **WHEN** 传入与输入 schema 不符的参数类型
- **THEN** 该判据期望调用返回结构化错误

#### Scenario: 合法参数被接受

- **WHEN** 传入符合输入 schema 的参数
- **THEN** 该判据期望调用成功返回

### Requirement: Schema and behavior consistency

系统 SHALL 验证 server 声明的输入 schema 与实际行为一致：schema 声明为必填的参数在缺失时必须被拒绝。

#### Scenario: 声明必填且拒绝缺失

- **WHEN** schema 声明某参数必填，且调用省略该参数时被拒绝
- **THEN** 一致性判据通过

#### Scenario: 声明必填却接受缺失

- **WHEN** schema 声明某参数必填，但调用省略该参数时仍然成功
- **THEN** 一致性判据失败，并指出声明与行为矛盾的工具与参数

### Requirement: Results enter the standard run record

MCP 运行结论 SHALL 写入既有运行记录与轨迹，使结论、基线与门禁无需改动即可消费。

#### Scenario: 产出运行记录

- **WHEN** 一次 MCP 运行结束
- **THEN** 运行记录中包含每条用例的判定，且可直接用于基线与门禁判定
