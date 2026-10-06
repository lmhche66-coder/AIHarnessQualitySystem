# console-actions Specification

## Purpose
让控制台从只读展示面变成可操作的工作台：在受控边界内允许从界面产生副作用，首批操作覆盖结论浏览与基线管理，使「哪次运行算基线」这类需要人决定的事情可以在界面上完成。

## Requirements

### Requirement: Conclusion browsing

系统 SHALL 提供结论视图，列出结论记录并按类型展示摘要，分别覆盖门禁、失败归因与裁判校准。

#### Scenario: 列出结论

- **WHEN** 结论目录中存在记录
- **THEN** 界面按时间倒序列出，每条显示类型、结论与创建时间

#### Scenario: 展示门禁结论

- **WHEN** 选中一条门禁结论
- **THEN** 界面展示其来源运行、通过率、失败与错误数、回归与缺失的数量

#### Scenario: 展示归因结论

- **WHEN** 选中一条失败归因结论
- **THEN** 界面展示失败总数、可回流数、已验证数与候选用例数量

#### Scenario: 展示裁判校准结论

- **WHEN** 选中一条裁判校准结论
- **THEN** 界面展示样本数、一致率、置信区间、位置翻转率与长度偏好比例

#### Scenario: 没有结论

- **WHEN** 结论目录为空
- **THEN** 界面显示空状态并说明产生结论的命令，而不是报错

### Requirement: Action boundary

系统的写接口 SHALL 仅在控制台绑定回环地址时可用；绑定到其它地址时，写接口 MUST 被拒绝且读接口不受影响。

#### Scenario: 回环地址下允许操作

- **WHEN** 控制台绑定回环地址且用户发起写操作
- **THEN** 该操作按语义执行

#### Scenario: 非回环地址下拒绝操作

- **WHEN** 控制台绑定非回环地址且用户发起写操作
- **THEN** 系统拒绝该操作并说明原因，读接口仍然可用

#### Scenario: 非法请求体

- **WHEN** 写请求的请求体缺失、不是合法 JSON 或不是对象
- **THEN** 系统以请求错误拒绝，而不是抛出未处理异常

### Requirement: Action auditability

写操作 SHALL NOT 覆盖或删除既有运行记录与结论记录；基线捕获只新增或更新基线文件。

#### Scenario: 捕获基线不影响运行记录

- **WHEN** 用户从界面捕获基线
- **THEN** 运行记录与结论记录保持不变，只有基线文件被写入

### Requirement: Operation error contract

每个操作 SHALL 在失败时返回可读原因并指明对象；缺少必需输入、目标不存在与内容不合法 MUST 被区分为不同的请求错误。

#### Scenario: 缺少必需输入

- **WHEN** 请求缺少必需字段
- **THEN** 系统返回请求错误并指明缺失的字段名

#### Scenario: 目标不存在

- **WHEN** 请求指向不存在的运行、金标集或轨迹
- **THEN** 系统返回未找到错误并指明该对象

#### Scenario: 内容不合法

- **WHEN** 请求内容无法解析
- **THEN** 系统返回请求错误并给出可读原因，而不是抛出未处理异常

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
