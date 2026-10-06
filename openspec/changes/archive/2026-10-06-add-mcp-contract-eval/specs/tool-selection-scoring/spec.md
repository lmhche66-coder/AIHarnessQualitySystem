# Spec Delta

## Purpose

对 agent 的工具选择行为做结构化打分，覆盖多函数选择、参数正确性、并行调用完整性与无关请求下的正确「不调用」，让「选错工具」在 L1 就能被确定性拦截。

## ADDED Requirements

### Requirement: Declarative selection cases

系统 SHALL 支持以声明式方式定义选择用例，用例包含请求、可用工具、期望调用与实际调用，其中每个调用包含函数名与参数。

#### Scenario: 解析选择用例

- **WHEN** 用户提供包含请求、可用工具与调用列表的用例文件
- **THEN** 系统解析出每条用例，并保留期望调用与实际调用的名称与参数

### Requirement: Function name matching

系统 SHALL 以顺序无关的方式比对实际调用与期望调用的函数名集合，并区分错选、漏选与多选。

#### Scenario: 函数名一致

- **WHEN** 实际调用的函数名集合与期望完全一致
- **THEN** 函数选择判据通过

#### Scenario: 选错函数

- **WHEN** 实际调用了一个不在期望集合中的函数
- **THEN** 函数选择判据失败，并指出被错选的函数名

#### Scenario: 漏选函数

- **WHEN** 期望集合中的某个函数没有被调用
- **THEN** 函数选择判据失败，并指出被漏选的函数名

### Requirement: Argument matching

系统 SHALL 按函数逐项比对参数，要求期望参数都出现且取值一致，并且不存在多余参数。

#### Scenario: 参数一致

- **WHEN** 某函数的实际参数与期望参数在键和值上都一致
- **THEN** 该函数的参数判据通过

#### Scenario: 参数值不符

- **WHEN** 某函数某个参数的实际取值与期望不同
- **THEN** 参数判据失败，并指出参数名、期望值与实际值

#### Scenario: 参数缺失或多余

- **WHEN** 某函数缺少期望参数，或多出未声明的参数
- **THEN** 参数判据失败，并指出缺失或多出的参数名

### Requirement: Parallel call completeness

当期望调用包含多个函数时，系统 SHALL 要求实际调用集合完整覆盖全部期望函数，缺一即失败。

#### Scenario: 并行调用完整

- **WHEN** 一次请求的期望包含多个函数，且实际调用覆盖全部期望函数
- **THEN** 并行调用判据通过

#### Scenario: 并行调用缺失

- **WHEN** 实际调用只覆盖了期望函数的一部分
- **THEN** 并行调用判据失败，并指出缺失的函数

### Requirement: Irrelevance handling

当用例期望不调用任何工具时，系统 SHALL 要求实际调用为空。

#### Scenario: 无关请求未调用工具

- **WHEN** 期望调用为空且实际调用也为空
- **THEN** 该判据通过

#### Scenario: 无关请求却调用了工具

- **WHEN** 期望调用为空但实际调用了工具
- **THEN** 该判据失败，并指出不应发生的调用

### Requirement: Results enter the standard run record

选择打分结论 SHALL 写入既有运行记录，使基线、门禁与结论层无需改动即可消费。

#### Scenario: 产出运行记录

- **WHEN** 一次选择打分运行结束
- **THEN** 运行记录中包含每条用例的判定与逐项判据
