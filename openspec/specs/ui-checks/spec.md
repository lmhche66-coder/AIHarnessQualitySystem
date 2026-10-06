# ui-checks Specification

## Purpose
用真实浏览器验证 agent 产出在端上的可交付性：以声明式流程描述用户操作路径，用可替换的驱动执行，失败时留下截图，并把结论并进统一的运行记录。

## Requirements

### Requirement: Declarative UI flow

系统 SHALL 支持以声明方式定义 UI 流程，每个流程 MUST 包含唯一标识、至少一个步骤与至少一条断言。

#### Scenario: 载入流程

- **WHEN** 用户提供一份流程定义
- **THEN** 系统解析为流程对象，并在缺少标识、没有步骤或没有断言时拒绝

#### Scenario: 步骤类型

- **WHEN** 流程声明步骤
- **THEN** 系统支持打开地址、点击、填写与等待四类动作，并对未知动作拒绝

#### Scenario: 断言类型

- **WHEN** 流程声明断言
- **THEN** 系统支持元素可见、元素文本、元素计数与当前地址四类断言，并对未知类型拒绝

### Requirement: Driver protocol

系统 SHALL 通过可替换的驱动协议执行流程，流程定义 SHALL NOT 依赖具体驱动实现。

#### Scenario: 同一流程可换驱动

- **WHEN** 同一份流程用不同驱动执行
- **THEN** 流程定义无需修改

#### Scenario: 驱动不可用

- **WHEN** 指定的驱动未安装
- **THEN** 系统报错并说明需要安装的内容，而不是抛出未处理异常

### Requirement: Failure screenshots

系统 SHALL 在流程失败时保存截图作为产物，并记录其路径。

#### Scenario: 失败时截图

- **WHEN** 流程因断言不满足或步骤执行失败而未通过
- **THEN** 系统保存截图并在判定中给出其路径

#### Scenario: 通过时不截图

- **WHEN** 流程全部通过
- **THEN** 系统不产生截图，避免产物无限增长

#### Scenario: 截图失败不影响结论

- **WHEN** 截图本身失败
- **THEN** 判定结论不受影响，但会记录截图失败的原因

### Requirement: Results enter the standard run record

UI 流程结果 SHALL 写入既有运行记录并产出判定，使控制台与门禁无需改动即可消费。

#### Scenario: 产出运行记录

- **WHEN** 一次 UI 流程执行结束
- **THEN** 运行记录中包含每个流程的判定与元信息

#### Scenario: 步骤失败判定为错误

- **WHEN** 流程中的步骤抛出异常
- **THEN** 该流程判定为 error 并记录原因，其余流程继续执行

### Requirement: Step budget

系统 SHALL 为流程声明步骤上限与总时长上限，超出时判定不通过。

#### Scenario: 超出步骤上限

- **WHEN** 流程声明的步骤数超过上限
- **THEN** 该流程判定为不通过并说明实际步骤数与上限

#### Scenario: 未声明上限

- **WHEN** 流程未声明步骤上限与时长上限
- **THEN** 系统照常执行并只报告实际用量，不给出预算结论
