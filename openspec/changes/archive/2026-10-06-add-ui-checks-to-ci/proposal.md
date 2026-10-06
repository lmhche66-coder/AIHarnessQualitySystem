# Proposal

## Why

上一轮加了 UI 检查与布局检查工具，但没进 CI：Playwright 与 Chromium 要下约 180MB，每步都跑会让流水线明显变慢。代价是那类问题在 CI 里抓不到——我在本地靠布局检查抓到的两个界面缺陷（单面板视图落进侧栏列、移动端横向溢出），在流水线上完全不可见。

## What Changes

- CI 增加 Playwright 浏览器缓存，首次下载之后不再重复下载。
- CI 安装可选 UI 依赖并在真实浏览器中运行控制台布局检查。
- CI 用 UI 流程对控制台执行一次端到端检查。
- 上述检查放在产生数据的步骤之后，确保界面里有真实运行记录可渲染。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

无。本变更是流水线配置与验证手段，不改变任何对外行为。

## Impact

- 仅改动 `.github/workflows/ci.yml` 与 `pyproject.toml` 的安装步骤，不涉及产品代码。
- 新增 CI 耗时主要在首次下载浏览器；缓存命中后只增加数秒。
- 依赖增加仅限 CI 环境，不影响使用方的默认安装。
