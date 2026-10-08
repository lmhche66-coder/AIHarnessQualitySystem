# Proposal

## Why

平台已有「裁判校准」：它量化一个成对偏好裁判与人工标注的一致率、位置翻转与长度偏置，决定裁判能否进门禁。但文章第 5 节真正要解决的是另一件事——把「某条用例的某个指标到底达不达标」变成可解析、可计算、可归口的**结构化裁判任务**。校准只回答「这个裁判靠不靠谱」，不回答「这条用例在任务完成率上过没过」。

文章给出的机制是 Judge Task：一个 Prompt 只评一个指标（单一职责）、先输出 reasoning 再给结论（先推理后判断）、带通过/不通过的对照示例（负例引导）、以严格 JSON 输出（结构化输出）。平台此前没有这层机制，逐指标的质量判定只能靠人手工写断言或轻信一个总分。

## What Changes

- 新增六类 Judge Task：二元判定（`binary`）、单标签分类（`classification`）、多标签匹配（`multi_label`）、抽取比对（`extraction`）、量表评分（`score`）、成对偏好（`preference`）。
- 每个任务只评一个指标；裁判返回严格 JSON，平台按类型校验字段并折算为 `pass`/`fail`，无法解析或字段不合法时判为 `error` 而不是静默通过。
- 新增指标到任务类型的默认归口表，与既有指标登记表对齐，可用 `build_tasks` 按指标集装配任务。
- 上游路由错误时，依赖检索证据的指标按既有口径标记为跳过：既不计入分子也不计入分母。
- 判定复用既有 `CheckOutcome` 的 `metric` 与 `skipped` 字段，因此记分卡、门禁、控制台无需改动即可消费逐指标结果。
- 新增 `judge task` 命令：从用例文件与外部裁判跑一遍 Judge Task，产出可被 `report`、`gate` 消费的运行记录。
- 新增示例裁判与示例用例，把「上报输出 → 结构化裁判 → 逐指标判定 → 记分卡」闭环跑通。
- **BREAKING**：无。新增模块与子命令，不改既有裁判校准口径与运行记录格式。

## Capabilities

### New Capabilities

- `judge-tasks`: 六类结构化裁判任务、结构化输出校验、指标归口装配与逐指标判定。

### Modified Capabilities

- 无。

## Impact

- 新增 `agenteval.judge_tasks` 模块；`cli` 新增 `judge task` 子命令与少量装配代码。
- 新增示例裁判 `examples/demo_judge_tasks.py` 与用例 `examples/judge_task_cases.json`。
- 依赖无新增；平台不调用任何模型，裁判仍由外部提供，平台只负责装配任务、校验结构化输出与折算判定。
- 不做的事：不内置 Prompt 模板的模型调用；不替裁判产生 reasoning；不改裁判校准与门禁口径。
