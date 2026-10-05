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

## 质量门禁

门禁回答三个问题：有没有基线、有没有阈值、低于阈值能不能自动拦住。

```bash
# 把最近一次运行捕获为基线
python -m agenteval --home .agenteval baseline

# 对最近一次运行做门禁判定
python -m agenteval --home .agenteval gate
```

门禁有两类互相独立的条件，任一类不满足都会不通过：

- 阈值：`--min-pass-rate`（默认 1.0）与 `--max-errors`（默认 0），回答「这次的绝对水平够不够」
- 回归：基线中通过但当前失败或错误的用例，以及基线中存在、当前缺失的用例，回答「这次比上次差了没有」

```bash
python -m agenteval --home .agenteval gate --json                        # 机器可读报告
python -m agenteval --home .agenteval gate --min-pass-rate 0.95 --max-errors 0
python -m agenteval --home .agenteval gate --allow-regressions           # 豁免回归，但仍会列出
```

退出码：通过为 0，不通过为 1，用法错误为 2。没有基线时只按阈值判定，并在报告中标注 `baseline: none`，不会因为缺基线就直接失败；但显式指定了某个不存在的基线名会报错，避免误以为比对过了。

CI 工作流在 `.github/workflows/ci.yml`，依次跑测试、录制 cassette、捕获基线、回放并执行门禁，最后一步的退出码就是整个 job 的结论。

## 轨迹级过程断言

只看最终答案是碰运气的。过程断言检查 agent 的中间步骤：工具选得对不对、顺序是否合理、有没有多余步骤、失败后是否恢复、上一步的状态有没有传下去。

过程用例与工具契约用例写在同一个用例文件里，声明要执行的步骤和要验证的断言：

```json
{
  "id": "process-exact-sequence",
  "kind": "process",
  "steps": [
    { "target": "echo_tool", "input": { "message": "reserve" } },
    { "target": "idempotent_tool", "input": { "idempotency_key": "order-1" } }
  ],
  "checks": [
    { "kind": "tool_sequence", "expected": ["echo_tool", "idempotent_tool"], "mode": "exact" },
    { "kind": "no_extra_calls", "allowed": ["echo_tool", "idempotent_tool"] }
  ]
}
```

四类过程断言：

| kind | 关键字段 | 验证内容 |
| --- | --- | --- |
| `tool_sequence` | `expected`、`mode` | 调用顺序；`subsequence`（默认）允许多余步骤，`exact` 要求逐项一致 |
| `no_extra_calls` | `allowed` | 除允许集合外没有其他工具被调用 |
| `recovery` | `failed_tool`、`recovered_by` | 某次失败之后存在成功的后续调用 |
| `state_continuity` | `producer`、`consumer`、`producer_field` | 生产者产出的值出现在消费者调用的参数里 |

```bash
python -m agenteval --home .agenteval run --cases examples/process_cases.json --demo
```

两个刻意的取舍：顺序断言默认子序列，因为真实 agent 常有多余的准备性调用，默认完全匹配会制造误报，最后逼着团队把断言放宽到没有意义；`recovery` 在「失败从未发生」时判失败而不是跳过，因为恢复断言的目的是证明恢复路径被走过，没失败就说明这条路径根本没被覆盖。

要在自己的 agent 里记录真实轨迹，用记录包装器：

```python
from agenteval.models import Trace
from agenteval.process import TraceSession, check_process_case, wrap_registry_for_trace

session = TraceSession()
registry = wrap_registry_for_trace(my_registry, session)

trace = Trace(case_id="my-task")
session.begin_case(trace)
run_my_agent(registry)  # 期间所有工具调用都会进入 trace
session.end_case()

verdict = check_process_case(my_process_case, trace)
```

注意工具实例在整轮运行中是共享的，有状态的桩件会跨用例累积状态。示例用例因此只使用结果不依赖历史调用的工具；真实工具本就如此，用例设计时要把这一点考虑进去。

### 把真实轨迹变成可复用的评测资产

过程用例默认由运行器驱动步骤。要在没有 agent、没有外部服务的环境里复评一份真实轨迹，先把轨迹存下来再对它求值。

在自己的 agent 里记录并保存：

```python
from agenteval.models import Trace
from agenteval.process import TraceSession, wrap_registry_for_trace
from agenteval.trace_store import TraceStore

session = TraceSession()
registry = wrap_registry_for_trace(my_registry, session)
trace = Trace(case_id="checkout-flow")
session.begin_case(trace)
run_my_agent(registry)
session.end_case()

TraceStore.default().save("checkout-flow", trace, source="manual")
```

也可以直接从一次已存储的运行里提取：

```bash
python -m agenteval --home .agenteval trace save --name checkout --run <run_id> --case <case_id>
python -m agenteval --home .agenteval trace list
```

这类用例只声明断言、不声明步骤：

```json
{
  "id": "checkout-flow",
  "kind": "process",
  "checks": [
    { "kind": "tool_sequence", "expected": ["create_order", "pay", "ship"], "mode": "subsequence" }
  ]
}
```

```bash
python -m agenteval --home .agenteval run --cases examples/import_cases.json --demo --trace checkout
python -m agenteval --home .agenteval gate
```

对已保存轨迹求值时不调用任何工具，结论不受环境影响，可以直接进 CI。运行记录会标注所用的轨迹名称，判定所依据的那条轨迹也随运行落盘，便于事后复查。

两个来源互斥：一条过程用例既声明步骤、又引用外部轨迹会被判 error，而不是让系统替你选一个。引用的轨迹不存在时直接报错并终止，不会留下一次没有意义的运行。

## 端到端任务成功率

前面几层回答的是「工具对不对、过程对不对」。业务方只关心一个问题：一批真实任务里做成了几件。

任务只描述要达成什么，agent 的解法不写进任务定义：

```json
{
  "id": "task-charge-and-settle",
  "kind": "task",
  "description": "扣款并结算后，账本余额必须等于扣款额",
  "checks": [
    { "kind": "tool_sequence", "expected": ["ledger_tool", "ledger_tool"], "mode": "subsequence" },
    { "kind": "no_extra_calls", "allowed": ["ledger_tool"] },
    { "kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 30 }
  ]
}
```

成功判据分两类：过程断言（复用轨迹层）与 `final_state` 终态断言。终态断言读取工具暴露的 `state()` 并与期望值比较；工具未注册、没有 `state()`、字段不存在都判失败并说明原因，而不是抛异常。

```bash
python -m agenteval --home .agenteval task run \
  --tasks examples/tasks.json \
  --registry agenteval.fakes:build_task_registry \
  --agent examples/demo_agent.py:build_agent
```

```
run_id: 20261004T092700Z-1a2b3c4d
tasks: 2  resolved: 2  resolved_rate: 1.0000
  [pass ] task-charge-and-settle  (1.2 ms)
  [pass ] task-refund-reduces-balance  (0.9 ms)
```

关键约束是环境隔离：环境由工厂构建，**每个任务开始前重建一次**，agent 也每个任务重建一次。这是通过率可信的前提，环境不稳定时通过率本身就是噪声。

agent 抛异常记为 error（没跑起来），判据不满足记为未解决（跑了但没做对），两者分开列出，因为排查方向完全不同。全部任务解决时退出码为 0，否则为 1；空任务集不算通过，避免在空集上拿到满分。

### 重复尝试与 pass@k

任务可以声明尝试次数。每次尝试从全新环境与全新 agent 开始，逐次产出独立判定：

```json
{
  "id": "task-flaky-charge",
  "kind": "task",
  "attempts": 3,
  "checks": [{ "kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 30 }]
}
```

```
tasks: 1  resolved: 1  resolved_rate: 1.0000
attempts: 3  pass@1: 0.0000  pass@k: 1.0000
  [fail ] task-flaky-charge#1
  [pass ] task-flaky-charge#2
  [pass ] task-flaky-charge#3
```

`pass@1` 与 `pass@k` 分开报，是为了区分「做不成」与「不稳定」：这条任务一次都没成（pass@1 = 0），但三次内能成（pass@k = 1）。只跑一次会把这种 agent 误判为不具备能力。

逐次判定意味着门禁能按尝试维度设阈值——稳定的任务可以要求每次通过，波动的任务用比例阈值。默认尝试一次，此时判定标识不带序号，既有基线与回归资产无需重建。

任务结果就是普通的 Verdict 与运行记录，因此门禁零改动即可覆盖任务成功率：

```bash
python -m agenteval --home .agenteval gate --min-pass-rate 1.0 --max-errors 0
```

## 失败回流

看到「用例没过」之后，下一步应该是把它变成可反复执行的回归用例。否则真实失败样本永远停在报告里，这是质量闭环最常缺的一环。

```bash
# 归因最近一次运行，并把可复现的失败写成候选用例
python -m agenteval --home .agenteval triage --emit candidates.json
```

```
run: 20261004T093000Z-1a2b3c4d
failures: 1  reflowable: 1  verified: 1
  [fail] reflow-probe
         - tool_sequence.exact: expected=['payout_tool'] actual=['echo_tool']
         reflow: verified=True trace=repro-reflow-probe candidate=reflow-probe@repro
```

它做三件事：保存证据轨迹、生成绑定该轨迹的候选用例、并**自证**候选用例确实复现了原失败。生成的候选可以直接重跑：

```bash
python -m agenteval --home .agenteval run --cases candidates.json --demo
```

一条重要边界：**期望一律沿用原用例，绝不从失败轨迹反推**。失败轨迹记录的是 agent 做了什么，不是它该做什么；反推出来的期望必然通过，看起来闭环了，实际什么都没守住。依赖环境状态的失败（终态判据）会被标记为不可自动回流，而不是被悄悄转成一条假通过用例。

候选用例通过用例级 `trace` 字段绑定自己的证据轨迹，因此多条候选可以在同一次运行里各自绑定不同轨迹：

```json
{
  "id": "reflow-probe@repro",
  "kind": "process",
  "trace": "repro-reflow-probe",
  "checks": [{ "kind": "tool_sequence", "expected": ["payout_tool"], "mode": "exact" }]
}
```

归因需要原始用例定义，默认取运行元信息里记录的文件路径，也可以用 `--cases` 指定。

### 接入跑在别处的 agent：导入工具调用审计

真实 agent 多数不在平台里运行，工具调用记在自己的审计日志中。只要有工具名、参数、生命周期状态、起止时间与耗时，就能导入成平台轨迹，**不需要改动 agent 的代码**。

```bash
python -m agenteval --home .agenteval trace import \
  --from examples/kingfar_audit.json \
  --name kingfar-eeg-sample-loss \
  --case-id task-eeg-sample-loss
```

```
trace 'kingfar-eeg-sample-loss' imported from examples\kingfar_audit.json
records: 5  case: task-eeg-sample-loss  events: 5
limitation: no raw result values were recorded; the state_continuity check cannot be evaluated on this trace
limitation: 1 call(s) have no completion; they are recorded as interrupted without a result or duration
```

JSON 数组与 CSV 导出都支持，示例取的是 kingfar-aiops `/audits/tool-calls/export` 的列结构。导入后就是普通命名轨迹：

```bash
python -m agenteval --home .agenteval run --cases examples/kingfar_cases.json --demo
```

```
cases: 1  pass: 1  fail: 0  error: 0
metrics: calls=5 retries=1 p50=6020.0ms p95=6020.0ms tokens=0/0 usage=not reported
```

指标全部来自审计本身：5 次调用、1 次重复（同参数重试 `SearchLog`）、跨度 6 秒，其中 3 秒是那次 opensearch 超时。这正是功能判据看不见、但业务方会问的东西。

三个刻意的设计：

- **三态生命周期**。审计里存在只有开始、没有结束的记录，代表调用被中断。当作失败是编造，当作成功更危险，保留为「未结束」才如实。
- **缺失就是缺失**。审计通常只存有界结果摘要而非原始返回值。导入时把摘要放进单独字段而不是返回值字段，并明确标注状态传递判据**无法评估**，否则会产生一堆假失败。
- **坏记录直接拒绝**。缺少工具名或开始时间时拒绝并指出第几条，而不是跳过——跳过会让轨迹步骤变少，而步骤数量本身是判据的输入。

字段对照表：

| 审计字段 | 轨迹事件字段 |
| --- | --- |
| `toolName` | `target` |
| `arguments` / `argumentsJson` | `args` |
| `status`：completed / failed / started | `ok` / `error_kind` / 未结束 |
| `startedAt`、`completedAt` | 事件时间戳，耗时与预算据此推导 |
| `resultSummary` | 单独字段，不进入返回值，避免假失败 |
| `errorMessage` | 事件错误消息 |

## 指标与预算

功能判据全绿不代表代价可控：一个用例可能通过，却调用了三十次工具、重试了十次。指标与预算把这类问题变成可判定的东西。

```bash
python -m agenteval --home .agenteval run --cases examples/process_cases.json --demo
```

```
cases: 3  pass: 3  fail: 0  error: 0
metrics: calls=7 retries=0 p50=0.999ms p95=2.004ms tokens=0/0 usage=not reported
```

每次运行都会产出调用总数、重复调用总数、耗时 p50/p95 与 token 汇总。重复调用按「相同目标工具 + 相同参数」统计，因为它正是成本随重试膨胀的形态。

预算判据与顺序、恢复、终态判据并列，声明上限、超限即失败：

```json
{
  "kind": "budget",
  "max_calls": 5,
  "max_retries": 0,
  "max_duration_ms": 2000
}
```

```
[fail ] budget-probe  (3.5 ms)
       - budget.max_retries: max_retries 2 exceeds the limit 1
budget violations: budget-probe:budget.max_retries
```

三个取舍。指标全部从轨迹推导，不引入外部计时器，因此同一份轨迹重复求值得到同样的数字，指标本身也能参与回归比对。只声明部分上限时，未声明的指标不参与判定。token 用量依赖工具主动上报（`ToolResult.usage`），未上报时报告写 `usage=not reported`，而不是给出「零消耗」的结论——后者比没有数字更危险。

预算失败就是普通的判据失败，所以门禁零改动即可覆盖非功能准入：

```bash
python -m agenteval --home .agenteval gate --min-pass-rate 1.0
```

两个口径需要说明：耗时取自轨迹首尾事件的时间差，衡量的是工具调用跨度，不含模型思考时间；分位数用最近秩法，运行记录里标注了所采用的口径。

## 裁判校准

回答质量这类维度没有确定性判据，只能由裁判给分。但裁判本身是需要被验证的测量仪器——未经校准的裁判分不能进质量门禁。

```bash
python -m agenteval judge calibrate \
  --gold examples/judge_gold.json \
  --judge examples/demo_judges.py:build_keyword_judge
```

```
judge: usable for gate
items: 8  agreement: 1.0000 (95% CI 0.6756-1.0000, wilson)
position flips: 0/8 (0.0000)
length bias: 4/7 decisive (0.5714)
thresholds: min_agreement=0.80  max_position_flip_rate=0.20  max_length_bias=0.80
```

平台不调用任何模型：裁判由你提供，是一个接收 `(prompt, response_a, response_b)` 并返回 `"a"` / `"b"` / `"tie"` 的函数。平台负责量化它对不对、偏不偏。

三个检测项：

- **一致率加置信区间**。8 条全对也只有 `0.6756-1.0000` 的区间，这就是为什么小样本上的「完美一致」不能当作精确值。区间用 Wilson 法，小样本下不越界也不虚窄。
- **位置偏置**。交换两个候选重跑，看判定是否翻转。永远选第一个的裁判翻转率是 1.0：

```
judge: reference only
agreement: 0.5000 (95% CI 0.2152-0.7848)
position flips: 8/8 (1.0000)
reasons:
  - agreement 0.5000 is below the minimum 0.8000
  - position flip rate 1.0000 exceeds the maximum 0.2000
```

- **长度偏置**。永远选更长回答的裁判会被识别：

```
agreement: 0.6250 (95% CI 0.3057-0.8632)
length bias: 7/7 decisive (1.0000)
reasons:
  - longer-response win rate 1.0000 exceeds the maximum 0.8000
```

结论只有两种：`usable for gate` 或 `reference only`，退出码 0 或 1。未达标的裁判可以出参考分，但不能拦发布——这是把「裁判分不能当唯一标准」落成机制，而不是写成注意事项。

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
- 本版本覆盖工具契约层、确定性回放、轨迹与过程断言、质量门禁与端到端任务成功率。LLM 裁判、可观测、性能与多端层均为后续变更。
- 端到端任务只做单次执行统计，不做重复尝试与 pass@k；环境隔离靠工厂重建，尚未提供快照恢复。
- token 用量依赖工具上报，覆盖不全时只汇总已上报部分；尚未做 token 预算与成本货币化换算。
- 未提供吞吐与并发压测、显存与推理引擎 benchmark。
- 审计导入只覆盖工具调用边界；状态传递判据要求源数据保留原始返回值，仅有结果摘要时无法评估。
- 裁判校准只覆盖成对偏好型裁判；逐点评分型没有「位置」概念，偏置检测不适用。

## 规格来源

本项目的设计基线在 `openspec/`。当前变更 `add-tool-contract-eval` 包含 proposal、能力规格、design 与 tasks，可用 `openspec show add-tool-contract-eval` 查看。
