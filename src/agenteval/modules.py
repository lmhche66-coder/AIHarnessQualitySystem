"""模块级信号：把感知、规划、记忆、工具四个模块的执行信息写进轨迹。

对照文章的 EvalTrace：Agent 的每个节点在完成自己的工作后把关键信息写进一条轻量
轨迹，平台据此做模块级断言与诊断，而不是从最终回复文本里反推。信号以
``module.<name>`` 事件落在既有 ``Trace`` 上，因此对运行记录格式零侵入，历史轨迹
与断言不受影响。

信号来源有两处：被测 agent 通过 bridge 上报，或平台侧的工具/适配器在用例执行期
直接写入当前轨迹。payload 约定见各 ``record_*`` 函数的文档。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import Trace

EVENT_PREFIX = "module."


class ModuleName(str, Enum):
    """四个核心模块；检索（RAG）作为记忆模块的数据来源单列一条信号。"""

    PERCEPTION = "perception"
    PLANNING = "planning"
    MEMORY = "memory"
    TOOL = "tool"
    RETRIEVAL = "retrieval"


# 指标 → 模块归口，用于把记分卡按模块聚合。口径取自文章第 3.2–3.5 节。
METRIC_MODULE: dict[str, ModuleName] = {
    # 感知
    "intent_accuracy": ModuleName.PERCEPTION,
    "intent_recall": ModuleName.PERCEPTION,
    "intent_precision": ModuleName.PERCEPTION,
    "multi_intent_recognition": ModuleName.PERCEPTION,
    "ambiguous_clarification": ModuleName.PERCEPTION,
    "fallback_accuracy": ModuleName.PERCEPTION,
    # 规划
    "route_accuracy": ModuleName.PLANNING,
    "tool_decision_accuracy": ModuleName.PLANNING,
    "retrieval_decision_accuracy": ModuleName.PLANNING,
    "planning_path_score": ModuleName.PLANNING,
    # 记忆（含检索）
    "short_term_memory_retention": ModuleName.MEMORY,
    "long_term_retrieval_precision": ModuleName.MEMORY,
    "long_term_retrieval_recall": ModuleName.MEMORY,
    "memory_decay_curve": ModuleName.MEMORY,
    # 工具
    "tool_load_success": ModuleName.TOOL,
    "tool_call_accuracy": ModuleName.TOOL,
    "tool_call_success": ModuleName.TOOL,
    "param_mapping_accuracy": ModuleName.TOOL,
}


def event_name(module: ModuleName | str) -> str:
    """模块信号在轨迹中的事件名。"""

    return f"{EVENT_PREFIX}{ModuleName(module).value}"


class ModuleSignal(BaseModel):
    """一条模块级信号；bridge 协议与轨迹共用同一结构。"""

    model_config = ConfigDict(extra="forbid")

    module: ModuleName
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float | None = None


def record_signal(
    trace: Trace,
    module: ModuleName | str,
    payload: dict[str, Any] | None = None,
    duration_ms: float | None = None,
) -> None:
    """把一条模块信号写入轨迹。"""

    body = dict(payload or {})
    if duration_ms is not None:
        body.setdefault("duration_ms", duration_ms)
    trace.record(event_name(module), payload=body)


def record_perception(
    trace: Trace,
    *,
    intent: str | None = None,
    skill: str | None = None,
    hit: bool | None = None,
    duration_ms: float | None = None,
) -> None:
    """感知节点信号：识别的意图、命中的 Skill、是否命中与耗时。"""

    payload: dict[str, Any] = {}
    if intent is not None:
        payload["intent"] = intent
    if skill is not None:
        payload["skill"] = skill
    if hit is not None:
        payload["hit"] = hit
    record_signal(trace, ModuleName.PERCEPTION, payload, duration_ms)


def record_planning(
    trace: Trace,
    *,
    route: str | None = None,
    tools: list[str] | None = None,
    duration_ms: float | None = None,
) -> None:
    """规划节点信号：路由方向（skill_hit / skill_miss）、选中的工具与耗时。"""

    payload: dict[str, Any] = {}
    if route is not None:
        payload["route"] = route
    if tools is not None:
        payload["tools"] = list(tools)
    record_signal(trace, ModuleName.PLANNING, payload, duration_ms)


def record_memory(
    trace: Trace,
    *,
    turns: int | None = None,
    injected: list[str] | None = None,
    duration_ms: float | None = None,
) -> None:
    """记忆节点信号：会话历史轮数、注入的关键信息与耗时。"""

    payload: dict[str, Any] = {}
    if turns is not None:
        payload["turns"] = turns
    if injected is not None:
        payload["injected"] = list(injected)
    record_signal(trace, ModuleName.MEMORY, payload, duration_ms)


def record_retrieval(
    trace: Trace,
    *,
    chunks: list[dict[str, Any]] | None = None,
    count: int | None = None,
    duration_ms: float | None = None,
) -> None:
    """检索（RAG）信号：检索耗时、结果数量与原始文本块（文章 §6.3）。

    ``count`` 未显式给出时按 ``chunks`` 长度推导；两者都没有就不写，不臆造。
    """

    payload: dict[str, Any] = {}
    if chunks is not None:
        payload["chunks"] = list(chunks)
        payload["count"] = len(chunks) if count is None else count
    elif count is not None:
        payload["count"] = count
    record_signal(trace, ModuleName.RETRIEVAL, payload, duration_ms)


USAGE_EVENT = "model.usage"


def record_usage(
    trace: Trace,
    *,
    model_calls: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """把模型调用次数与输入/输出 Token 写进轨迹（文章 §6.3「其他信息统计」）。"""

    payload: dict[str, Any] = {}
    if model_calls is not None:
        payload["model_calls"] = model_calls
    if input_tokens is not None:
        payload["input_tokens"] = input_tokens
    if output_tokens is not None:
        payload["output_tokens"] = output_tokens
    trace.record(USAGE_EVENT, payload=payload)


def extract_signals(trace: Trace, module: ModuleName | str | None = None) -> list[ModuleSignal]:
    """按发生顺序取出轨迹中的模块信号，可按模块过滤。"""

    wanted = ModuleName(module) if module is not None else None
    signals: list[ModuleSignal] = []
    for event in trace.events:
        if not event.name.startswith(EVENT_PREFIX):
            continue
        try:
            name = ModuleName(event.name[len(EVENT_PREFIX) :])
        except ValueError:
            continue
        if wanted is not None and name is not wanted:
            continue
        duration = event.payload.get("duration_ms")
        signals.append(
            ModuleSignal(
                module=name,
                payload=dict(event.payload),
                duration_ms=duration if isinstance(duration, (int, float)) else None,
            )
        )
    return signals


def latest_signal(trace: Trace, module: ModuleName | str) -> ModuleSignal | None:
    """取某模块最近一条信号；没有则返回 ``None``。"""

    signals = extract_signals(trace, module)
    return signals[-1] if signals else None


def module_of_metric(metric: str) -> ModuleName | None:
    """返回指标归口的模块；未登记时返回 ``None``。"""

    return METRIC_MODULE.get(metric)


def signal_at() -> datetime:
    """统一的时间戳来源，便于测试替换。"""

    return datetime.now(timezone.utc)
