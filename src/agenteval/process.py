"""轨迹级过程断言。

断言只依赖归一化后的工具调用序列，不直接读原始事件，因此事件命名的演进不会
波及断言本身。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import (
    CheckOutcome,
    NoExtraCallsCheck,
    ProcessCase,
    RecoveryCheck,
    StateContinuityCheck,
    Status,
    ToolSequenceCheck,
    Trace,
    Verdict,
)
from agenteval.tools import Tool, ToolErrorKind, ToolRegistry, ToolResult

TOOL_CALL_EVENT = "tool_call"


class ToolCall(BaseModel):
    """归一化后的一次工具调用。"""

    model_config = ConfigDict(extra="forbid")

    index: int
    target: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    error_kind: str | None = None
    attempts: int = 1
    value: Any = None


def record_tool_call(
    trace: Trace,
    target: str,
    args: Mapping[str, Any],
    result: ToolResult,
) -> None:
    """把一次工具调用记入轨迹。"""

    error_kind = (
        result.error_kind.value
        if isinstance(result.error_kind, ToolErrorKind)
        else result.error_kind
    )
    trace.record(
        TOOL_CALL_EVENT,
        payload={
            "target": target,
            "args": dict(args),
            "ok": result.ok,
            "error_kind": error_kind,
            "attempts": result.attempts,
            "value": result.value,
        },
    )


def extract_tool_calls(trace: Trace) -> list[ToolCall]:
    """按发生顺序把轨迹中的工具调用归一化为结构化序列。"""

    calls: list[ToolCall] = []
    for event in trace.events:
        if event.name != TOOL_CALL_EVENT:
            continue
        payload = event.payload
        calls.append(
            ToolCall(
                index=len(calls),
                target=str(payload.get("target", "")),
                args=dict(payload.get("args") or {}),
                ok=bool(payload.get("ok")),
                error_kind=payload.get("error_kind"),
                attempts=int(payload.get("attempts") or 1),
                value=payload.get("value"),
            )
        )
    return calls


def process_handler(kind: str) -> Callable[[Any, list[ToolCall]], list[CheckOutcome]] | None:
    """按检查类型取出过程断言处理器，供任务层复用。"""

    return _HANDLERS.get(kind)


@dataclass
class TraceSession:
    """把当前用例的轨迹暴露给工具包装器。"""

    _trace: Trace | None = field(default=None, repr=False)

    def begin_case(self, trace: Trace) -> None:
        self._trace = trace

    def end_case(self) -> None:
        self._trace = None

    def record_call(self, target: str, args: Mapping[str, Any], result: ToolResult) -> None:
        if self._trace is not None:
            record_tool_call(self._trace, target, args, result)


class TracingTool:
    """把工具调用记录到轨迹中的包装器，对上层完全透明。"""

    def __init__(self, tool: Tool, session: TraceSession) -> None:
        self._tool = tool
        self._session = session
        self.name = tool.name
        self.input_schema = tool.input_schema

    def invoke(self, **kwargs: Any) -> ToolResult:
        result = self._tool.invoke(**kwargs)
        self._session.record_call(self.name, kwargs, result)
        return result


def wrap_registry_for_trace(registry: ToolRegistry, session: TraceSession) -> ToolRegistry:
    """包装注册表中的全部工具，使其调用进入轨迹。"""

    wrapped = ToolRegistry()
    for name in registry.names():
        wrapped.register(TracingTool(registry.get(name), session))
    return wrapped


def check_process_case(case: ProcessCase, trace: Trace) -> Verdict:
    """对一条过程用例的轨迹执行全部过程断言。"""

    if not case.checks:
        return Verdict(
            case_id=case.id,
            status=Status.FAIL,
            checks=[
                CheckOutcome(
                    name="process.checks_configured",
                    passed=False,
                    expected="at least one check",
                    actual=0,
                    message="process case declares no checks",
                )
            ],
        )
    calls = extract_tool_calls(trace)
    outcomes: list[CheckOutcome] = []
    for spec in case.checks:
        handler = _HANDLERS.get(spec.kind)
        if handler is None:
            outcomes.append(
                CheckOutcome(
                    name=f"{spec.kind}.unsupported",
                    passed=False,
                    expected="a supported process check",
                    actual=spec.kind,
                    message="unsupported process check",
                )
            )
            continue
        outcomes.extend(handler(spec, calls))
    status = Status.PASS if outcomes and all(item.passed for item in outcomes) else Status.FAIL
    return Verdict(case_id=case.id, status=status, checks=outcomes)


def _check_tool_sequence(spec: Any, calls: list[ToolCall]) -> list[CheckOutcome]:
    check: ToolSequenceCheck = spec
    if not check.expected:
        return [
            CheckOutcome(
                name="tool_sequence.configured",
                passed=False,
                expected="a non-empty expected list",
                actual=check.expected,
                message="case does not declare an expected tool sequence",
            )
        ]
    observed = [call.target for call in calls]
    if check.mode == "exact":
        passed = observed == check.expected
    else:
        passed = _is_subsequence(check.expected, observed)
    return [
        CheckOutcome(
            name=f"tool_sequence.{check.mode}",
            passed=passed,
            expected=check.expected,
            actual=observed,
        )
    ]


def _check_no_extra_calls(spec: Any, calls: list[ToolCall]) -> list[CheckOutcome]:
    check: NoExtraCallsCheck = spec
    if not check.allowed:
        return [
            CheckOutcome(
                name="no_extra_calls.configured",
                passed=False,
                expected="a non-empty allowed list",
                actual=check.allowed,
                message="case does not declare which tools are allowed",
            )
        ]
    allowed = set(check.allowed)
    extras = [call.target for call in calls if call.target not in allowed]
    return [
        CheckOutcome(
            name="no_extra_calls",
            passed=not extras,
            expected=sorted(allowed),
            actual=extras,
            message=None if not extras else f"unexpected tool calls: {', '.join(extras)}",
        )
    ]


def _check_recovery(spec: Any, calls: list[ToolCall]) -> list[CheckOutcome]:
    check: RecoveryCheck = spec
    if not check.failed_tool:
        return [
            CheckOutcome(
                name="recovery.configured",
                passed=False,
                expected="a non-empty failed_tool",
                actual=check.failed_tool,
                message="case does not declare which tool is expected to fail",
            )
        ]
    for index, call in enumerate(calls):
        if call.target != check.failed_tool or call.ok:
            continue
        for later in calls[index + 1 :]:
            if check.recovered_by is not None and later.target != check.recovered_by:
                continue
            if later.ok:
                return [
                    CheckOutcome(
                        name="recovery",
                        passed=True,
                        expected=f"{check.failed_tool} failure followed by a successful call",
                        actual={
                            "failed_index": index,
                            "recovered_index": later.index,
                            "recovered_by": later.target,
                        },
                    )
                ]
        return [
            CheckOutcome(
                name="recovery",
                passed=False,
                expected="a successful call after the failure",
                actual=[call.target for call in calls[index + 1 :]],
                message="no successful call followed the failure",
            )
        ]
    return [
        CheckOutcome(
            name="recovery",
            passed=False,
            expected=f"a failing call to {check.failed_tool}",
            actual=[call.target for call in calls],
            message="no failing call was observed; the recovery path was never exercised",
        )
    ]


def _check_state_continuity(spec: Any, calls: list[ToolCall]) -> list[CheckOutcome]:
    check: StateContinuityCheck = spec
    if not check.producer or not check.consumer:
        return [
            CheckOutcome(
                name="state_continuity.configured",
                passed=False,
                expected="non-empty producer and consumer",
                actual={"producer": check.producer, "consumer": check.consumer},
                message="case does not declare both producer and consumer",
            )
        ]
    for index, producer in enumerate(calls):
        if producer.target != check.producer or not producer.ok:
            continue
        followers = [call for call in calls[index + 1 :] if call.target == check.consumer]
        if not followers:
            continue
        candidates = _produced_values(producer.value, check.producer_field)
        for consumer in followers:
            matched = [value for value in candidates if _contains(value, consumer.args)]
            if matched:
                return [
                    CheckOutcome(
                        name="state_continuity",
                        passed=True,
                        expected=f"a value produced by {check.producer} appears in {check.consumer} arguments",
                        actual={"matched": matched, "consumer_index": consumer.index},
                    )
                ]
        return [
            CheckOutcome(
                name="state_continuity",
                passed=False,
                expected=f"one of {candidates!r} appears in {check.consumer} arguments",
                actual={"produced": candidates, "consumer_args": followers[0].args},
                message="produced state was not carried into the consumer call",
            )
        ]
    has_producer = any(call.target == check.producer and call.ok for call in calls)
    return [
        CheckOutcome(
            name="state_continuity",
            passed=False,
            expected=f"a successful {check.producer} call followed by a {check.consumer} call",
            actual=[call.target for call in calls],
            message=(
                "no consumer call followed the producer"
                if has_producer
                else "no successful producer call was observed"
            ),
        )
    ]


def _produced_values(value: Any, field_name: str | None) -> list[Any]:
    if field_name is not None:
        return [value.get(field_name)] if isinstance(value, dict) else []
    if isinstance(value, dict):
        return list(value.values())
    return [value]


def _contains(candidate: Any, haystack: Any) -> bool:
    if candidate is None:
        return False
    if isinstance(haystack, Mapping):
        return any(_contains(candidate, item) for item in haystack.values())
    if isinstance(haystack, (list, tuple, set)):
        return any(_contains(candidate, item) for item in haystack)
    return candidate == haystack


def _is_subsequence(expected: Sequence[str], observed: Sequence[str]) -> bool:
    iterator = iter(observed)
    return all(item in iterator for item in expected)


_HANDLERS: dict[str, Callable[[Any, list[ToolCall]], list[CheckOutcome]]] = {
    "tool_sequence": _check_tool_sequence,
    "no_extra_calls": _check_no_extra_calls,
    "recovery": _check_recovery,
    "state_continuity": _check_state_continuity,
}
