from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.models import (
    CheckOutcome,
    ProcessCase,
    Run,
    Status,
    Trace,
    Verdict,
)
from agenteval.reflow import (
    REFLOW_DIRNAME,
    ReflowReport,
    default_paths,
    fingerprint_for,
    reflow,
)
from agenteval.store import RunStore
from agenteval.trace_store import TraceStore
from agenteval.triage import build_candidate


def _case(case_id: str = "proc-1", expected: list[str] | None = None) -> ProcessCase:
    return ProcessCase.model_validate(
        {
            "id": case_id,
            "kind": "process",
            "checks": [
                {
                    "kind": "tool_sequence",
                    "expected": expected or ["ledger_tool"],
                    "mode": "subsequence",
                }
            ],
        }
    )


def _failing_verdict(case_id: str = "proc-1") -> Verdict:
    return Verdict(
        case_id=case_id,
        status=Status.FAIL,
        checks=[
            CheckOutcome(
                name="tool_sequence.subsequence",
                passed=False,
                expected=["ledger_tool"],
                actual=[],
            )
        ],
    )


def _run(run_id: str, case_id: str = "proc-1") -> Run:
    run = Run(run_id=run_id, started_at=datetime.now(timezone.utc))
    run.verdicts.append(_failing_verdict(case_id))
    run.traces.append(Trace(case_id=case_id))
    run.finished_at = datetime.now(timezone.utc)
    run.refresh_summary()
    return run


def _prepare(tmp_path: Path, case: ProcessCase) -> tuple[RunStore, TraceStore, Path]:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [case.model_dump(mode="json")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = RunStore(tmp_path / "runs")
    run = _run("run-1", case.id)
    run.metadata["cases_file"] = str(cases_path)
    store.save(run)
    return store, TraceStore(tmp_path / "traces"), tmp_path / "reflow"


# --------------------------------------------------------------------------- 指纹


def test_fingerprint_is_stable_and_discriminating() -> None:
    first = fingerprint_for("proc-1", _failing_verdict())
    assert first == fingerprint_for("proc-1", _failing_verdict())
    other = Verdict(
        case_id="proc-1",
        status=Status.FAIL,
        checks=[
            CheckOutcome(
                name="no_extra_calls",
                passed=False,
                expected=["ledger_tool"],
                actual=["echo_tool"],
            )
        ],
    )
    assert fingerprint_for("proc-1", other) != first
    assert fingerprint_for("proc-2", _failing_verdict()) != first


# --------------------------------------------------------------------------- 回流


def test_reflow_emits_a_verified_candidate(tmp_path: Path) -> None:
    store, traces, base = _prepare(tmp_path, _case())
    dataset, ledger = default_paths(base)
    report = reflow(store, traces, dataset, ledger)
    assert isinstance(report, ReflowReport)
    assert report.scanned_runs == 1
    assert report.scanned_failures == 1
    assert report.new_failures == 1
    assert report.emitted == 1
    assert report.entries[0].verified is True
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    assert len(payload["cases"]) == 1
    assert payload["cases"][0]["id"].startswith("proc-1@repro-")


def test_reflow_is_idempotent(tmp_path: Path) -> None:
    store, traces, base = _prepare(tmp_path, _case())
    dataset, ledger = default_paths(base)
    reflow(store, traces, dataset, ledger)
    second = reflow(store, traces, dataset, ledger)
    assert second.new_failures == 0
    assert second.duplicates == 1
    assert second.emitted == 0
    assert second.dataset_size == 1
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    assert len(payload["cases"]) == 1


def test_reflow_accumulates_across_runs(tmp_path: Path) -> None:
    case_one = _case("proc-1")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {"cases": [case_one.model_dump(mode="json")]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = RunStore(tmp_path / "runs")
    for run_id in ("run-1", "run-2"):
        run = _run(run_id)
        run.metadata["cases_file"] = str(cases_path)
        store.save(run)
    traces = TraceStore(tmp_path / "traces")
    dataset, ledger = default_paths(tmp_path / "reflow")
    report = reflow(store, traces, dataset, ledger)
    assert report.scanned_runs == 2
    assert report.emitted == 1
    assert report.duplicates == 1


def test_error_verdict_is_not_reflowable(tmp_path: Path) -> None:
    state_case = _case("proc-state")
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [state_case.model_dump(mode="json")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = RunStore(tmp_path / "runs")
    run = Run(run_id="run-1", started_at=datetime.now(timezone.utc))
    run.verdicts.append(
        Verdict(case_id="proc-state", status=Status.ERROR, error="agent raised: boom")
    )
    run.traces.append(Trace(case_id="proc-state"))
    run.metadata["cases_file"] = str(cases_path)
    run.refresh_summary()
    store.save(run)
    dataset, ledger = default_paths(tmp_path / "reflow")
    report = reflow(store, TraceStore(tmp_path / "traces"), dataset, ledger)
    assert report.non_reflowable == 1
    assert report.emitted == 0
    assert "error" in (report.entries[0].reason or "")


def test_run_without_cases_file_is_skipped(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(_run("run-1"))
    dataset, ledger = default_paths(tmp_path / "reflow")
    report = reflow(store, TraceStore(tmp_path / "traces"), dataset, ledger)
    assert report.scanned_runs == 1
    assert report.emitted == 0
    assert report.non_reflowable == 1
    assert "not found" in (report.entries[0].reason or "")


def test_candidate_suffix_keeps_ids_distinct() -> None:
    case = _case()
    first = build_candidate(case, "run-1", "trace-a", suffix="-aaaa")
    second = build_candidate(case, "run-1", "trace-b", suffix="-bbbb")
    assert first.id != second.id
    assert first.id.endswith("-aaaa")


def test_no_failures_keeps_the_dataset_untouched(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = Run(run_id="run-ok", started_at=datetime.now(timezone.utc))
    run.verdicts.append(Verdict(case_id="proc-1", status=Status.PASS, checks=[]))
    run.traces.append(Trace(case_id="proc-1"))
    run.refresh_summary()
    store.save(run)
    dataset, ledger = default_paths(tmp_path / "reflow")
    report = reflow(store, TraceStore(tmp_path / "traces"), dataset, ledger)
    assert report.scanned_failures == 0
    assert report.emitted == 0
    assert report.dataset_size == 0


# --------------------------------------------------------------------------- CLI


def test_cli_reflow_run_reports_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    case = _case()
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [case.model_dump(mode="json")]}, ensure_ascii=False),
        encoding="utf-8",
    )
    store = RunStore(home / "runs")
    run = _run("run-1")
    run.metadata["cases_file"] = str(cases_path)
    store.save(run)

    code = main(["--home", str(home), "reflow", "run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["emitted"] == 1

    code = main(["--home", str(home), "reflow", "run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["emitted"] == 0
    assert payload["duplicates"] == 1

    assert (home / REFLOW_DIRNAME / "regression_cases.json").exists()
