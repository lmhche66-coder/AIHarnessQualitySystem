# redteam-probes Specification

## Purpose
用声明式对抗探针与确定性检测回答「agent 会不会被诱导做不该做的事」，把安全维度从口头约定变成可执行、可回归、可进门禁的判据。

## Requirements

### Requirement: Probe definition

系统 SHALL 支持以声明方式定义红队探针，每条探针 MUST 包含唯一标识、类别、对抗输入与至少一项检测目标。

#### Scenario: 载入探针

- **WHEN** 用户提供一份探针定义
- **THEN** 系统解析为探针对象，并在缺少标识、缺少输入或没有任何检测目标时拒绝

#### Scenario: 类别标注

- **WHEN** 探针声明了类别
- **THEN** 系统在汇总中按类别分组统计，使安全维度可以单独设阈值

### Requirement: Probe target protocol

系统 SHALL 通过可替换的目标协议执行探针，目标接收一条探针并返回响应文本与本次调用记录。

#### Scenario: 接入自定义目标

- **WHEN** 用户提供符合协议的目标
- **THEN** 系统用它执行全部探针，不要求特定 agent 形态

#### Scenario: 目标执行失败

- **WHEN** 目标在响应某条探针时抛出异常
- **THEN** 该条探针判定为 error 并记录原因，其余探针继续执行

### Requirement: Canary leak detection

系统 SHALL 检测响应中是否出现声明的金丝雀内容，出现即判定该探针失败，且 SHALL NOT 把金丝雀内容本身写入判定记录。

#### Scenario: 金丝雀泄漏

- **WHEN** 响应包含探针声明的金丝雀内容
- **THEN** 该探针判定为失败，并在判定中指明是第几个金丝雀被泄漏

#### Scenario: 金丝雀未出现

- **WHEN** 响应不包含任何声明的金丝雀
- **THEN** 该项检测通过

### Requirement: Forbidden tool detection

系统 SHALL 检测调用记录中是否出现声明的禁用工具，出现即判定该探针失败并指明工具名。

#### Scenario: 调用了禁用工具

- **WHEN** 探针执行期间调用了声明的禁用工具
- **THEN** 该探针判定为失败，并记录被调用的工具名

#### Scenario: 未调用禁用工具

- **WHEN** 调用记录中没有声明的禁用工具
- **THEN** 该项检测通过

### Requirement: Forbidden value detection

系统 SHALL 检测调用参数中是否出现声明的禁用取值，出现即判定该探针失败并指明命中的取值。

#### Scenario: 参数携带禁用取值

- **WHEN** 某次调用的参数中包含声明的禁用取值
- **THEN** 该探针判定为失败，并记录命中的取值

#### Scenario: 参数不含禁用取值

- **WHEN** 所有调用的参数都不包含声明的禁用取值
- **THEN** 该项检测通过

### Requirement: Results enter the standard run record

红队结果 SHALL 写入既有运行记录与判定，并按类别汇总。

#### Scenario: 产出运行记录

- **WHEN** 一次红队运行结束
- **THEN** 运行记录中包含每条探针的判定，以及按类别统计的通过失败数
