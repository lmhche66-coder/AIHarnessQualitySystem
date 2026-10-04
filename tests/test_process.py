from __future__ import annotations

from typing import Any

from agenteval.models import ProcessCase, Status, Trace
from agenteval.process import (
    TOOL_CALL_EVENT,
    TraceSession,
    TracingTool,
    check_process_case,
    extract_tool_calls,
    record_tool_call,
    wrap_registry_for_trace,
)
from agenteval.tools import ToolErrorKind, ToolRegistry, ToolResult


def make_case(checks: list[dict[str, Any]]) -> ProcessCase:
    return ProcessCase.model_validate({"id": "p", "checks": checks})


def trace_with(calls: list[tuple[str, dict[str, Any], bool, Any]]) -> Trace:
    trace = Trace(case_id="p")
    for target, args, ok, value in calls:
        result = ToolResult(
            ok=ok,
            value=value,
            error_kind=None if ok else ToolErrorKind.UPSTREAM,
        )
        record_tool_call(trace, target, args, result)
    return trace


def verdict_for(checks: list[dict[str, Any]], calls: list[tuple[str, dict[str, Any], bool, Any]]) -> Any:
    return check_process_case(make_case(checks), trace_with(calls))


class StubTool:
    def __init__(self, name: str = "stub", value: Any = None) -> None:
        self.name = name
        self.input_schema = {"type": "object"}
        self.value = value
        self.calls = 0

    def invoke(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, value=self.value)


def test_extract_tool_calls_is_ordered_and_indexed() -> None:
    calls = extract_tool_calls(
        trace_with([("a", {"x": 1}, True, None), ("b", {}, False, None)])
    )
    assert [call.index for call in calls] == [0, 1]
    assert [call.target for call in calls] == ["a", "b"]
    assert calls[0].args == {"x": 1}
    assert calls[1].ok is False
    assert calls[1].error_kind == "upstream"


def test_extract_tool_calls_ignores_other_events() -> None:
    trace = Trace(case_id="p")
    trace.record("cassette_hit", payload={"target": "a"})
    assert extract_tool_calls(trace) == []


def test_tracing_tool_records_call_into_bound_trace() -> None:
    session = TraceSession()
    tool = StubTool(value={"done": True})
    wrapped = TracingTool(tool, session)
    trace = Trace(case_id="p")
    session.begin_case(trace)
    result = wrapped.invoke(a=1)
    session.end_case()

    assert result.ok is True
    assert [event.name for event in trace.events] == [TOOL_CALL_EVENT]
    calls = extract_tool_calls(trace)
    assert calls[0].target == "stub"
    assert calls[0].value == {"done": True}


def test_tracing_tool_without_bound_trace_still_works() -> None:
    wrapped = TracingTool(StubTool(), TraceSession())
    assert wrapped.invoke().ok is True


def test_wrap_registry_for_trace_preserves_names() -> None:
    registry = ToolRegistry([StubTool("alpha"), StubTool("beta")])
    wrapped = wrap_registry_for_trace(registry, TraceSession())
    assert wrapped.names() == ["alpha", "beta"]


def test_tool_sequence_subsequence_allows_extra_calls() -> None:
    verdict = verdict_for(
        [{"kind": "tool_sequence", "expected": ["a", "c"]}],
        [("a", {}, True, None), ("b", {}, True, None), ("c", {}, True, None)],
    )
    assert verdict.status is Status.PASS


def test_tool_sequence_exact_rejects_extra_calls() -> None:
    verdict = verdict_for(
        [{"kind": "tool_sequence", "expected": ["a", "c"], "mode": "exact"}],
        [("a", {}, True, None), ("b", {}, True, None), ("c", {}, True, None)],
    )
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "tool_sequence.exact"


def test_tool_sequence_detects_wrong_order() -> None:
    verdict = verdict_for(
        [{"kind": "tool_sequence", "expected": ["a", "b"]}],
        [("b", {}, True, None), ("a", {}, True, None)],
    )
    assert verdict.status is Status.FAIL


def test_no_extra_calls_fails_and_lists_offenders() -> None:
    verdict = verdict_for(
        [{"kind": "no_extra_calls", "allowed": ["a"]}],
        [("a", {}, True, None), ("b", {}, True, None)],
    )
    assert verdict.status is Status.FAIL
    outcome = verdict.failed_checks[0]
    assert outcome.actual == ["b"]
    assert "unexpected tool calls: b" == outcome.message


def test_no_extra_calls_passes_when_only_allowed_tools_used() -> None:
    verdict = verdict_for(
        [{"kind": "no_extra_calls", "allowed": ["a", "b"]}],
        [("a", {}, True, None), ("b", {}, True, None)],
    )
    assert verdict.status is Status.PASS


def test_recovery_passes_when_failure_is_followed_by_success() -> None:
    verdict = verdict_for(
        [{"kind": "recovery", "failed_tool": "a", "recovered_by": "a"}],
        [("a", {}, False, None), ("a", {}, False, None), ("a", {}, True, None)],
    )
    assert verdict.status is Status.PASS
    assert verdict.checks[0].actual["recovered_index"] == 2


def test_recovery_honours_recovered_by_filter() -> None:
    verdict = verdict_for(
        [{"kind": "recovery", "failed_tool": "a", "recovered_by": "c"}],
        [("a", {}, False, None), ("b", {}, True, None)],
    )
    assert verdict.status is Status.FAIL


def test_recovery_fails_when_failure_is_never_recovered() -> None:
    verdict = verdict_for(
        [{"kind": "recovery", "failed_tool": "a"}],
        [("a", {}, False, None), ("b", {}, False, None)],
    )
    assert verdict.status is Status.FAIL
    assert "no successful call followed" in (verdict.failed_checks[0].message or "")


def test_recovery_fails_when_failure_never_happened() -> None:
    verdict = verdict_for(
        [{"kind": "recovery", "failed_tool": "a"}],
        [("a", {}, True, None)],
    )
    assert verdict.status is Status.FAIL
    assert "never exercised" in (verdict.failed_checks[0].message or "")


def test_state_continuity_passes_when_value_is_carried_forward() -> None:
    verdict = verdict_for(
        [{"kind": "state_continuity", "producer": "create", "consumer": "pay", "producer_field": "id"}],
        [("create", {}, True, {"id": "o-1"}), ("pay", {"order_id": "o-1"}, True, None)],
    )
    assert verdict.status is Status.PASS


def test_state_continuity_passes_for_nested_arguments() -> None:
    verdict = verdict_for(
        [{"kind": "state_continuity", "producer": "create", "consumer": "pay"}],
        [("create", {}, True, {"id": "o-9"}), ("pay", {"payload": {"refs": ["o-9"]}}, True, None)],
    )
    assert verdict.status is Status.PASS


def test_state_continuity_fails_when_state_is_lost() -> None:
    verdict = verdict_for(
        [{"kind": "state_continuity", "producer": "create", "consumer": "pay", "producer_field": "id"}],
        [("create", {}, True, {"id": "o-1"}), ("pay", {"order_id": "o-2"}, True, None)],
    )
    assert verdict.status is Status.FAIL
    assert "was not carried" in (verdict.failed_checks[0].message or "")


def test_state_continuity_fails_without_producer() -> None:
    verdict = verdict_for(
        [{"kind": "state_continuity", "producer": "create", "consumer": "pay"}],
        [("pay", {"order_id": "o-1"}, True, None)],
    )
    assert verdict.status is Status.FAIL
    assert "no successful producer call" in (verdict.failed_checks[0].message or "")


def test_process_case_without_checks_fails() -> None:
    verdict = check_process_case(make_case([]), Trace(case_id="p"))
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "process.checks_configured"
