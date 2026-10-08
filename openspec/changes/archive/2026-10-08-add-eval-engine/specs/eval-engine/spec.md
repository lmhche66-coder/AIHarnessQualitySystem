# Spec Delta

## ADDED Requirements

### Requirement: Dataset registry

系统 SHALL 支持把「评测数据集 → 用例文件」固化为一份注册表，并 SHALL 以注册表文件所在目录解析其中的相对路径。

#### Scenario: 载入数据集注册表

- **WHEN** 用户提供一份数据集注册表
- **THEN** 系统解析每个数据集对应的用例文件列表，相对路径按注册表文件所在目录解析

#### Scenario: 空或非法注册表

- **WHEN** 注册表为空、结构非法或不是数据集到文件的映射
- **THEN** 系统以可读错误结束，而不是静默产出空计划

### Requirement: Agent-driven execution

评测执行引擎 SHALL 按文章 §6.1 的链路驱动评测用例：把用例交给被测 Agent，采集轨迹，再交给裁判。引擎 MUST 只执行 agent 用例（单轮任务与多轮对话），MUST NOT 执行「平台直接调用工具、不经 Agent」的契约与过程用例；遇到这类用例 MUST 给出可读错误并指向 `run`。

#### Scenario: 交给 Agent 执行

- **WHEN** 一次评测装配出任务或多轮对话用例
- **THEN** 引擎把用例交给被测 Agent 执行，并采集其轨迹

#### Scenario: 拒收非 agent 用例

- **WHEN** 装配结果里包含契约或过程用例
- **THEN** 引擎以可读错误结束并提示改用 `run`，而不是把工具契约当作 Agent 评测

#### Scenario: 指标归口

- **WHEN** agent 用例的检查声明了指标标识
- **THEN** 该标识写到判定上，记分卡按文章那套指标（如任务完成率、多轮对话完成率）聚合，而不是回退到断言名

#### Scenario: 缺少 Agent

- **WHEN** 装配出的用例是 agent 用例，但没有提供 Agent
- **THEN** 引擎以可读错误结束，说明需要提供 Agent

### Requirement: Evaluation plan

系统 SHALL 按评测范围画像装配数据集与用例文件，给出该范围的数据集、指标与主指标，并 SHALL 把范围内没有注册文件的数据集记为 `missing`。

#### Scenario: 按范围装配数据集

- **WHEN** 用户给定一个评测范围
- **THEN** 系统输出该范围装配的数据集、每个数据集对应的用例文件，以及缺文件的数据集清单

#### Scenario: 范围没有可执行用例

- **WHEN** 该范围内的数据集都没有注册可用文件
- **THEN** 系统以可读错误结束并返回非零退出码，而不是输出空报告

#### Scenario: 模块范围主指标

- **WHEN** 评测范围是感知、规划、记忆或工具模块
- **THEN** 计划里的主指标取该模块的核心职责指标

### Requirement: Full evaluation scope

系统 SHALL 提供「全量评测」范围，装配全部八类评测数据集；加上该范围后，评测范围总数 MUST 为文章所述的十五种（八类数据集、端到端、全量、四个模块与核心模块）。

#### Scenario: 全量范围装配全部数据集

- **WHEN** 用户选择全量评测范围
- **THEN** 系统装配全部八类数据集，主指标取任务完成率

### Requirement: Execution controls

系统 SHALL 支持并发执行用例、单条用例超时与失败重试。并发 MUST 至少为 1，重试次数 MUST 非负，超时 MUST 为正；使用 cassette 时并发 MUST 回退到 1。

#### Scenario: 并发执行

- **WHEN** 用户声明的并发大于 1 且未使用 cassette
- **THEN** 系统并发执行用例，并把声明的并发写入运行元数据

#### Scenario: 单条超时

- **WHEN** 某条用例超过声明的单条超时仍未完成
- **THEN** 该用例判为 `error` 并说明超时，其余用例照常执行

#### Scenario: cassette 与并发

- **WHEN** 一次协同使用 cassette 声明了大于 1 的并发
- **THEN** 系统把并发回退到 1，保证录制与重放确定

#### Scenario: 非法执行参数

- **WHEN** 并发小于 1、重试为负、超时非正或重试间隔为负
- **THEN** 系统以可读错误结束并返回非零退出码

### Requirement: Retry semantics

系统 SHALL 只对「执行异常」重试，MUST NOT 对「判定不通过」重试。执行异常（超时、异常、错误）最多重试声明次数，每次间隔声明秒数；判定不通过是能力问题，重试不会改变结果。运行元数据 SHALL 记录逐用例的实际尝试次数。

#### Scenario: 执行异常重试

- **WHEN** 某条用例判定为 `error`，且声明了重试次数
- **THEN** 系统按声明间隔重试至多次数上限，并记录实际尝试次数

#### Scenario: 判定不通过不重试

- **WHEN** 某条用例判定为 `fail`
- **THEN** 系统不重试，实际尝试次数为 1

#### Scenario: 未声明重试

- **WHEN** 用户没有声明重试次数
- **THEN** 每条用例只执行一次

### Requirement: Runtime metric collection

系统 SHALL 采集并落数文章 §6.3 的运行时指标：模块级延迟（意图识别、规划决策、记忆注入、检索、工具调用）、端到端延迟、成本（工具调用次数、模型调用次数、输入/输出 Token）。没有数据的指标 MUST NOT 臆造零值。

#### Scenario: 模块级延迟落数

- **WHEN** 轨迹中包含带耗时的模块信号
- **THEN** 记分卡按指标给出该延迟的均值与 p50/p95

#### Scenario: 模型调用次数

- **WHEN** 被测 agent 上报了模型调用次数
- **THEN** 记分卡的成本栏展示模型调用合计；未上报时不计入

#### Scenario: 缺数据的延迟指标

- **WHEN** 某模块没有任何带耗时的信号
- **THEN** 该延迟指标不出现在记分卡中，而不是显示为零

### Requirement: Trace-aware judging

系统 SHALL 支持把一次执行的轨迹折算成裁判证据，并在裁判任务中一并提交给裁判：命中 Skill、路由方向、调用过的工具、检索到的片段、各段耗时。MUST NOT 只依赖最终回复文本推断中间过程。

#### Scenario: 组装轨迹证据

- **WHEN** 用户把一个已存运行关联到裁判任务
- **THEN** 系统从该运行的轨迹抽取模块信号与工具调用，作为裁判证据

#### Scenario: 路由误触发跳过下游

- **WHEN** 用例声明了期望路由，而轨迹里的实际路由与之一致性被打破
- **THEN** 系统据此判定上游路由误触发，并按既有口径跳过依赖检索证据的下游指标

### Requirement: Multi-turn evaluation handling

多轮对话评测 SHALL 处理文章 §6.4 的四件事：会话上下文共享、轮次间同步、成本指标逐轮累加、整体评判。

#### Scenario: 会话上下文共享

- **WHEN** 一条多轮对话用例执行多轮
- **THEN** 各轮共享同一个会话标识（用例声明 `session_id`，缺省取用例标识），Agent 每轮都能读到完整历史

#### Scenario: 轮次间同步

- **WHEN** 声明了大于零的轮次间等待
- **THEN** 平台在每一轮结束后等待会话持久化完成，再发起下一轮

#### Scenario: 成本逐轮累加

- **WHEN** Agent 在多轮中分别上报模型用量
- **THEN** 轨迹里每一轮各有一条用量记录，成本指标按整组对话累加

#### Scenario: 整体评判

- **WHEN** 对一条多轮对话做判定或裁判
- **THEN** 以整段对话为单位（各轮输出拼接）评判，而不是逐轮独立判定

### Requirement: Multi-turn synchronization

多轮对话评测 SHALL 支持在每一轮结束后等待会话持久化完成，再发起下一轮，且等待时长 MUST 可配置。

#### Scenario: 轮次间等待

- **WHEN** 声明了大于零的轮次间等待
- **THEN** 每一轮结束后系统等待该时长再发起下一轮

#### Scenario: 逐轮用量累计

- **WHEN** 被测 agent 在多轮中上报 Token 与模型调用
- **THEN** 系统按整组对话累计，而不是只统计最后一轮

### Requirement: Asynchronous jobs

系统 SHALL 支持异步提交评测：提交后立即返回任务标识符，评测在后台执行，客户端可轮询进度，并 SHALL 支持在运行中请求取消。

#### Scenario: 异步提交

- **WHEN** 用户提交一次评测
- **THEN** 系统立即返回任务标识符，评测在后台执行

#### Scenario: 轮询进度

- **WHEN** 用户按任务标识符查询
- **THEN** 系统返回该任务的运行状态与已产出结果

#### Scenario: 运行中取消

- **WHEN** 用户请求取消一条运行中的任务
- **THEN** 系统置位取消标志，后台执行在用例边界停止并把任务标记为已取消

#### Scenario: 取消已结束任务

- **WHEN** 用户取消一条已完成或已失败的任务
- **THEN** 系统保持其最终状态不变

### Requirement: Evaluation mode

系统 SHALL 支持两种评测模式：端到端真实（`e2e_real`）与端到端 Mock（`e2e_mock`）。声明的评测模式 MUST 写入运行元数据，且记分卡 MUST 优先采用该声明。

#### Scenario: 声明 Mock 模式

- **WHEN** 用户在重放 cassette 时声明 Mock 模式
- **THEN** 运行元数据记录 `e2e_mock`，记分卡据此标注评测模式

#### Scenario: 历史运行回落

- **WHEN** 运行元数据没有声明评测模式
- **THEN** 记分卡回落到既有口径：cassette 重放即 Mock，其余视为真实链路

### Requirement: Case input, expected output and mock data

评测用例 SHALL 支持声明用户输入、期望输出、逐指标期望与 Mock 数据，供 Agent 执行与裁判评分使用；缺失时 MUST 保持可选，既有用例无需改动。

#### Scenario: 用例携带输入与期望

- **WHEN** 用例声明了用户输入与期望输出
- **THEN** 引擎把它们交给 Agent 与裁判，而不是只依赖 description

#### Scenario: 逐指标期望

- **WHEN** 用例声明了逐指标期望
- **THEN** 裁判按指标取用对应期望值

### Requirement: Agent-owned tools by default

工具 SHALL 默认属于被测 Agent 自己的工具模块：平台 MUST NOT 要求提供工具环境，也 MUST NOT 派发工具描述或代为执行工具。Agent SHALL 把它已经执行过的调用回报给平台，平台据此记录轨迹供过程断言使用。平台 MAY 通过显式参数提供受控环境，用于需要平台代执行工具的评测。

#### Scenario: 不给工具也能评测

- **WHEN** 用户发起一次评测但没有提供任何工具环境
- **THEN** 平台以空环境执行，Agent 使用自己的工具完成任务

#### Scenario: Agent 回报已执行的调用

- **WHEN** Agent 回报它已经执行过的工具调用
- **THEN** 平台不重复执行，只把它们记入轨迹，供调用顺序与多余调用断言使用

#### Scenario: 多轮对话同样适用

- **WHEN** 多轮对话用例由自带工具的 Agent 执行
- **THEN** 平台不派发工具，按轮记录 Agent 回报的调用

#### Scenario: 显式提供受控环境

- **WHEN** 用户显式提供工具环境
- **THEN** 平台按既有口径派发并代执行工具，这条路径不受默认行为影响

### Requirement: Reported tool-call detail

Agent 自带工具时，回报的工具调用 SHALL 能携带「是否成功、错误类别、尝试次数、调用耗时」；平台 MUST 按回报记账并计入工具延迟指标。未回报的字段 MUST NOT 臆造。

#### Scenario: 回报成功与耗时

- **WHEN** Agent 回报一次成功的工具调用及其耗时
- **THEN** 平台把它记入轨迹并计入工具延迟，不重复执行

#### Scenario: 回报失败

- **WHEN** Agent 回报一次失败的工具调用与错误类别
- **THEN** 轨迹里该调用标记为失败并保留错误类别，而不是一律记成成功

#### Scenario: 未回报耗时

- **WHEN** Agent 没有回报调用耗时
- **THEN** 该调用不计入工具延迟，而不是补零

### Requirement: Injected execution context

平台 SHALL 在执行每条用例时注入会话标识与 Mock 数据：用例可声明 `session_id` 与 `mock`，未声明时会话标识取用例标识；注入的上下文 MUST 随 bridge 请求交给被测 Agent。

#### Scenario: 注入 sessionId

- **WHEN** 平台执行一条用例
- **THEN** 该用例的会话标识写入请求上下文，多轮对话各轮共享同一个标识

#### Scenario: 注入 Mock 数据

- **WHEN** 用例声明了 Mock 数据
- **THEN** 平台把 Mock 数据写入请求上下文，供被测 Agent 使用

#### Scenario: 用户输入

- **WHEN** 用例声明了用户输入
- **THEN** 平台把它作为给 Agent 的用户消息，而不是只发 description

### Requirement: Trace structure aligned with the article

轨迹 SHALL 覆盖文章 §6.3 的六组信息，字段命名稳定：感知（是否命中 Skill、Skill 名称、意图类型、耗时）、规划（决策耗时）、记忆（注入耗时、会话历史轮数）、工具（工具名、参数、是否成功、耗时）、RAG（检索耗时、结果数量、原始文本块）、模型消耗（模型调用次数、输入/输出 Token）。缺项的字段 MUST NOT 臆造。

#### Scenario: 感知与规划

- **WHEN** Agent 上报感知与规划信号
- **THEN** 轨迹记录命中与否、Skill 名称、意图类型以及各自的耗时

#### Scenario: 记忆与 RAG

- **WHEN** Agent 上报记忆与检索信号
- **THEN** 轨迹记录会话轮数、注入耗时，以及检索耗时、结果数量与原始文本块

#### Scenario: 工具调用

- **WHEN** 发生一次工具调用
- **THEN** 轨迹记录工具名、参数、是否成功与耗时

#### Scenario: 模型消耗

- **WHEN** Agent 上报模型用量
- **THEN** 轨迹里出现该用例的模型调用次数与输入/输出 Token，而不仅仅汇总到运行级

### Requirement: Trace collection without agent changes

平台 SHALL 在不修改被测 Agent 业务逻辑的前提下采集可观测的运行时数据；无法从平台侧观测的数据（如 Agent 内部各节点的耗时归属）MAY 由 Agent 上报，且缺失时 MUST NOT 臆造。

#### Scenario: 工具调用耗时

- **WHEN** 平台通过包装注册表执行工具调用
- **THEN** 平台自动测量并记录该调用耗时，计入工具延迟指标，Agent 无需上报

#### Scenario: 缺失的模块耗时

- **WHEN** Agent 未上报某模块的耗时
- **THEN** 该延迟指标不出现在报告中，而不是记为零

### Requirement: Eval command

系统 SHALL 提供从评测范围直达报告的命令：装配用例、执行、生成记分卡，并把记分卡写成一条结论记录，使控制台与门禁无需改动即可消费。

#### Scenario: 一次请求直达报告

- **WHEN** 用户提供评测范围与数据集注册表并发起一次评测
- **THEN** 系统自动装配用例、执行、输出记分卡，并写入一条 `report` 结论

#### Scenario: 自动装配多个数据集

- **WHEN** 评测范围覆盖多个数据集
- **THEN** 系统把它们装配进同一次运行，并把每个数据集的用例归属透传到判定

#### Scenario: 用例归属透传

- **WHEN** 从注册表装配的用例未声明数据集归属
- **THEN** 系统按注册表补写其数据集归属，供记分卡按数据集与场景聚合
