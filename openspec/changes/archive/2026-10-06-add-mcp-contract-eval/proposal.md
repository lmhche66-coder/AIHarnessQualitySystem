# Proposal

## Why

L1 目前只覆盖自建 `ToolRegistry` 的 Python 工具，对通过 JSON-RPC 暴露的 MCP server 完全无覆盖：既没有握手与能力协商验证，也没有「声明的 inputSchema 与实际拒绝行为是否一致」的检查。同时「从多个工具里选对哪一个、并行调用是否完整」这类选择能力没有任何打分，只能看最终答案，选错工具的事故挡不住。

## What Changes

- 新增最小 MCP 客户端：以子进程 + stdio 启动 MCP server，执行 `initialize` / `initialized` 握手、`tools/list`、`tools/call`，纯 JSON-RPC 2.0，不引入官方 SDK 依赖。
- 新增 MCP 契约断言：握手与会话、能力协商、工具清单与 schema 声明、参数校验、schema 与行为一致性。
- 新增函数选择打分：把 agent 产出的调用与期望调用做结构比对，覆盖选对函数、参数正确、无多余调用、并行调用完整性与「不该调用时不调用」。
- 新增 CLI 子命令 `mcp run` 与 `selection run`。
- 新增示例 MCP server（契约正确与 schema 行为不一致两个变体）与示例用例。
- **BREAKING**：无。既有用例类型、协议与接入方不受影响。

## Capabilities

### New Capabilities

- `mcp-contract-eval`: 验证 MCP server 的会话握手、能力协商、工具清单、schema 声明、参数校验与 schema 行为一致性。
- `tool-selection-scoring`: 对 agent 的工具选择行为做结构化打分，覆盖多函数选择、参数正确性、并行调用完整性与无关请求下的不调用。

### Modified Capabilities

无。

## Impact

- 新增 `agenteval.mcp` 与 `agenteval.selection` 两个模块，以及对应的 CLI 子命令。
- 依赖无新增：MCP 客户端用标准库 `subprocess` + `json` 实现，不依赖网络与官方 SDK。
- 既有各层、用例联合类型、运行记录与控制台展示保持兼容；两类新结果复用既有 `Run` / `Verdict` / `CheckOutcome`。
