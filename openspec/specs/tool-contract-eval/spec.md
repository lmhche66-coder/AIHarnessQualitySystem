# tool-contract-eval Specification

## Purpose
以确定性方式验证 agent 工具调用的接口契约与失败语义，在无需 LLM 与真实外部服务的前提下拦截参数、超时、限流、幂等与回滚类缺陷。

## Requirements

### Requirement: Declarative contract cases

系统 SHALL 支持以声明式方式定义工具契约用例，每个用例 SHALL 指定目标工具、调用输入与期望验证的契约类型。

#### Scenario: 用例分派到目标工具

- **WHEN** 用户定义一条契约用例并指定目标工具名与调用输入
- **THEN** 系统在运行时将该用例分派到注册表中对应的工具执行

#### Scenario: 目标工具未注册

- **WHEN** 用例引用了未注册的工具
- **THEN** 系统将该用例判定为 error，并在错误信息中指明未注册的工具名

### Requirement: Argument validation contract

系统 SHALL 验证工具对非法参数的拒绝行为，覆盖必填参数缺失与参数类型错误两类情况，且要求以结构化错误而非未处理异常呈现。

#### Scenario: 必填参数缺失

- **WHEN** 调用时省略一个必填参数
- **THEN** 系统期望工具返回结构化参数校验错误，而不是未处理异常或静默接受

#### Scenario: 参数类型错误

- **WHEN** 调用时传入与工具输入 schema 不符的参数类型
- **THEN** 系统期望工具返回结构化校验错误，并指明非法字段

### Requirement: Timeout contract

系统 SHALL 验证工具在超过调用时限时以受控方式结束，并且不阻塞调用方。

#### Scenario: 调用超时

- **WHEN** 工具执行时间超过用例声明的超时阈值
- **THEN** 系统在阈值内结束调用、记录超时错误，并保证调用过程不悬挂

### Requirement: Rate limit and retry contract

系统 SHALL 验证工具在遭遇限流时的重试行为，覆盖退避后成功与超过重试上限两条路径。

#### Scenario: 限流后退避成功

- **WHEN** 工具前若干次调用返回限流错误、后续调用成功
- **THEN** 系统在重试上限内完成调用，并将结果判定为成功

#### Scenario: 超过重试上限

- **WHEN** 工具持续返回限流错误直至超过重试上限
- **THEN** 系统以结构化限流错误结束，并记录实际重试次数

### Requirement: Idempotent retry contract

系统 SHALL 验证重试不会产生重复副作用。

#### Scenario: 相同幂等键重复调用

- **WHEN** 使用相同幂等键重复发起同一工具调用
- **THEN** 底层副作用仅发生一次，且重复调用返回与首次一致的结果

### Requirement: Partial failure rollback contract

系统 SHALL 验证多步工具操作在任一步骤失败时回滚已完成的步骤。

#### Scenario: 中间步骤失败

- **WHEN** 一个多步操作在中途步骤失败
- **THEN** 系统期望此前已完成步骤的副作用被撤销，并记录回滚结果
