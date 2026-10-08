# agenteval

面向 Agent 应用的评测平台：把「Agent 到底能不能用、问题出在哪个环节、贵不贵、稳不稳」变成可重复执行、可进 CI 的结论。

设计遵循三条原则（参考 AI Agent 精细化评测体系）：

- **评测面向架构**：Agent 由感知、规划、记忆、工具四个模块协作运行，评测按同样的结构逐层拆解，让每一层都能独立观测（含 RAG 检索与模型消耗）。
- **指标面向行动**：每个指标是一份「诊断报告」而非「成绩单」——下降时能告诉你该改 Skills、换 MCP 工具、还是优化检索。
- **能力面向产品**：评测不是跑一次就丢的脚本，而是可持续运行、可横向对比、可追溯演进的基础设施。

平台的边界很清楚：**它评测 Agent 应用，不实现 Agent 自身**。工具属于 Agent 自己的工具模块（MCP / Skill），平台不派发、也不代执行；平台负责构造输入、调用 Agent、采集轨迹、交给裁判评分，最后给出质量 × 成本 × 性能的结论。

本文档只覆盖 Agent 评测这条链路。

## 快速开始

```bash
# 1) 安装
python -m pip install -e ".[dev]"

# 2) 注册你的 Agent（详见「接入任意 agent」）
#    也可以先用仓库里的示例 agent 跑一遍：
python -m agenteval --home .agenteval eval run \
  --scope end_to_end \
  --datasets examples/eval_datasets.json \
  --agents examples/self_contained.agents.json \
  --agent @self-contained

# 3) 看报告（也可用 serve 打开只读控制台）
python -m agenteval --home .agenteval report
python -m agenteval --home .agenteval serve --port 8787
```

一次 `eval run` 会走完整链路：**评测范围 → 自动装配数据集 → 调用 Agent 执行 → 采集轨迹 → 裁判评分 → 生成记分卡**。

## 安装

```bash
python -m pip install -e ".[dev]"

# 可选：需要用 Playwright 做界面检查时
python -m pip install -e ".[dev,ui]"
python -m playwright install chromium
```

## 接入任意 agent（Agent Bridge）

被测 agent 不必是 Python，也不必跑在平台进程里。平台用一份 JSON wire 协议与 agent 通信，传输可以是 HTTP endpoint 或本地子进程，agent 只需实现一个入口。

**平台不提供工具。** 工具属于 agent 自己的工具模块（MCP / Skill），由 agent 执行；平台只把「已经发生过的调用」记进轨迹，供调用顺序与多余调用断言使用。

平台发出的请求带协议版本、应用标识、能力、用例数据与对话历史，并通过 `context` 注入会话标识、评测模式与 Mock 数据；agent 返回文本输出、已执行的工具调用、模块信号与 token 用量。同一个 endpoint 能被 task、dialogue、redteam、selection 四条链路复用，用 `capability` 区分。

```json
// 请求（平台 → agent）
{
  "protocol": "agenteval.bridge/1",
  "app": "ledger-agent",
  "version": "1.2.0",
  "capability": "task",
  "case": { "id": "T-001", "user_input": "帮我把 30 元扣款并结算" },
  "tools": [],
  "messages": [{ "role": "user", "content": "帮我把 30 元扣款并结算" }],
  "context": { "session_id": "T-001", "eval_mode": "e2e_real", "mock": {} }
}
```

```json
// 响应（agent → 平台）
{
  "protocol": "agenteval.bridge/1",
  "output": "已扣款 30 元并完成结算，账本余额 30 元。",
  "tool_calls": [
    { "name": "ledger_tool", "arguments": { "op": "charge", "amount": 30 },
      "ok": true, "duration_ms": 12.5 }
  ],
  "signals": [
    { "module": "perception", "payload": { "hit": true, "skill": "ledger-skill", "intent": "charge" }, "duration_ms": 14 },
    { "module": "planning",   "payload": { "route": "skill_hit" }, "duration_ms": 5 },
    { "module": "memory",     "payload": { "turns": 1 }, "duration_ms": 2 },
    { "module": "retrieval",  "payload": { "count": 2, "chunks": [{ "id": "c1" }] }, "duration_ms": 9 }
  ],
  "usage": { "input_tokens": 120, "output_tokens": 40, "model_calls": 2 }
}
```

四个 `module` 取值是 `perception` / `planning` / `memory` / `retrieval`（工具调用单独走 `tool_calls`）；`usage` 汇总模型调用次数与输入输出 Token。缺哪项就少哪项指标，平台不补零。

### 注册多个 agent

`--agents` 指向一个注册表，公司内部每个 agent 一个条目，声明 id、版本、传输与能力：

```json
{
  "agents": [
    {
      "id": "ledger-agent",
      "version": "1.2.0",
      "transport": "subprocess",
      "command": "python",
      "args": ["examples/agents/ledger_agent.py"],
      "capabilities": ["task"]
    },
    {
      "id": "support-agent",
      "version": "0.9.3",
      "transport": "http",
      "url": "http://127.0.0.1:9210/eval",
      "capabilities": ["dialogue", "selection", "redteam"],
      "timeout_s": 20
    }
  ]
}
```

```bash
# 端到端任务，走子进程 agent
python -m agenteval --agents examples/agents.json task run \
  --tasks examples/tasks.json \
  --agent @ledger-agent

# 多轮对话，走 HTTP agent（需先启动 examples/agents/http_support_agent.py）
python -m agenteval --agents examples/agents.json dialogue run \
  --cases examples/dialogue_cases.json \
  --agent @support-agent
```

### 三种引用形式

| 引用 | 含义 |
| --- | --- |
| `module:factory` | 既有的进程内 Python 工厂，零成本快路径 |
| `@app-id` | 从注册表取，评测结论会绑定 app 与版本 |
| `http(s)://...` / `cmd:...` | 直连，不经过注册表 |

一次针对注册表 agent 的运行，结论里会带上 `app` 与 `app_version`，因此能回答「测的是哪个应用的哪个版本」。

### 接入已有后端

很多生产 agent 的工具、会话与调用审计都在它自己的后端里，平台既不该也拿不到那些工具——`tool_calls` 与 `signals` 就是为这种情况准备的：agent 把已经发生过的调用与各模块的执行信息回报上来即可。

`bridges/` 用来放这类适配桥。仓库里带了一个模板 `bridges/http_agent.py`，走的是「登录 → 建会话 → 发消息 → 拉动作记录」这套常见形状；换成你自己的系统时，只改其中四个函数里的路径与字段名即可，平台内核不用动。

```json
{
  "id": "http-agent",
  "version": "0.1.0",
  "transport": "subprocess",
  "command": "python",
  "args": ["bridges/http_agent.py"],
  "env": { "AGENT_BASE_URL": "http://127.0.0.1:8080" },
  "capabilities": ["task"],
  "timeout_s": 240
}
```

```bash
python -m agenteval --home .agenteval --agents examples/http_agent.agents.json \
  task run \
  --tasks my_tasks.json \
  --agent @http-agent
```

## 函数选择打分

工具选错和参数填错，最终答案里未必看得出来。选择打分把调用规整成「函数名 + 参数」逐项比对，覆盖错选、漏选、多选、参数不符、并行调用缺失，以及无关请求下的正确「不调用」。

```json
{
  "cases": [
    {
      "id": "parallel-complete",
      "prompt": "把北京和上海的天气都查一下",
      "available_tools": ["get_weather", "send_email"],
      "expected": [
        { "name": "get_weather", "arguments": { "city": "北京" } },
        { "name": "get_weather", "arguments": { "city": "上海" } }
      ],
      "actual": [
        { "name": "get_weather", "arguments": { "city": "北京" } },
        { "name": "get_weather", "arguments": { "city": "上海" } }
      ]
    }
  ]
}
```

```bash
python -m agenteval --home .agenteval selection run --cases examples/selection_cases.json
# 也可以让 agent 当场产出调用，而不是读用例里的 actual
python -m agenteval --home .agenteval selection run \
  --cases examples/selection_cases.json \
  --agent myagent.py:build_agent
```

用例里的 `actual` 供离线复评使用；`--agent` 则接收用例、返回结构化调用列表。解析模型的自由文本输出是接入方的责任，打分只处理已结构化的调用，因此结论确定且可解释。

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

CI 工作流在 `.github/workflows/ci.yml`：跑测试、跑一次评测、捕获基线、对最新运行做门禁判定，最后一步的退出码就是整个 job 的结论。

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

## 多轮对话

一次性任务协议下，平台把环境与任务交给 agent 就交出了控制权，「它有没有先问再动手」「几轮才收敛」这类问题无从断言。多轮场景用一条独立协议：平台掌握循环，agent 只负责一轮。

```python
# examples/demo_dialogue_agent.py
def build_agent():
    def respond(registry, conversation):
        if not conversation.agent_turns():
            return "请先告诉我故障发生的时间范围。"   # 先问
        registry.get("echo_tool").invoke(message="order-12345 payment timeout")
        return "已确认：支付网关超时。"                # 收尾
    return respond
```

用例声明剧本、回合预算与断言：

```json
{
  "id": "diagnose-after-asking",
  "kind": "dialogue",
  "script": ["订单 12345 一直没支付成功，帮我看看。", "时间大概是今天下午两点到三点。"],
  "max_turns": 4,
  "checks": [
    { "kind": "required_clarification", "marker": "时间", "before_tool": "echo_tool" },
    { "kind": "termination", "marker": "已确认" }
  ]
}
```

两类对话断言：

| kind | 关键字段 | 验证内容 |
| --- | --- | --- |
| `required_clarification` | `marker`、`before_tool` | 首次调用 `before_tool` 之前，必须有一个 agent 回合包含 `marker` |
| `termination` | `marker` | 最后一个 agent 回合必须包含 `marker` |

```bash
python -m agenteval --home .agenteval dialogue run \
  --cases examples/dialogue_cases.json \
  --agent examples/demo_dialogue_agent.py:build_agent \
  --registry agenteval.fakes:build_demo_registry
```

剧本模拟器用尽即自然结束，此时回合预算判据通过；预算先耗尽则判失败，于是「几轮内收敛」本身成为可断言的判据。每轮的工具调用被切片归到对应回合，对话结果同样写入运行记录与轨迹，因此四类过程断言与门禁无需改动即可复用。

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

## 真实沙箱（容器 + 快照/重置）

端到端任务的隔离如果只靠工厂重建进程内对象，依赖的数据库与向量库仍是同一份长期运行的实例，上一个用例写进去的数据会影响下一个。沙箱能力把一组 compose 服务变成可启动、可检查、可快照、可重置的对象。

沙箱定义声明 compose 文件、项目名、健康要求与重置策略：

```json
{
  "sandbox": {
    "id": "demo-sandbox",
    "project": "agenteval-sandbox-demo",
    "compose_file": "sandbox/compose.yaml",
    "services": ["sleeper"],
    "health_services": ["sleeper"],
    "volumes": ["sleeper-data"],
    "reset": "recreate",
    "teardown": "down",
    "health_timeout_s": 90
  }
}
```

三种重置策略：

| reset | 做什么 | 适用 |
| --- | --- | --- |
| `recreate` | 移除数据卷后重建，回到全新状态 | 正确性优先的回归 |
| `snapshot` | 从已保存的卷快照恢复，回到预置数据状态 | 需要初始数据的业务任务 |
| `none` | 只检查健康，不改环境 | 只读评测与本地调试 |

```bash
# 独立使用
python -m agenteval sandbox up --sandbox examples/sandbox.json
python -m agenteval sandbox health --sandbox examples/sandbox.json
python -m agenteval sandbox snapshot --sandbox examples/sandbox.json --name baseline
python -m agenteval sandbox reset --sandbox examples/sandbox.json
python -m agenteval sandbox down --sandbox examples/sandbox.json --volumes

# 接到评测上：整轮前健康门禁，每个用例前重置
python -m agenteval --home .agenteval --agents examples/agents.json task run \
  --tasks examples/tasks.json \
  --registry agenteval.fakes:build_task_registry \
  --agent @ledger-agent \
  --sandbox examples/sandbox.json
```

接到评测上以后：整轮开始前做健康检查，不通过就整轮失败、不产出用例判定；每个用例前按策略重置，重置失败只影响该用例；运行记录里带上沙箱的项目名、compose 内容哈希与镜像标签，使结论能追溯到具体环境。不传 `--sandbox` 时行为与之前完全一致。

沙箱定义指向任意 compose 文件即可。`reset: none` 只做健康检查，适合不干扰正在开发的环境；`reset: snapshot` 则先执行 `sandbox snapshot --name baseline` 生成卷快照，之后每个用例前从该快照恢复数据卷。真实 stack 启动较慢，这类定义要把 `command_timeout_s` 调高。

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

### 增量回流：让线上失败自动进入数据集

`triage` 面向一次指定运行；`reflow` 面向持续产生的失败。它扫描已存储的全部运行，只处理此前没见过的失败模式，把通过自证的候选用例累积进一个数据集，不需要人工指定运行标识。

```bash
python -m agenteval --home .agenteval reflow run
```

```
runs scanned: 4
failures: 2  new: 2  duplicates: 0  not reflowable: 1
emitted regression cases: 1  dataset size: 1
```

三个机制：

| 机制 | 作用 |
| --- | --- |
| 失败指纹 | 由「用例 + 失败判据的期望与实际」推导，同一失败模式只回流一次，不同失败模式互不吞并 |
| 水位账本 | 记录已处理指纹与首次发现时间，重复执行幂等；运行记录本身保持完整，不因「已回流」被删除 |
| 累积数据集 | 只追加不覆盖，重复执行不会丢失人工编辑 |

候选进入数据集前会对证据轨迹自证，只有真的复现了原失败才会写入。默认落在 `<home>/reflow/regression_cases.json`，它适合放进 CI 定时执行。

一点语义说明：候选绑定的是**失败轨迹**，它是「这个失败可复现」的证据资产，重放时仍会失败；要变成修复后应当通过的活体回归，需要把它提升为带步骤的用例或绑定真实 agent。

### 接入跑在别处的 agent：导入工具调用审计

真实 agent 多数不在平台里运行，工具调用记在自己的审计日志中。只要有工具名、参数、生命周期状态、起止时间与耗时，就能导入成平台轨迹，**不需要改动 agent 的代码**。

```bash
python -m agenteval --home .agenteval trace import \
  --from path/to/audit_export.json \
  --name imported-checkout-trace \
  --case-id task-checkout-timeout
```

```
trace 'imported-checkout-trace' imported from path/to/audit_export.json
records: 5  case: task-checkout-timeout  events: 5
limitation: no raw result values were recorded; the state_continuity check cannot be evaluated on this trace
limitation: 1 call(s) have no completion; they are recorded as interrupted without a result or duration
```

JSON 数组与 CSV 导出都支持：只要有工具名、参数、状态、起止时间与耗时，服务端就能把它转成轨迹，字段缺失时记为限制而不是报错。导入后就是普通命名轨迹，可以用过程用例对它求值：

```bash
python -m agenteval --home .agenteval run --cases path/to/import_cases.json --demo
```

```
cases: 1  pass: 1  fail: 0  error: 0
metrics: calls=5 retries=1 p50=6020.0ms p95=6020.0ms tokens=0/0 usage=not reported
```

指标全部来自审计本身：5 次调用、1 次重复（同参数重试）、跨度 6 秒，其中 3 秒耗在超时的那次调用上。这正是只看最终答案时看不见、但业务方会追问的东西。

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

## 评测执行引擎：从范围到报告

一篇文章里的评测链路是固定的：**提交评测请求（范围 + 评测模式）→ 自动装配数据集 → 并发执行 → 采集轨迹与指标 → 结构化评分 → 生成报告**。`eval run` 把这条链路一次跑完：范围决定装配哪些数据集，数据集注册表决定每个数据集从哪些用例文件加载。

```json
{
  "datasets": {
    "basic_function": ["contract_cases.json"],
    "knowledge_qa": ["process_cases.json"],
    "tool_call": ["scorecard_cases.json"]
  }
}
```

注册表里的相对路径按注册表文件所在目录解析。一次评测请求直达报告：

```bash
python -m agenteval --home .agenteval eval run \
  --scope end_to_end \
  --datasets examples/eval_datasets.json \
  --agents examples/self_contained.agents.json \
  --agent @self-contained \
  --concurrency 1 --timeout 120 --retries 2
```

任务与多轮对话的检查可以声明 `metric`，判定会带上该指标，记分卡因此按文章那套指标（`task_completion`、`multi_turn_completion`、`tool_call_accuracy` 等）聚合，而不是回退到断言名。

工具属于 **Agent 自己的工具模块**（MCP / Skill）：`eval run` **不接受任何工具环境参数**，Agent 用它自己的工具完成任务，并把「已经执行过的调用」（工具名、参数、是否成功、耗时）回报给平台记账；平台不派发工具、也不代执行。

`eval run` 走的是文章 §6.1 的那条链路：**评测用例 → 交给 Agent → 采集轨迹 → 交裁判**。因此它只执行 **agent 用例**——单轮任务（`TaskCase`）与多轮对话（`DialogueCase`），并强制要求 `--agent`。平台上还有「直接调用工具、不经 agent」的契约与过程用例（工具契约测试那一层），那类用例用 `run` 跑，`eval run` 会明确拒收。

```
eval plan: end_to_end  eval_mode: e2e_real
datasets: basic_function, knowledge_qa, multi_turn, abnormal_input
primary metric: task_completion
execution: concurrency=1  timeout_s=120.0  retries=2
  - basic_function: 1 file(s)
  - knowledge_qa: 1 file(s)
  - missing (no files registered): multi_turn, abnormal_input

scorecard: …（质量 × 成本 × 性能三栏）
```

执行控制与文章第 6 节一致：

- **并发**。`--concurrency` 大于 1 时并行执行，前提是被测 agent 自身并发安全；批量提交（`eval submit`）默认 3 线程。
- **单条超时**。`--timeout` 是单条用例的等待上限，超时判为 `error` 并说明原因，不阻塞其余用例。
- **重试与容错**（文章 §6.6）。`--retries`（默认 2）**只对执行异常**重试——超时、异常等判为 `error` 的用例，每次间隔 `--retry-interval`（默认 3 秒）；**判定不通过（fail）不重试**，因为那是能力问题，重试不会改变结果。逐用例尝试次数写入运行元数据。
- **两种评测模式**。`--eval-mode e2e_real` 走真实链路；`--eval-mode e2e_mock` 时平台把模式与 Mock 数据一起注入执行上下文，由拥有工具的 agent 用预设数据代替真实外部调用。声明的模式写进运行元数据，记分卡据此标注。
- **范围自动装配**。范围内的数据集若没有注册用例文件，会列入 `missing` 而不是被静默忽略；所有数据集都无文件时命令以可读错误结束。

`eval run` 结束时会把本次记分卡写成一条 `report` 结论，因此控制台与门禁无需改动即可消费。一次评测可以同时装配单轮任务与多轮对话，引擎按用例类型分派给同一个 Agent，最后汇总成一份运行记录。

### 全量范围（文章 §6.2）

评测范围共 15 种：8 类数据集、端到端、**全量评测**（`full`，装配全部 8 类数据集）、四个模块范围与核心模块范围。

### 用例的输入、期望与 Mock（文章 §6.1 步骤 2/4a）

用例可以声明 `user_input`、`expected_output`、`expected`（逐指标期望）、`mock`、`session_id` 与 `expected_route`。这些字段可选，既有用例无需改动。

执行时平台会**注入执行上下文**（文章 §6.1 步骤 4a）：`session_id`（缺省取用例标识，多轮对话各轮共享）与 `mock` 数据一起放进 bridge 请求的 `context`，`user_input` 作为给 Agent 的用户消息；同时把它们连同 Agent 输出、轨迹证据一起交给裁判。

### 裁判在引擎内执行（文章 §6.1 步骤 4d）

`eval run` 可以带 `--judge`：按评测范围自动装配裁判任务（`--judge-tasks` 可覆盖），对每条用例把 **Agent 输出 + EvalTrace** 交给裁判，逐指标结论并入同一份运行记录，记分卡直接消费——不必再手工跑 `judge task`。

```bash
python -m agenteval --home .agenteval eval run \
  --scope end_to_end --datasets examples/eval_datasets.json \
  --agents examples/self_contained.agents.json --agent @self-contained \
  --judge examples/demo_judge_tasks.py:build_keyword_judge \
  --judge-tasks examples/eval_judge_tasks.json
```

### Trace 结构（对齐文章 §6.3）

一条用例的轨迹由事件流组成，覆盖文章的六组信息：

| 节点 | 事件 | 字段 |
| --- | --- | --- |
| 感知 | `module.perception` | `hit`、`skill`、`intent`、`duration_ms` |
| 规划 | `module.planning` | `duration_ms`（另含 `route` / `tools`） |
| 记忆 | `module.memory` | `duration_ms`、`turns`（另含 `injected`） |
| 工具 | `tool_call` | `target`、`args`、`ok`、`error_kind`、`attempts`、`duration_ms` |
| RAG | `module.retrieval` | `duration_ms`、`count`、`chunks` |
| 模型消耗 | `model.usage` | `model_calls`、`input_tokens`、`output_tokens` |

工具调用与模型消耗由**平台记账**（Agent 回报或平台代执行）；感知/规划/记忆/RAG 是 Agent 内部信息，由 Agent 通过 bridge 的 `signals` 上报。缺哪项，哪项就不出现在报告里，不补零。

### 运行时指标落数（文章 §6.3）

记分卡不再只是「声明指标」，而是把轨迹里的数据落成数字：

- **模块级延迟**：工具调用耗时有两个来源——平台代执行时由**平台自动测量**；Agent 自带工具时由 **Agent 在回报的调用里带上 `duration_ms`**。其余（感知/规划/记忆/检索）由 `module.*` 信号的 `duration_ms` 聚合出 `intent_latency`、`planning_latency`、`memory_injection_latency`、`retrieval_latency`、`tool_latency`，连同 `e2e_latency` 一起给出均值与 p50/p95。没有数据的指标不出现在报告里，不臆造零值。
- **模型调用次数**：agent 在 bridge 的 `usage` 里上报 `model_calls`，平台贯通 `TokenUsage → CaseMetrics → 运行指标 → 记分卡`，成本栏展示 `model_calls`。

### 轨迹感知的裁判（文章 §6.1 步骤 4d）

裁判不只看输出文本。`judge task --run <run_id>` 会把该运行轨迹里的中间数据一并交给裁判：

```bash
python -m agenteval --home .agenteval judge task \
  --cases examples/judge_task_cases.json \
  --tasks examples/judge_tasks.json \
  --run <run_id> \
  --judge examples/demo_judge_tasks.py:build_keyword_judge
```

平台从轨迹抽取命中 Skill、路由方向、调用过的工具、检索片段与各段耗时（`evidence_from_trace`）；用例声明了 `expected_route` 而实际路由不符时，按 §5.4 跳过依赖检索证据的下游指标。

### 多轮对话特化（文章 §6.4）

```bash
python -m agenteval --home .agenteval dialogue run \
  --cases examples/dialogue_cases.json --agent @support-agent --turn-settle 2
```

`--turn-settle`（默认 2 秒）在每一轮结束后等待会话持久化完成，再发起下一轮，避免下一轮读不到历史。Token 与模型调用经 bridge 按整组对话累计。

### 异步作业：提交、轮询、取消（文章 §6.7）

批量评测支持异步提交-轮询，批量并发默认 3 线程：

```bash
# 提交：立即返回 taskId，后台执行
python -m agenteval --home .agenteval eval submit \
  --scope end_to_end --datasets examples/eval_datasets.json --demo

# 轮询单条，或列出全部
python -m agenteval --home .agenteval eval status <taskId>

# 运行中取消
python -m agenteval --home .agenteval eval cancel <taskId>
```

作业记录落在 `<home>/jobs`，每条一个文件，因此提交、轮询与取消互不阻塞；后台执行在用例边界检查取消标志。已完成或已失败的任务再次取消，保持最终状态不变。
## 评测范围与三维记分卡

一次运行的通过率只说「有多少用例过了」，不说「哪个维度、哪个场景出了问题」。评测范围画像把这层诊断补上：范围决定装配哪些数据集，数据集决定适用哪些指标，每个范围（或数据集）有一个主指标。

画像参考 AI Agent 精细化评测体系：八个数据集（基础技能、知识问答、多轮对话、异常输入、工具调用、多意图、模糊意图、长对话衰减），加端到端与四个模块级范围。模块级范围的主指标覆盖数据集主指标——感知看意图识别准确率、规划看路由决策准确率、记忆看短期记忆保留率、工具看工具调用准确率。

用例与断言可以声明归属，运行器会把它透传到判定结果：

```json
{
  "id": "tool-call-wrong-type",
  "target": "echo_tool",
  "scene": "参数映射",
  "dataset_type": "tool_call",
  "input": { "message": "hello", "count": 2 },
  "check": {
    "kind": "wrong_type",
    "overrides": { "count": "not-an-integer" },
    "metric": "param_mapping_accuracy"
  }
}
```

`report` 把一次运行折算成质量、成本、性能三栏：

```bash
python -m agenteval --home .agenteval run --cases examples/scorecard_cases.json --demo
python -m agenteval report --scope tool_call
```

```
scorecard: 20261008T020113Z-29d57c2d
scope: tool_call  eval_mode: e2e_real
datasets: tool_call
primary metric: tool_call_accuracy
cases: 3  pass: 3  fail: 0  error: 0  skipped: 0
pass_rate: 1.0000  primary_pass_rate: 1.0000 (分母排除跳过与错误)
quality:
  - tool_call_accuracy: 1.0000 (pass 3/3, skipped 0)
  - param_mapping_accuracy: 1.0000 (pass 1/1, skipped 0)
cost:
  - tool_calls: 4  retries: 0  tokens: 0/0  usage: not reported
performance:
  - p50: 0.998ms  p95: 2.003ms  total: 3.996ms
scenes:
  - 参数映射: 1.0000 (pass 2/2, skipped 0)
  - 多步流程: 1.0000 (pass 1/1, skipped 0)
```

两个口径与门禁不同：

- **主指标决定通过与失败**。一条用例是否通过只看该范围的主指标，其余指标只做诊断。内容正确但格式不合规的回答，不会因为次要指标被否定；报告仍如实保留原始判定。
- **跳过不进分母**。当上游错误（例如路由误触发）让下游指标没有执行机会时，该断言标记为 `skipped`，既不算通过也不算失败，通过率的分母只统计既未跳过也未出错的用例。这样路由错误只体现在路由决策指标上，不会雪崩式拉低其他模块的数字。

`report --json` 输出同一份记分卡的结构化形式，并把一条 `report` 结论写进运行根目录的 `reports/`，可被只读控制台的结论接口直接读取。评测模式取运行元数据里声明的值，范围默认取运行元数据，缺省回落到端到端。

控制台的「结论」分区会识别这条 `report` 结论，把它渲染成三维记分卡：质量栏按感知、规划、记忆、工具给出模块级与指标级通过率，另有成本、性能、场景通过率，以及只列未通过与被跳过用例的诊断明细。


## 模块级评测（感知 / 规划 / 记忆 / 工具）

三维记分卡回答「哪个维度、哪个场景」，模块级评测回答「哪个模块」。参考文章的 EvalTrace 设计，被测 agent 的每个节点把执行信息写进轨迹，平台据此做模块级断言，而不是从最终回复里反推。信号通过 bridge 协议的可选 `signals` 字段上报：

```json
{
  "output": "…",
  "signals": [
    { "module": "perception", "payload": { "intent": "knowledge_qa", "skill": null, "hit": false }, "duration_ms": 15 },
    { "module": "planning", "payload": { "route": "skill_miss", "tools": ["repo_vector_search"] }, "duration_ms": 4 },
    { "module": "memory", "payload": { "turns": 2, "injected": ["商品 1005007651467330"] } },
    { "module": "retrieval", "payload": { "chunks": [{ "id": "chunk-sale-rule" }] } }
  ]
}
```

信号以 `module.<名称>` 事件落在既有轨迹上，运行记录格式不变。四个模块对应五种断言：

| 模块 | 判据 | 断言内容 |
| --- | --- | --- |
| 感知 | `intent_match` | 意图与命中的 Skill 是否与期望一致 |
| 规划 | `route_decision` | 走 Skill 还是知识库检索，以及选中了哪些工具 |
| 规划 | `tool_decision` | 决定调用的工具集合 |
| 记忆 | `memory_retention` | 注入内容是否保留前文关键信息、会话轮数下限 |
| 记忆 | `retrieval_hit` | 检索是否召回了期望的知识片段 |

用示例 agent 跑一遍完整闭环：

```bash
python -m agenteval --home .agenteval --agents examples/module_agents.json task run \
  --tasks examples/module_tasks.json \
  --registry agenteval.fakes:build_task_registry \
  --agent @module-agent
python -m agenteval report --scope core_module
```

```
modules:
  - perception: 1.0000 (pass 2/2, skipped 0)
  - planning: 1.0000 (pass 3/3, skipped 0)
  - memory: 1.0000 (pass 2/2, skipped 0)
```

一条与文章一致的规则：路由决策失败时，依赖检索证据的下游指标（忠实性、检索精确率/召回率）自动标记为跳过，既不计入分子也不计入分母——路由错误只体现在路由决策指标上，不会雪崩式拉低记忆模块的数字。

## Judge Task（结构化裁判任务）

裁判校准回答「这个裁判靠不靠谱」，Judge Task 回答「这条用例的这个指标到底达没达标」。参考文章第 5 节，平台把逐指标判定拆成结构化的裁判任务：**一个任务只评一个指标**，裁判返回**严格 JSON**（先 `reasoning` 后结论），平台校验字段后折算成通过或不通过；无法解析或字段不合法判为 `error`，绝不静默通过。

六种任务类型覆盖文章的指标集：

| 类型 | 裁判结论字段 | 典型指标 |
| --- | --- | --- |
| `binary` 二元判定 | `verdict`: `pass`/`fail` | 任务完成率、忠实性、异常输入处理率 |
| `classification` 单标签分类 | `label` | 意图识别准确率、路由决策准确率、降级触发准确率 |
| `multi_label` 多标签匹配 | `labels` | 多意图识别率、工具调用准确率、长期检索召回率 |
| `extraction` 抽取比对 | `fields` | 参数映射准确率 |
| `score` 量表评分 | `score` | 规划路径评分、用户满意度 |
| `preference` 成对偏好 | `choice`: `a`/`b`/`tie` | 相对质量、多模型 A/B |

指标到类型的默认归口已内置，也可以用一个任务规格文件声明候选标签、比对字段与评分阈值：

```json
{
  "tasks": [
    { "metric": "task_completion", "kind": "binary" },
    { "metric": "route_accuracy", "kind": "classification", "labels": ["skill_hit", "skill_miss"] },
    { "metric": "param_mapping_accuracy", "kind": "extraction", "fields": ["country", "product_id"] },
    { "metric": "planning_path_score", "kind": "score", "min_score": 0, "max_score": 1, "threshold": 0.5 }
  ]
}
```

用例声明输入、被测输出、参考与逐指标期望，可选地带上 `evidence`：

```json
{
  "cases": [
    {
      "id": "KQA-003",
      "dataset_type": "knowledge_qa",
      "input": "IC 的 deleteAllProducts 接口怎么调用？",
      "output": "IC 中不存在 deleteAllProducts 接口。标签=skill_miss",
      "metrics": ["task_completion", "faithfulness"],
      "evidence": { "route_error": true },
      "expected": { "task_completion": "pass", "faithfulness": "pass" }
    }
  ]
}
```

```bash
python -m agenteval --home .agenteval judge task \
  --cases examples/judge_task_cases.json \
  --tasks examples/judge_tasks.json \
  --judge examples/demo_judge_tasks.py:build_keyword_judge
python -m agenteval --home .agenteval report
```

三条与文章一致的口径：

- **单一职责**。一个任务只评一个指标，结论只落到这一个指标上，避免混合任务互相稀释。
- **结构化输出必须可解析**。裁判返回非 JSON、缺 `reasoning`、标签越界、分数越界都会判为 `error`，而不是猜一个结论。
- **上游错误跳过下游指标**。路由误触发时，依赖检索证据的指标（忠实性、检索精确率/召回率）标记为 `skipped`，既不计入分子也不计入分母；其余指标照常判定。

逐条结论以既有断言的 `metric` 与 `skipped` 承载，因此运行记录格式不变，记分卡、门禁与控制台无需改动即可消费。
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

## Web 控制台

### 四个只读分区

界面只呈现既有产物，不提供任何产生副作用的入口：

| 分区 | 内容 |
| --- | --- |
| 运行 | 运行列表与详情：逐用例判定、失败判据的期望值与实际值、指标、任务通过率与 pass@k，展开可看工具调用序列 |
| 结论 | 门禁、失败归因、裁判校准三类结论记录及各自摘要 |
| 基线 | 名称、来源运行、用例数、状态分布与捕获时间 |
| 资产 | 金标集、证据轨迹、回流数据集三份只读清单 |

写操作的入口都在命令行：`run` / `task run` 触发评测，`gate` 判定门禁，`judge calibrate` 校准裁判，`trace import` 导入轨迹，`reflow run` 回流失败，`baseline` 捕获基线。后端仍保留这些写接口并沿用同一条回环边界，脚本与 CI 可以直接调用。

### 界面

```bash
python -m agenteval --home .agenteval serve --port 8787
```

```
agenteval console: http://127.0.0.1:8787
runs: 12  home: .agenteval
```

打开后是左侧导航加右侧内容区：运行分区是列表加详情，其余三个分区是整宽清单。

三条约束：**只读**，界面不写入、不删除、不修改任何产物；**默认只绑回环地址**，运行记录里可能含敏感参数与结果；**离线可用**，页面不引用任何外部资源，前端构建在开发期完成、产物随包分发，使用方不需要执行构建。

字段缺失时省略区块而不是填零——不同版本的运行记录字段不一定齐全，填零会让人误以为「真的没有成本」。

### 界面开发

前端在 `web/`，用 React + Vite：

```bash
cd web
npm install
npm run build      # 产物写入 src/agenteval/console/dist/，随包分发
```

构建产物提交进仓库，CI 会重新构建并比对差异，因此改完前端必须一并提交 `dist/`。

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

- 超时用线程执行器实现，无法强制中断阻塞中的原生调用；被测 agent 应提供可中断实现。
- **感知 / 规划 / 记忆 / RAG 的耗时归属属于 agent 内部信息**，平台观测不到，必须由 agent 上报；未上报的指标不出现在报告里，也不补零。
- agent 自带工具时，工具调用耗时只能由 agent 上报（平台不代执行工具）。
- token 用量依赖 agent 上报，覆盖不全时只汇总已上报部分；已有 token 预算，但尚未做成本货币化换算。
- Mock 模式由平台声明模式并提供 Mock 数据，由拥有工具的 agent 自己兑现，平台不注入工具返回值。
- 评测集内容需要业务方按规范填充：平台提供装配机制与示例，不含成规模的行业评测集。
- 多轮对话只带确定性脚本模拟器，不实现 LLM 模拟用户、多分支与回溯；并假设 agent 在单轮内完成其工具调用，跨轮保留的异步调用归属会失准。
- 裁判校准只覆盖成对偏好型裁判；逐点评分型没有「位置」概念，偏置检测不适用。
- 审计导入只覆盖工具调用边界；状态传递判据要求源数据保留原始返回值，仅有结果摘要时无法评估。
- Agent Bridge 只做单机直连，覆盖 HTTP 与本地子进程两种传输；不含分布式调度、容器编排与多语言 SDK。子进程传输每次调用启动一个进程，适合无状态 agent。
- 沙箱以 compose 项目为单位，只做数据卷级快照，不做容器进程级快照（CRIU）；同一沙箱同一时间只允许一轮运行，并发调度不在范围内。
- 脱敏只覆盖顶层字段，嵌套结构内的敏感值需在 agent 侧处理。

## 规格来源

本项目的设计基线在 `openspec/`：`specs/` 是已归档的能力规格，`changes/archive/` 保留每次变更的 proposal、design 与 tasks。

```bash
openspec list --specs                    # 能力清单
openspec show tool-contract-eval --type spec   # 某一条能力规格
openspec validate --all --strict         # 全量校验
```
