# Proposal

## Why

端到端任务的隔离目前只是「每个任务重建一份 `ToolRegistry`」，那是进程内状态；任务依赖的外部服务（数据库、向量库、对象存储）始终是同一份长期运行的实例，上一个用例写进去的数据会影响下一个，评测结论随环境漂移。这就是「同一个任务两次跑结果不同」的主要来源。仓库里已经有真实的 compose 基础设施可管，缺的是平台侧的沙箱驱动。

## What Changes

- 新增沙箱定义：声明 compose 文件、项目名、服务范围、重置策略、健康要求与快照涉及的卷。
- 新增沙箱驱动：对 compose 项目执行 `up` / `down` / `health` / `reset` / `snapshot`，并把命令结果转成结构化错误。
- 新增三种重置策略：`recreate`（销毁卷后重建，回到全新状态）、`snapshot`（从已保存的卷快照恢复，回到预置状态）、`none`（只检查健康，适合只读评测）。
- 新增与评测的生命周期集成：整轮开始前做健康门禁，每个用例前把沙箱重置到已知状态，整轮结束后按配置保留或销毁。
- 新增沙箱版本绑定：把 compose 文件内容哈希、项目名与镜像标签写入运行记录，使结论可追溯到具体环境。
- 新增 CLI `sandbox` 子命令：`up` / `down` / `health` / `reset` / `snapshot`，可独立于评测使用。
- **BREAKING**：无。既有任务运行不传沙箱时行为完全不变。

## Capabilities

### New Capabilities

- `sandbox-driver`: compose 级沙箱的定义、启停、健康检查、快照与重置策略。
- `sandbox-lifecycle`: 沙箱与评测运行的生命周期集成、前置健康门禁与版本绑定。

### Modified Capabilities

无。

## Impact

- 新增 `agenteval.sandbox` 模块与 `sandbox` CLI 子命令。
- `TaskRunner` 增加可选沙箱生命周期钩子；不传时行为不变，既有测试与接入方不受影响。
- 依赖无新增：只调用本机 `docker` / `docker compose` 命令，不引入 Python Docker SDK。
