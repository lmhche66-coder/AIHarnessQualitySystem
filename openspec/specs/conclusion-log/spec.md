# conclusion-log Specification

## Purpose
把评测结论从终端输出变成可回看、可检索的结构化资产，使门禁、归因与裁判校准的判定不再随着终端滚动而消失。

## Requirements

### Requirement: Conclusion persistence

系统 SHALL 把评测结论以结构化记录持久化到运行根目录下的结论目录，每条记录 MUST 包含类型、创建时间、结论摘要与完整报告。

#### Scenario: 写入结论

- **WHEN** 一次产生结论的命令执行完成
- **THEN** 系统写入一条结论记录，包含类型、创建时间、结论摘要与完整报告

#### Scenario: 目录不存在

- **WHEN** 结论目录尚不存在
- **THEN** 系统自动创建目录并完成写入

### Requirement: Gate conclusions are recorded

系统 SHALL 在门禁判定完成后写入结论，内容 MUST 包含通过与否、通过率、回归清单与缺失清单。

#### Scenario: 门禁结论

- **WHEN** 门禁对一个运行执行判定
- **THEN** 结论记录包含该运行标识、通过与否、通过率，以及回归与缺失的用例清单

### Requirement: Triage conclusions are recorded

系统 SHALL 在失败归因完成后写入结论，内容 MUST 包含失败总数、可回流数与已验证数。

#### Scenario: 归因结论

- **WHEN** 对一次运行执行失败归因
- **THEN** 结论记录包含失败总数、可回流数与已验证数，并保留逐条归因明细

### Requirement: Judge calibration conclusions are recorded

系统 SHALL 在裁判校准完成后写入结论，内容 MUST 包含一致率、置信区间、位置翻转率、长度偏好比例与是否可用于门禁。

#### Scenario: 校准结论

- **WHEN** 对一份金标集执行裁判校准
- **THEN** 结论记录包含一致率及其置信区间、两项偏置指标，以及是否可用于门禁

### Requirement: Listing and reading conclusions

系统 SHALL 支持按创建时间倒序列举结论，以及按标识读取单条结论。

#### Scenario: 列举结论

- **WHEN** 结论目录中存在多条记录
- **THEN** 系统按创建时间从新到旧列举它们

#### Scenario: 读取不存在的结论

- **WHEN** 用户按一个不存在的标识读取结论
- **THEN** 系统报错并指明该标识，而不是返回空记录

### Requirement: Conclusions reference rather than duplicate

结论记录 SHALL 引用来源运行标识，且 SHALL NOT 复制判定明细，避免与运行记录重复。

#### Scenario: 引用来源运行

- **WHEN** 结论来自某次运行
- **THEN** 记录中保存该运行标识，判定明细仍以运行记录为准
