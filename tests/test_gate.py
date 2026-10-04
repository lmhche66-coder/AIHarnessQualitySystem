from __future__ import annotations

import json

from agenteval.gate import Baseline, GateThresholds, evaluate, format_report
from agenteval.models import Run, Status, Verdict


def make_run(run_id: str, statuses: dict[str, Status]) -> Run:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z")
    run.verdicts = [Verdict(case_id=case_id, status=status) for case_id, status in statuses.items()]
    run.refresh_summary()
    return run


def test_default_thresholds_pass_a_clean_run() -> None:
    result = evaluate(make_run("r1", {"a": Status.PASS}))
    assert result.passed is True
    assert result.pass_rate == 1.0
    assert result.reasons == []


def test_pass_rate_below_minimum_fails() -> None:
    result = evaluate(
        make_run("r1", {"a": Status.PASS, "b": Status.FAIL}),
        thresholds=GateThresholds(min_pass_rate=0.9),
    )
    assert result.passed is False
    assert any("pass rate" in reason for reason in result.reasons)


def test_error_count_above_maximum_fails() -> None:
    result = evaluate(
        make_run("r1", {"a": Status.PASS, "b": Status.ERROR}),
        thresholds=GateThresholds(max_errors=0),
    )
    assert result.passed is False
    assert any("error count" in reason for reason in result.reasons)


def test_empty_run_fails_rather_than_passing() -> None:
    result = evaluate(make_run("r1", {}))
    assert result.passed is False
    assert "run contains no verdicts" in result.reasons
    assert result.pass_rate == 0.0


def test_regression_is_detected_and_fails() -> None:
    baseline = Baseline.from_run(make_run("base", {"a": Status.PASS, "b": Status.PASS}))
    result = evaluate(make_run("cur", {"a": Status.PASS, "b": Status.FAIL}), baseline=baseline)
    assert result.passed is False
    assert result.regressions == ["b"]
    assert result.baseline_run_id == "base"
    assert any("regressed" in reason for reason in result.reasons)


def test_missing_case_is_detected_and_fails() -> None:
    baseline = Baseline.from_run(make_run("base", {"a": Status.PASS, "b": Status.PASS}))
    result = evaluate(make_run("cur", {"a": Status.PASS}), baseline=baseline)
    assert result.passed is False
    assert result.missing == ["b"]


def test_new_cases_are_reported_but_do_not_fail() -> None:
    baseline = Baseline.from_run(make_run("base", {"a": Status.PASS}))
    result = evaluate(make_run("cur", {"a": Status.PASS, "b": Status.PASS}), baseline=baseline)
    assert result.passed is True
    assert result.new_cases == ["b"]


def test_without_baseline_only_thresholds_apply() -> None:
    result = evaluate(make_run("cur", {"a": Status.PASS}))
    assert result.passed is True
    assert result.baseline_run_id is None
    assert "baseline: none" in format_report(result)


def test_allow_regressions_keeps_them_visible() -> None:
    baseline = Baseline.from_run(make_run("base", {"a": Status.PASS}))
    result = evaluate(
        make_run("cur", {"a": Status.FAIL}),
        baseline=baseline,
        thresholds=GateThresholds(min_pass_rate=0.0),
        allow_regressions=True,
    )
    assert result.passed is True
    assert result.regressions == ["a"]
    report = format_report(result)
    assert "regressions (1): a" in report
    assert "gate: PASS" in report


def test_report_lists_reasons_and_is_machine_readable() -> None:
    result = evaluate(
        make_run("r1", {"a": Status.ERROR}),
        thresholds=GateThresholds(min_pass_rate=1.0, max_errors=0),
    )
    report = format_report(result)
    assert "gate: FAIL" in report
    assert "reasons:" in report
    assert "error count 1 exceeds the maximum 0" in report

    payload = json.loads(result.model_dump_json())
    assert payload["passed"] is False
    assert payload["errored"] == 1
    assert payload["thresholds"]["max_errors"] == 0
    assert payload["regressions"] == []
    assert payload["missing"] == []
