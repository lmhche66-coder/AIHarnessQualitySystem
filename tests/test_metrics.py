from __future__ import annotations

from pathlib import Path

from agenteval.metrics import PERCENTILE_METHOD, percentile, summarize_metrics
from agenteval.models import (
    CaseMetrics,
    ProcessCase,
    Status,
    TokenUsage,
    Trace,
    TraceEvent,
    Verdict,
)
from agenteval.process import (
    check_process_case,
    count_repeated_calls,
    extract_tool_calls,
    measure_calls,
    record_tool_call,
    trace_duration_ms,
)
from agenteval.runner import ContractRunner
from agenteval.store import RunStore
from agenteval.tools import ToolResult


def trace_with(calls: list[tuple[str, dict, ToolResult]]) -> Trace:
    trace = Trace(case_id="p1")
    for target, args, result in calls:
        record_tool_call(trace, target, args, result)
    return trace


def budget_case(**limits: object) -> ProcessCase:
    return ProcessCase.model_validate(
        {"id": "p1", "checks": [{"kind": "budget", **limits}]}
    )


def test_measure_counts_calls() -> None:
    trace = trace_with(
        [
            ("a", {"x": 1}, ToolResult(ok=True)),
            ("b", {"x": 1}, ToolResult(ok=True)),
            ("a", {"x": 1}, ToolResult(ok=True)),
        ]
    )
    assert measure_calls(trace).calls == 3


def test_repeated_calls_counted_by_identical_target_and_args() -> None:
    trace = trace_with([("a", {"x": 1}, ToolResult(ok=True))] * 3)
    assert count_repeated_calls(extract_tool_calls(trace)) == 2


def test_different_args_are_not_repeats() -> None:
    trace = trace_with(
        [
            ("a", {"x": 1}, ToolResult(ok=True)),
            ("a", {"x": 2}, ToolResult(ok=True)),
            ("b", {"x": 1}, ToolResult(ok=True)),
        ]
    )
    assert count_repeated_calls(extract_tool_calls(trace)) == 0


def test_trace_duration_uses_first_and_last_event() -> None:
    trace = Trace(case_id="p1")
    trace.events = [
        TraceEvent(seq=1, name="tool_call", at="2026-01-01T00:00:00Z"),
        TraceEvent(seq=2, name="tool_call", at="2026-01-01T00:00:01.500Z"),
    ]
    assert trace_duration_ms(trace) == 1500.0


def test_trace_duration_is_zero_for_short_traces() -> None:
    empty = Trace(case_id="p1")
    assert trace_duration_ms(empty) == 0.0
    single = Trace(case_id="p1")
    single.record("tool_call", payload={"target": "a"})
    assert trace_duration_ms(single) == 0.0


def test_token_usage_summed_when_reported() -> None:
    trace = trace_with(
        [
            ("a", {}, ToolResult(ok=True, usage=TokenUsage(input_tokens=10, output_tokens=4))),
            ("b", {}, ToolResult(ok=True, usage=TokenUsage(input_tokens=5, output_tokens=2))),
        ]
    )
    metrics = measure_calls(trace)
    assert (metrics.input_tokens, metrics.output_tokens) == (15, 6)
    assert metrics.usage_reported is True


def test_token_usage_absent_is_not_fabricated() -> None:
    metrics = measure_calls(trace_with([("a", {}, ToolResult(ok=True))]))
    assert (metrics.input_tokens, metrics.output_tokens) == (0, 0)
    assert metrics.usage_reported is False


def test_budget_passes_when_within_limits() -> None:
    trace = trace_with([("a", {"x": 1}, ToolResult(ok=True))])
    verdict = check_process_case(
        budget_case(max_calls=5, max_retries=2, max_duration_ms=10000), trace
    )
    assert verdict.status is Status.PASS


def test_budget_fails_and_reports_measured_value() -> None:
    trace = trace_with([("a", {"x": 1}, ToolResult(ok=True))] * 3)
    verdict = check_process_case(budget_case(max_retries=1), trace)
    assert verdict.status is Status.FAIL
    outcome = verdict.failed_checks[0]
    assert outcome.name == "budget.max_retries"
    assert outcome.actual == 2
    assert "exceeds the limit 1" in (outcome.message or "")


def test_budget_fails_on_exceeded_call_count() -> None:
    trace = trace_with([("a", {}, ToolResult(ok=True))] * 4)
    verdict = check_process_case(budget_case(max_calls=3), trace)
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "budget.max_calls"


def test_budget_fails_on_exceeded_duration() -> None:
    trace = Trace(case_id="p1")
    trace.events = [
        TraceEvent(seq=1, name="tool_call", at="2026-01-01T00:00:00Z"),
        TraceEvent(seq=2, name="tool_call", at="2026-01-01T00:00:05Z"),
    ]
    verdict = check_process_case(budget_case(max_duration_ms=1000), trace)
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "budget.max_duration_ms"


def test_budget_without_limits_fails() -> None:
    verdict = check_process_case(budget_case(), Trace(case_id="p1"))
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "budget.configured"


def test_undeclared_budget_limits_do_not_affect_the_conclusion() -> None:
    trace = trace_with([("a", {}, ToolResult(ok=True))] * 5)
    verdict = check_process_case(budget_case(max_duration_ms=10000), trace)
    assert verdict.status is Status.PASS
    assert [check.name for check in verdict.checks] == ["budget.max_duration_ms"]


def test_percentile_uses_nearest_rank() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(values, 0.5) == 30.0
    assert percentile(values, 0.95) == 50.0
    assert percentile([], 0.5) == 0.0


def test_summarize_metrics_aggregates_and_lists_violations() -> None:
    verdicts = [
        Verdict(
            case_id="a",
            status=Status.PASS,
            metrics=CaseMetrics(calls=2, retries=1, duration_ms=10.0, input_tokens=3, output_tokens=1, usage_reported=True),
        ),
        Verdict(
            case_id="b",
            status=Status.FAIL,
            metrics=CaseMetrics(calls=4, retries=2, duration_ms=50.0),
        ),
    ]
    from agenteval.models import CheckOutcome

    verdicts[1].checks = [CheckOutcome(name="budget.max_calls", passed=False)]
    summary = summarize_metrics(verdicts)
    assert summary.cases == 2
    assert (summary.calls, summary.retries) == (6, 3)
    assert summary.duration_p50_ms == 10.0
    assert summary.duration_p95_ms == 50.0
    assert (summary.input_tokens, summary.output_tokens) == (3, 1)
    assert summary.usage_reported is True
    assert summary.budget_violations == ["b:budget.max_calls"]
    assert summary.percentile_method == PERCENTILE_METHOD


def test_verdict_without_metrics_still_validates() -> None:
    verdict = Verdict.model_validate({"case_id": "a", "status": "pass"})
    assert verdict.metrics.calls == 0
    assert verdict.metrics.usage_reported is False


def test_functionally_passing_case_can_still_fail_a_budget(tmp_path: Path) -> None:
    """功能判据全绿，但重复调用超限时仍然判失败。"""

    case = ProcessCase.model_validate(
        {
            "id": "p1",
            "steps": [{"target": "echo_tool", "input": {"message": "hi"}}] * 3,
            "checks": [
                {"kind": "no_extra_calls", "allowed": ["echo_tool"]},
                {"kind": "budget", "max_retries": 1},
            ],
        }
    )
    from agenteval.fakes import build_demo_registry

    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    run = runner.run([case], run_id="budget-run")
    verdict = run.verdicts[0]
    assert verdict.status is Status.FAIL
    assert [check.name for check in verdict.failed_checks] == ["budget.max_retries"]
    assert verdict.metrics.calls == 3
    assert verdict.metrics.retries == 2
    assert run.metadata["metrics"]["budget_violations"] == ["p1:budget.max_retries"]
