from __future__ import annotations

import pytest
from pydantic import ValidationError

from agenteval.models import Case, CheckOutcome, Run, RunSummary, Status, Trace, Verdict


def make_case(**overrides: object) -> Case:
    payload = {
        "id": "case-1",
        "target": "echo_tool",
        "input": {"message": "hi"},
        "check": {"kind": "missing_required", "drop": ["message"]},
    }
    payload.update(overrides)
    return Case.model_validate(payload)


def test_case_parses_discriminated_check() -> None:
    case = make_case()
    assert case.kind == "tool_contract"
    assert case.check.kind == "missing_required"


def test_case_rejects_unknown_check_kind() -> None:
    with pytest.raises(ValidationError):
        make_case(check={"kind": "no_such_check"})


def test_case_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        make_case(unexpected="value")


def test_case_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        make_case(id="")


def test_run_refresh_summary_counts_each_status() -> None:
    run = Run(run_id="run-1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [
        Verdict(case_id="a", status=Status.PASS),
        Verdict(case_id="b", status=Status.FAIL),
        Verdict(case_id="c", status=Status.FAIL),
        Verdict(case_id="d", status=Status.ERROR),
    ]
    summary = run.refresh_summary()
    assert summary == RunSummary(total=4, passed=1, failed=2, errored=1)
    assert run.summary.total == 4


def test_verdict_failed_checks_filters_passed() -> None:
    verdict = Verdict(
        case_id="a",
        status=Status.FAIL,
        checks=[
            CheckOutcome(name="ok", passed=True),
            CheckOutcome(name="bad", passed=False, expected=1, actual=2),
        ],
    )
    assert [check.name for check in verdict.failed_checks] == ["bad"]


def test_trace_records_monotonic_sequence() -> None:
    trace = Trace(case_id="a")
    first = trace.record("one")
    second = trace.record("two", payload={"x": 1})
    assert (first.seq, second.seq) == (1, 2)
    assert second.payload == {"x": 1}


def test_run_serialization_round_trip() -> None:
    run = Run(run_id="run-1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [Verdict(case_id="a", status=Status.PASS)]
    run.traces = [Trace(case_id="a", attempts=2, side_effects=["k"])]
    run.refresh_summary()
    restored = Run.model_validate_json(run.model_dump_json())
    assert restored == run
