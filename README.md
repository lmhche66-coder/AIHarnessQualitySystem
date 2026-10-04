# agenteval

面向 agent 项目的评测平台。当前版本实现第 1 层能力：**工具调用契约的确定性验证**。

这一层不调用 LLM、不依赖真实外部服务，因此同一用例重复执行必须得到相同结论。它挡掉的是参数缺失、类型错误、超时、限流重试、非幂等副作用、部分失败未回滚这几类不需要模型就能复现的缺陷。

## 安装

```bash
python -m pip install -e ".[dev]"
```

## 跑通第一闭环

```bash
python -m agenteval run --cases examples/contract_cases.json --demo
python -m agenteval list
python -m agenteval show <run_id>
```

`run` 在全部用例通过时返回退出码 0，否则返回 1，可直接用于后续的 CI 门禁。

## 用例格式

用例可写成 JSON 或 YAML，顶层既可以是数组，也可以带 `cases` 键。每个用例由目标工具、调用输入和一项契约检查组成。

```json
{
  "id": "contract-timeout",
  "target": "slow_tool",
  "input": { "delay_s": 0.4 },
  "check": { "kind": "timeout", "timeout_s": 0.1 }
}
```

支持的 `check.kind`：

| kind | 必填字段 | 验证内容 |
| --- | --- | --- |
| `missing_required` | `drop` | 必填参数缺失时返回结构化校验错误 |
| `wrong_type` | `overrides` | 参数类型错误时返回结构化校验错误并指明字段 |
| `timeout` | `timeout_s` | 超时后受控结束且不阻塞调用方 |
| `rate_limit` | `max_attempts`、`expect_success` | 限流后重试成功，或在超过上限时结构化失败 |
| `idempotent_retry` | `idempotency_key`、`repeat` | 重复调用只产生一次副作用 |
| `partial_rollback` | `steps`、`fail_at` | 中途失败后回滚已完成步骤 |

## 接入自己的工具

工具实现 `invoke(**kwargs) -> ToolResult` 协议，注册进 `ToolRegistry`，再通过 `--registry` 指给 CLI：

```python
# mytools.py
from agenteval.tools import SchemaTool, ToolRegistry, ToolResult


class ChargeTool(SchemaTool):
    name = "charge_tool"
    input_schema = {
        "type": "object",
        "properties": {"amount": {"type": "integer", "minimum": 1}},
        "required": ["amount"],
        "additionalProperties": False,
    }

    def run(self, amount: int, **kwargs):
        return ToolResult(ok=True, value={"charged": amount, "side_effect_count": 1})


def build_registry() -> ToolRegistry:
    return ToolRegistry([ChargeTool()])
```

```bash
python -m agenteval run --cases my_cases.json --registry mytools.py:build_registry
```

继承 `SchemaTool` 即可免费获得入参 schema 校验。契约断言观察的是调用结果，因此不继承该基类的工具同样可以被测试，只是需要自己保证返回结构化错误。

两点契约要求需要注意：

- 幂等检查要求工具在返回值中报告 `side_effect_count`；无法观察副作用会被判为失败。
- 回滚检查要求工具在失败结果中报告 `rolled_back` 与 `remaining`。

## 运行结果

每次运行写入一个独立目录，默认位于 `.agenteval/runs/<run_id>/`：

- `summary.json`：运行元信息与通过/失败/错误计数
- `verdicts.jsonl`：逐条判定，便于后续把失败样本回流成新用例
- `traces.jsonl`：逐条执行轨迹

运行根目录可用 `--home` 或环境变量 `AGENTEVAL_HOME` 覆盖，便于 CI 与多环境隔离。

## 录制与回放

把工具交互录成 cassette，之后就能在没有外部服务、没有网络的环境里重复执行同一批用例。

```bash
# 录制：真实调用工具并写入 cassette
python -m agenteval --home .agenteval run \
  --cases examples/replayable_cases.json --demo \
  --cassette demo --cassette-mode record

# 回放：完全不触达真实工具
python -m agenteval --home .agenteval run \
  --cases examples/replayable_cases.json --demo \
  --cassette demo --cassette-mode replay
```

`--cassette-mode` 必须显式指定，避免误覆盖已录制的 cassette：

- `record`：真实调用并把结果写入 cassette；同名 cassette 已存在时会提示覆盖。
- `replay`：只读 cassette。请求未录制时返回 `cassette_miss` 并把该用例判定为 error，绝不回退到真实工具。
- `auto`：命中则回放，缺失则录制，适合本地开发。

同一个请求反复出现时（例如限流重试），响应按录制顺序依次回放。录制结束后若有交互从未被消费，`run` 会在运行记录的 `metadata.cassette.unused` 中报告；加 `--strict-cassette` 则直接判定失败，用来守「agent 少调用了一步」这类回归。

敏感字段默认按顶层字段名脱敏（`token`、`api_key`、`password`、`secret` 等），可用 `--redact FIELD` 追加。脱敏只作用于落盘内容，不影响指纹匹配。

### 一个明确的边界：耗时相关契约不回放

`timeout` 这类契约的结论取决于真实调用耗时，而 cassette 记录的是请求与响应。回放会让慢调用瞬间返回，若继续判定就会得到一个看似有效、实则无意义的结论。

因此非录制模式下运行 `timeout` 用例会被直接判定为 error 并说明原因，而不是给出通过或失败。这类用例请在无 cassette 的情况下运行，或用 `--cassette-mode record` 跑真实调用。`examples/replayable_cases.json` 就是去掉耗时契约后的可回放集合。

## 在 pytest 里运行

```bash
python -m pytest
```

也可以直接调用运行器，把契约用例纳入既有测试套件：

```python
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore
from mytools import build_registry


def test_tool_contracts(tmp_path):
    runner = ContractRunner(registry=build_registry(), store=RunStore(tmp_path / "runs"))
    run = runner.run(load_cases("my_cases.json"))
    assert run.summary.failed == 0
    assert run.summary.errored == 0
```

## 已知限制

- 超时用线程执行器实现，无法强制中断阻塞中的原生调用。被测工具应提供可中断实现。
- 耗时相关契约（`timeout`）无法从 cassette 回放，非录制模式下会被显式拒绝。
- 脱敏只覆盖顶层字段，嵌套结构内的敏感值需在工具层处理。
- 并发调用下 cassette 的消费顺序不做保证，当前只支持单线程顺序消费。
- 运行目录与 cassette 会随运行次数增长，尚未提供清理与索引。
- 本版本覆盖第 1 层与回放骨架。轨迹级断言、LLM 裁判、端到端沙箱、CI 门禁、可观测与性能层均为后续变更。

## 规格来源

本项目的设计基线在 `openspec/`。当前变更 `add-tool-contract-eval` 包含 proposal、能力规格、design 与 tasks，可用 `openspec show add-tool-contract-eval` 查看。
