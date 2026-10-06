# Proposal

## Why

平台所有被测对象都必须以 `module:factory` 形式在平台进程内加载，于是任意语言、任意进程、部署在别处的 agent 接不进来；公司内部多个 agent 各写一套胶水，评测结论也不绑定 agent 版本。中小公司要的不是分布式调度，而是「一份协议、一个注册表，把内部 N 个 agent 都接上，且结论可追溯到版本」。

## What Changes

- 新增 Agent Bridge：一份标准 JSON wire 协议，规定平台如何向 agent 发起一次评测调用、agent 如何返回文本输出、工具调用与用量。
- 新增两种传输：HTTP endpoint（远端、容器、其他语言）与子进程 stdio（本地命令行 agent）。
- 新增能力适配：同一 agent 通过同一份协议接入 task、dialogue、redteam、selection 四类评测。
- 新增工具执行模式：平台执行（agent 返回工具调用，平台调用工具并把结果回传，驱动多步循环）与 agent 执行（agent 自行执行并回报调用记录）。
- 新增 agent 注册表：声明多个 agent 应用的 id、版本、传输、能力矩阵与超时，CLI 以 `@app-id` 引用。
- CLI 统一引用形式：`module:factory`（零成本快路径）、`@app-id`（注册表）、`http(s)://...`（直连）、`cmd:...`（直连子进程）。
- 新增两个不同形态的示例 agent 与注册表，验证「一次接入、四条链路复用」。
- **BREAKING**：无。既有 `module:factory` 接入方式与全部用例格式不变。

## Capabilities

### New Capabilities

- `agent-bridge`: 标准 wire 协议、HTTP 与子进程传输、四类能力适配、工具执行模式、用量上报与故障隔离。
- `agent-registry`: 多 agent 的声明式注册、按 id 解析、版本绑定、能力声明与可用性检查。

### Modified Capabilities

无。

## Impact

- 新增 `agenteval.bridge` 模块；CLI 的 `run`/`task`/`dialogue`/`redteam`/`selection` 扩展 agent 引用解析。
- 运行记录 `metadata` 增加 app id 与版本，便于结论追溯到具体部署版本。
- 依赖无新增：HTTP 用标准库 `urllib`，子进程用 `subprocess`。
