# Spec Delta

## Purpose

把工具调用录制成可回放的 cassette，使评测在无外部依赖、无网络的前提下确定性重放，并让「相同请求、不同响应」的重试场景可被精确复现。

## ADDED Requirements

### Requirement: Cassette persistence

系统 SHALL 把工具交互持久化为可读写的 cassette 文件，每条交互 MUST 至少包含请求指纹、目标工具、脱敏后的请求参数与响应结果。

#### Scenario: 录制后 cassette 文件可用

- **WHEN** 一次录制模式运行结束
- **THEN** cassette 文件被写入运行根目录下的 cassettes 目录，且内容可被重新读取

#### Scenario: 回放读取既有 cassette

- **WHEN** 回放模式运行且指定的 cassette 已存在
- **THEN** 系统从该文件恢复全部交互，不触达真实工具

### Requirement: Deterministic request fingerprint

系统 SHALL 依据目标工具与调用参数生成稳定的请求指纹，指纹 MUST 与参数键的书写顺序无关。

#### Scenario: 参数键序不同但语义相同

- **WHEN** 两次调用传入内容相同但键顺序不同的参数
- **THEN** 两次生成相同指纹

#### Scenario: 参数取值不同

- **WHEN** 两次调用的参数取值不同
- **THEN** 两次生成不同指纹

### Requirement: Ordered replay of repeated requests

对同一指纹的重复调用，系统 SHALL 按录制顺序依次回放响应，而不是重复返回同一结果。

#### Scenario: 相同请求先失败后成功

- **WHEN** 录制时同一请求依次产生两次限流失败与一次成功
- **THEN** 回放时前两次调用返回限流失败，第三次返回成功

#### Scenario: 耗尽同一指纹的响应

- **WHEN** 回放时对同一指纹的调用次数超过录制次数
- **THEN** 系统按 cassette 缺失处理，而不是重复最后一次响应

### Requirement: Record mode captures real interactions

录制模式下系统 SHALL 真实调用工具，并把成功与失败结果一并写入 cassette。

#### Scenario: 记录失败结果

- **WHEN** 录制时工具返回结构化失败
- **THEN** 该失败结果被写入 cassette，并在回放时原样返回

### Requirement: Hermetic replay

回放模式下系统 SHALL NOT 调用真实工具；请求在 cassette 中不存在时，系统 SHALL 返回明确的缺失错误并将该用例判定为 error。

#### Scenario: cassette 缺失

- **WHEN** 回放模式遇到未录制的请求
- **THEN** 系统返回缺失错误，且真实工具未被调用

#### Scenario: 真实工具不可用

- **WHEN** 回放模式运行且真实工具在被调用时必然失败
- **THEN** 运行结果不受影响，证明回放过程未触达真实工具

### Requirement: Unused interaction reporting

系统 SHALL 在回放结束后报告 cassette 中未被使用的交互；严格模式下 SHALL 将未使用交互判定为失败。

#### Scenario: 存在未使用交互

- **WHEN** 回放结束后 cassette 仍有未被消费的交互
- **THEN** 系统在运行记录中报告这些交互的数量与指纹

#### Scenario: 严格模式下未使用交互

- **WHEN** 严格模式开启且存在未使用交互
- **THEN** 该次运行被判定为失败

### Requirement: Sensitive field redaction

系统 SHALL 在写入 cassette 之前对声明的敏感字段做脱敏，且脱敏 SHALL NOT 影响请求指纹的计算。

#### Scenario: 敏感字段不落盘

- **WHEN** 调用参数包含声明的敏感字段
- **THEN** cassette 文件中该字段的值为脱敏占位符，原始值不出现在文件内容中

#### Scenario: 脱敏后仍可匹配

- **WHEN** 使用与录制时相同的原始参数发起回放
- **THEN** 系统仍能匹配到对应交互

### Requirement: Cassette usage observability

系统 SHALL 在用例轨迹中记录每次工具调用的 cassette 命中或缺失。

#### Scenario: 记录命中

- **WHEN** 回放模式成功命中一次交互
- **THEN** 该用例轨迹中出现一条命中事件

#### Scenario: 记录缺失

- **WHEN** 回放模式遇到缺失
- **THEN** 该用例轨迹中出现一条带错误信息的缺失事件

### Requirement: Non-replayable contracts are rejected

对依赖实际调用耗时才能判定的契约，系统 SHALL NOT 给出通过或失败结论；在非录制模式下系统 SHALL 改为产出明确的错误判定。

#### Scenario: 回放模式下遇到耗时相关契约

- **WHEN** 非录制模式下用例的检查依赖真实调用耗时
- **THEN** 该用例被判定为 error，并说明该契约无法从 cassette 回放

#### Scenario: 录制模式下仍可正常判定

- **WHEN** 录制模式下执行耗时相关契约
- **THEN** 该用例按真实调用耗时正常判定，不受该限制影响
