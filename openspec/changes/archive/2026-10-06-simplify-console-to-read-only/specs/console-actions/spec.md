# Spec Delta

## ADDED Requirements

### Requirement: Read-only interface surface

控制台界面 SHALL 只读：它 SHALL NOT 提供任何产生副作用的入口，包括触发运行、判定门禁、校准裁判、写入标注、上传轨迹与写出候选用例。界面 SHALL 分为运行、结论、基线、资产四个分区，且每个分区 SHALL 只呈现既有产物。

#### Scenario: 界面不提供写入口

- **WHEN** 用户浏览控制台的任一分区
- **THEN** 界面上不存在产生副作用的控件，只有选择、刷新与展开这类读取操作

#### Scenario: 四个分区

- **WHEN** 控制台加载完成
- **THEN** 导航提供运行、结论、基线、资产四个分区，各自对应一类既有产物

#### Scenario: 资产分区集中呈现只读清单

- **WHEN** 用户进入资产分区
- **THEN** 界面展示金标集、证据轨迹与回流数据集三份只读清单

#### Scenario: 回流数据集可见

- **WHEN** 存在回流数据集
- **THEN** 界面展示其中的回归用例数量与绑定轨迹

### Requirement: Read-only action API

控制台的写接口 SHALL 保持不变并继续受回环地址限制；本次变更 SHALL NOT 改动任何后端写接口的行为。

#### Scenario: 写接口仍然存在

- **WHEN** 调用既有的控制台写接口
- **THEN** 行为与本次变更之前一致，仍受绑定地址与授权边界约束

### Requirement: Read-only baseline listing

系统 SHALL 提供只读的基线视图，列出已有基线及其名称、来源运行、用例数与状态分布；界面 SHALL NOT 提供捕获基线的入口。

#### Scenario: 列出基线

- **WHEN** 存在已保存的基线
- **THEN** 界面展示其名称、来源运行、用例数与状态分布

#### Scenario: 没有基线

- **WHEN** 基线目录为空
- **THEN** 界面显示空状态并说明捕获基线的命令，而不是报错

## REMOVED Requirements

### Requirement: Baseline management

**Reason**: 捕获基线需要人判断「哪次运行算基线」，属于决策而非浏览；把决策放在只读控制台里会让读与写的边界模糊。
**Migration**: 改用 `agenteval baseline --run <id> --name <name>` 捕获，结果在基线分区查看。

### Requirement: Run triggering

**Reason**: 触发评测属于副作用，实际执行者是 CLI 与 CI；界面承担它会带来误操作面。
**Migration**: 改用 `agenteval run` 与 `agenteval task run`。

### Requirement: Gate triggering

**Reason**: 门禁判定是发布流程的一部分，应在流水线里执行并留下可审计的退出码。
**Migration**: 改用 `agenteval gate`。

### Requirement: Judge calibration triggering

**Reason**: 裁判校准依赖金标集与裁判工厂，两者都在代码侧，界面传参只会增加一层转述。
**Migration**: 改用 `agenteval judge calibrate`。

### Requirement: Gold set authoring and labeling

**Reason**: 标注需要权限与复核，属于独立工具链；在只读控制台里保留半套标注只会让人误以为数据已经确认。
**Migration**: 在标注工具或脚本中完成，结论仍可在资产分区查看。

### Requirement: Trace upload

**Reason**: 导入外部审计是数据摄取，入口应在 CLI 与流水线，而不是浏览器。
**Migration**: 改用 `agenteval trace import`。

### Requirement: Failure reflow review

**Reason**: 回流会写数据集，属于质量流程的决策点，已由 `agenteval reflow run` 与人工确认承担。
**Migration**: 改用 `agenteval reflow run` 生成数据集，在资产分区查看结果。
