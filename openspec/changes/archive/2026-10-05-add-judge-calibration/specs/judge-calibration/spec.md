# Spec Delta

## Purpose

把裁判当成需要被验证的测量仪器：用人工标注的金标集量化它与人的一致程度、给出置信区间、检出位置与长度偏置，并据此决定它是否有资格参与质量门禁。

## ADDED Requirements

### Requirement: Judge protocol

系统 SHALL 通过可替换的判定器协议接入裁判，判定器接收提示词与两个候选并返回偏好，平台 SHALL NOT 自行调用任何模型。

#### Scenario: 接入外部判定器

- **WHEN** 用户提供一个符合协议的判定器
- **THEN** 系统用它完成全部比较，不依赖平台内置模型

#### Scenario: 判定器返回非法取值

- **WHEN** 判定器返回既不是第一个候选、也不是第二个候选、也不是平局的结果
- **THEN** 系统报错并指出该样本，而不是把它当作平局

### Requirement: Pairwise gold set

系统 SHALL 支持带人工标注的成对金标集，每条样本 MUST 包含唯一标识、提示词、两个候选与人工偏好。

#### Scenario: 载入金标集

- **WHEN** 用户提供一份金标集文件
- **THEN** 系统解析为结构化样本，并在缺少标识或人工偏好非法时给出指明位置的错误

#### Scenario: 空金标集

- **WHEN** 金标集不含任何样本
- **THEN** 系统拒绝校准并说明无样本可用，而不是给出满分

### Requirement: Agreement and confidence interval

系统 SHALL 计算裁判相对人工标注的一致率，并给出该一致率的置信区间。

#### Scenario: 计算一致率

- **WHEN** 裁判对全部金标样本完成判定
- **THEN** 系统报告一致率，并逐条列出与人工标注不一致的样本

#### Scenario: 置信区间随样本量变化

- **WHEN** 样本量很小
- **THEN** 置信区间明显变宽，报告的结论必须附带该区间，避免把小样本结果当作精确值

### Requirement: Position bias detection

系统 SHALL 通过交换候选顺序重跑判定来检测位置偏置，并报告判定发生翻转的比例。

#### Scenario: 存在位置偏置

- **WHEN** 判定器在交换顺序后系统性地改变偏好
- **THEN** 系统报告高翻转率，并在报告中指出该裁判存在位置偏置

#### Scenario: 无位置偏置

- **WHEN** 交换顺序后判定保持一致
- **THEN** 翻转率为零，报告不指出位置偏置

### Requirement: Length bias detection

系统 SHALL 报告在非平局判定中较长候选被选中的比例，用于发现长度偏置。

#### Scenario: 偏向更长的回答

- **WHEN** 较长候选在绝大多数非平局判定中获胜
- **THEN** 系统在报告中指出该裁判存在长度偏置

### Requirement: Calibration verdict

系统 SHALL 依据阈值判定裁判是否可用于质量门禁，并明确区分「可用于门禁」与「仅供参考」。

#### Scenario: 达到阈值

- **WHEN** 一致率不低于下限，且位置翻转率与长度偏好比例不超过上限
- **THEN** 裁判被判定为可用于门禁

#### Scenario: 未达阈值

- **WHEN** 任一阈值不满足
- **THEN** 裁判被判定为仅供参考，并逐条列出未满足的条件

### Requirement: Calibration report

系统 SHALL 提供机器可读与人类可读两种形式的校准报告，内容 MUST 包含一致率、置信区间、偏置指标、阈值与结论。

#### Scenario: 机器可读报告

- **WHEN** 用户以 JSON 形式请求校准结果
- **THEN** 输出包含一致率、置信区间上下界、位置翻转率、长度偏好比例、阈值与结论

#### Scenario: 人类可读报告

- **WHEN** 用户以文本形式查看校准结果
- **THEN** 输出逐项列出指标、阈值与是否达标
