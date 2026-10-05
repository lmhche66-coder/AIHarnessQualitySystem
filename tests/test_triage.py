from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.models import CheckOutcome, ProcessCase, Run, Status, TaskCase, Trace, Verdict
from agenteval.process import check_process_case, record_tool_call
from agenteval.tools import ToolResult
from agenteval.trace_store import TraceStore
from agenteval.triage import (
    build_candidate,
    classify,
    format_report,
    trace_checks,
    triage_run,
    verify_candidate,
)


def make_case(case_id: str = "p1", checks: list[dict[str, Any]] | None = None) -> ProcessCase:
    return ProcessCase.model_validate({"id": case_id, "checks": checks or []})


def make_task_case(case_id: str = "t1", checks: list[dict[str, Any]] | None = None) -> TaskCase:
    return TaskCase.model_validate({"id": case_id, "checks": checks or []})


def make_verdict(
    case_id: str = "p1",
    status: Status = Status.FAIL,
    checks: list[CheckOutcome] | None = None,
    error: str | None = None,
) -> Verdict:
    return Verdict(case_id=case_id, status=status, checks=checks or [], error=error)


def trace_with(case_id: str, calls: list[str]) -> Trace:
    trace = Trace(case_id=case_id)
    for target in calls:
        record_tool_call(trace, target, {}, ToolResult(ok=True))
    return trace


def failing_run(tmp_path: Path) -> tuple[Run, ProcessCase, Trace, TraceStore]:
    case = make_case("p1", [{"kind": "tool_sequence", "expected": ["b"], "mode": "exact"}])
    trace = trace_with("p1", ["a"])
    verdict = check_process_case(case, trace)
    run = Run(run_id="r1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [verdict]
    run.traces = [trace]
    run.refresh_summary()
    return run, case, trace, TraceStore(tmp_path / "traces")


def test_classify_rejects_passing_case() -> None:
    reflowable, reason = classify(make_verdict(status=Status.PASS), make_case(), has_trace=True)
    assert reflowable is False
    assert "nothing to reflow" in (reason or "")


def test_classify_rejects_error_verdict() -> None:
    reflowable, reason = classify(make_verdict(status=Status.ERROR), make_case(), has_trace=True)
    assert reflowable is False
    assert "error" in (reason or "")


def test_classify_rejects_missing_trace() -> None:
    reflowable, reason = classify(make_verdict(), make_case(), has_trace=False)
    assert reflowable is False
    assert "no trajectory" in (reason or "")


def test_classify_rejects_state_dependent_failure() -> None:
    verdict = make_verdict(checks=[CheckOutcome(name="final_state", passed=False, expected=1, actual=0)])
    reflowable, reason = classify(verdict, make_case(), has_trace=True)
    assert reflowable is False
    assert "environment state" in (reason or "")


def test_classify_rejects_case_without_trace_checks() -> None:
    verdict = make_verdict(checks=[CheckOutcome(name="tool_sequence.exact", passed=False)])
    case = make_task_case(checks=[{"kind": "final_state", "tool": "t", "field": "f", "expected": 1}])
    reflowable, reason = classify(verdict, case, has_trace=True)
    assert reflowable is False
    assert "no trajectory-based check" in (reason or "")


def test_classify_accepts_trace_reproducible_failure() -> None:
    verdict = make_verdict(checks=[CheckOutcome(name="tool_sequence.exact", passed=False)])
    case = make_case(checks=[{"kind": "tool_sequence", "expected": ["b"]}])
    reflowable, reason = classify(verdict, case, has_trace=True)
    assert reflowable is True
    assert reason is None


def test_trace_checks_drops_state_dependent_specs() -> None:
    case = make_task_case(
        checks=[
            {"kind": "tool_sequence", "expected": ["a"]},
            {"kind": "final_state", "tool": "t", "field": "f", "expected": 1},
        ]
    )
    kinds = [spec.kind for spec in trace_checks(case)]
    assert kinds == ["tool_sequence"]


def test_build_candidate_binds_trace_and_keeps_trace_checks() -> None:
    case = make_case(checks=[{"kind": "no_extra_calls", "allowed": ["a"]}])
    candidate = build_candidate(case, "run-1", "repro-p1")
    assert candidate.id == "p1@repro"
    assert candidate.trace == "repro-p1"
    assert [spec.kind for spec in candidate.checks] == ["no_extra_calls"]


def test_verify_candidate_accepts_matching_failure() -> None:
    case = make_case(checks=[{"kind": "tool_sequence", "expected": ["b"], "mode": "exact"}])
    trace = trace_with("p1", ["a"])
    verdict = check_process_case(case, trace)
    candidate = build_candidate(case, "r1", "repro-p1")
    ok, detail = verify_candidate(candidate, verdict, trace)
    assert ok is True
    assert detail is None


def test_verify_candidate_rejects_non_reproducing_case() -> None:
    case = make_case(checks=[{"kind": "tool_sequence", "expected": ["b"], "mode": "exact"}])
    failing_trace = trace_with("p1", ["a"])
    verdict = check_process_case(case, failing_trace)
    verifiable = build_candidate(case, "r1", "repro-p1")
    ok, detail = verify_candidate(verifiable, verdict, trace_with("p1", ["b"]))
    assert ok is False
    assert "expected fail" in (detail or "")


def test_verify_candidate_rejects_different_failing_checks() -> None:
    case = make_case(checks=[{"kind": "tool_sequence", "expected": ["b"], "mode": "exact"}])
    original = make_verdict(checks=[CheckOutcome(name="no_extra_calls", passed=False)])
    ok, detail = verify_candidate(
        build_candidate(case, "r1", "repro-p1"), original, trace_with("p1", ["a"])
    )
    assert ok is False
    assert "failing checks differ" in (detail or "")


def test_triage_run_produces_verified_candidate(tmp_path: Path) -> None:
    run, case, _, store = failing_run(tmp_path)
    report, candidates = triage_run(run, [case], store)

    assert report.total_failures == 1
    assert report.reflowable == 1
    assert report.verified == 1
    detail = report.details[0]
    assert detail.trace_name == "repro-p1"
    assert detail.candidate_id == "p1@repro"
    assert store.exists("repro-p1") is True
    assert [candidate.id for candidate in candidates] == ["p1@repro"]
    assert candidates[0].trace == "repro-p1"


def test_triage_run_is_empty_when_everything_passes(tmp_path: Path) -> None:
    case = make_case("p1", [{"kind": "tool_sequence", "expected": ["a"], "mode": "exact"}])
    trace = trace_with("p1", ["a"])
    run = Run(run_id="r1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [check_process_case(case, trace)]
    run.traces = [trace]
    run.refresh_summary()

    report, candidates = triage_run(run, [case], TraceStore(tmp_path / "traces"))
    assert report.total_failures == 0
    assert candidates == []
    assert "no failures" in format_report(report)


def test_triage_run_reports_missing_case_definition(tmp_path: Path) -> None:
    run, _, _, store = failing_run(tmp_path)
    report, candidates = triage_run(run, [], store)
    assert candidates == []
    assert "not found" in (report.details[0].reason or "")


def test_triage_run_marks_state_dependent_failure(tmp_path: Path) -> None:
    verdict = make_verdict(
        "t1", checks=[CheckOutcome(name="final_state", passed=False, expected=70, actual=30)]
    )
    from agenteval.models import TaskCase

    case = TaskCase.model_validate(
        {
            "id": "t1",
            "checks": [{"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 70}],
        }
    )
    run = Run(run_id="r1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [verdict]
    run.traces = [trace_with("t1", ["ledger_tool"])]
    run.refresh_summary()

    report, candidates = triage_run(run, [case], TraceStore(tmp_path / "traces"))
    assert candidates == []
    assert report.reflowable == 0
    assert "environment state" in (report.details[0].reason or "")
