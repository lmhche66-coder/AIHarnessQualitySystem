# multi-turn-dialogue Specification

## Purpose
把多轮交互变成可断言的对象：平台掌握对话循环，agent 只负责一轮，用户由可替换的模拟器扮演，从而能验证「先问再做」「几轮收敛」「有没有收尾」这类只在多轮场景里才存在的问题。

## Requirements

### Requirement: Conversation model

系统 SHALL 定义带角色的对话模型，回合序列中每个回合 MUST 标明角色与内容，agent 回合 MAY 携带该轮发生的工具调用。

#### Scenario: 记录对话回合

- **WHEN** 一轮交互完成
- **THEN** 对话中追加一个带角色与内容的回合

#### Scenario: 工具调用归属到轮次

- **WHEN** agent 在某一轮内调用了工具
- **THEN** 这些调用被记录在该 agent 回合上，而不是混在整段对话里

### Requirement: Turn-based agent protocol

系统 SHALL 通过逐轮协议驱动 agent：agent 接收当前对话与工具环境，返回本轮回复。系统 SHALL NOT 要求修改既有的一次性任务协议。

#### Scenario: 逐轮驱动

- **WHEN** 运行一条对话用例
- **THEN** 平台在每一轮把当前对话交给 agent，并取得本轮回复后继续循环

#### Scenario: 既有协议不受影响

- **WHEN** 使用既有的任务协议运行任务集
- **THEN** 行为与新增对话能力之前完全一致

### Requirement: User simulator

系统 SHALL 通过可替换的模拟器产生用户消息，并 SHALL 提供确定性脚本模拟器：按剧本逐句回复，剧本用尽时结束对话。

#### Scenario: 按剧本回复

- **WHEN** 剧本中还有未使用的消息
- **THEN** 模拟器返回下一条消息作为用户回合

#### Scenario: 剧本用尽

- **WHEN** 剧本中的消息已经全部使用
- **THEN** 模拟器返回结束信号，对话正常终止

#### Scenario: 模拟器可替换

- **WHEN** 用户提供自定义模拟器
- **THEN** 平台用它产生用户消息，脚本模拟器不再参与

### Requirement: Dialogue loop and turn budget

系统 SHALL 驱动「用户开口、agent 回复」的循环，并在达到回合预算时停止，且 SHALL 区分自然收敛与预算耗尽。

#### Scenario: 自然收敛

- **WHEN** 模拟器在预算用尽前结束对话
- **THEN** 该用例的回合预算判据通过

#### Scenario: 预算耗尽

- **WHEN** 循环达到回合预算而模拟器仍未结束对话
- **THEN** 该用例的回合预算判据失败，并说明预算被耗尽

### Requirement: Dialogue assertions

系统 SHALL 支持两类对话级断言：先问后做与收尾。

#### Scenario: 先问后做

- **WHEN** agent 在某个回合首次调用了指定工具
- **THEN** 该断言检查在此之前是否有一个 agent 回合包含指定标记

#### Scenario: 未调用目标工具

- **WHEN** agent 在整个对话中从未调用指定工具
- **THEN** 该断言失败并说明工具未被调用

#### Scenario: 收尾

- **WHEN** 对话结束
- **THEN** 收尾断言检查最后一个 agent 回合的内容是否包含指定标记

### Requirement: Results enter the standard run record

对话结果 SHALL 写入既有运行记录与轨迹，使轨迹类断言与门禁无需改动即可消费。

#### Scenario: 产出运行记录与轨迹

- **WHEN** 一次对话运行结束
- **THEN** 运行记录中包含每条用例的判定，以及该次对话的工具调用轨迹
