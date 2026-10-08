# Spec Delta

## MODIFIED Requirements

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
