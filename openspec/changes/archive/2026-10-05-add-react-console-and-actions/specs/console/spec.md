# Spec Delta

## MODIFIED Requirements

### Requirement: Local read-only console

系统 SHALL 提供一个本地展示服务，读取既有的运行产物；读接口 SHALL NOT 修改任何产物，默认 SHALL 绑定回环地址；写接口 SHALL 仅在绑定回环地址时可用。

#### Scenario: 启动控制台

- **WHEN** 用户以默认参数启动控制台
- **THEN** 服务绑定回环地址并展示既有运行

#### Scenario: 只读

- **WHEN** 控制台展示运行数据
- **THEN** 读接口只读取既有文件，不写入、不删除、不修改任何产物

#### Scenario: 产物目录不存在

- **WHEN** 运行目录尚不存在
- **THEN** 控制台正常启动并显示空列表，而不是启动失败

### Requirement: Offline and build-free

控制台 SHALL 在不访问外部网络的前提下可用，页面 SHALL NOT 引用外部脚本或样式；前端构建在开发期执行，构建产物随包分发，因此使用方无需执行构建步骤。

#### Scenario: 离线打开

- **WHEN** 用户在无外网的环境打开控制台
- **THEN** 页面样式与交互正常，不依赖任何外部资源

#### Scenario: 未构建产物

- **WHEN** 构建产物缺失
- **THEN** 服务返回明确的错误提示，说明需要执行前端构建，而不是返回空白页
