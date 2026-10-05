from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.reports import (
    KIND_GATE,
    KIND_JUDGE,
    KIND_TRIAGE,
    REPORTS_DIRNAME,
    ConclusionRecord,
    ConclusionStore,
    resolve_reports_dir,
    write_conclusion,
)
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
JUDGES = str(EXAMPLES / "demo_judges.py")
GOLD = EXAMPLES / "judge_gold.json"


def make_record(record_id: str, kind: str = KIND_GATE, created: str = "2026-01-01T00:00:00Z") -> ConclusionRecord:
    return ConclusionRecord(
        id=record_id,
        kind=kind,
        title=f"{kind} {record_id}",
        created_at=created,
        run_id="run-1",
        passed=True,
        summary={"run_id": "run-1"},
        payload={"detail": True},
    )


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    store = ConclusionStore(tmp_path / "reports")
    original = make_record("gate-1")
    path = store.save(original)
    assert path.is_file()
    assert store.load("gate-1") == original


def test_load_missing_conclusion_reports_id(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="conclusion not found: absent"):
        ConclusionStore(tmp_path / "reports").load("absent")


def test_listing_is_newest_first_and_skips_broken_files(tmp_path: Path) -> None:
    store = ConclusionStore(tmp_path / "reports")
    store.save(make_record("older", created="2026-01-01T00:00:00Z"))
    store.save(make_record("newer", created="2026-06-01T00:00:00Z"))
    (tmp_path / "reports" / "broken.json").write_text("{not json", encoding="utf-8")
    assert [record.id for record in store.list_records()] == ["newer", "older"]


def test_listing_absent_directory_is_empty(tmp_path: Path) -> None:
    assert ConclusionStore(tmp_path / "missing").list_records() == []


def test_resolve_reports_dir_default_and_env_override(tmp_path: Path) -> None:
    assert resolve_reports_dir(base=tmp_path, env={}) == tmp_path / ".agenteval" / REPORTS_DIRNAME
    override = tmp_path / "custom"
    assert (
        resolve_reports_dir(base=tmp_path, env={"AGENTEVAL_HOME": str(override)})
        == override / REPORTS_DIRNAME
    )


def test_write_conclusion_fills_id_and_timestamp(tmp_path: Path) -> None:
    store = ConclusionStore(tmp_path / "reports")
    record = write_conclusion(
        store,
        kind=KIND_JUDGE,
        title="judge calibration",
        passed=False,
        summary={"agreement": 0.5},
        payload={"items": 8},
    )
    assert record.id.startswith("judge-")
    assert store.load(record.id) == record


def test_conclusion_references_run_without_copying_verdicts(tmp_path: Path) -> None:
    store = ConclusionStore(tmp_path / "reports")
    record = write_conclusion(
        store,
        kind=KIND_GATE,
        title="gate run-1",
        run_id="run-1",
        summary={"run_id": "run-1", "pass_rate": 1.0},
        payload={"run_id": "run-1", "reasons": []},
    )
    blob = (tmp_path / "reports" / f"{record.id}.json").read_text(encoding="utf-8")
    assert "run-1" in blob
    assert "verdicts" not in blob


def write_run(home: Path, statuses: dict[str, str]) -> None:
    from agenteval.models import Run, Status, Verdict

    run = Run(run_id="run-1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [
        Verdict(case_id=case_id, status=Status(value)) for case_id, value in statuses.items()
    ]
    run.refresh_summary()
    RunStore(home / "runs").save(run)


def test_gate_command_records_a_conclusion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    write_run(home, {"a": "pass"})
    assert main(["--home", str(home), "gate"]) == 0
    capsys.readouterr()

    records = ConclusionStore(home / "reports").list_records()
    assert len(records) == 1
    record = records[0]
    assert record.kind == KIND_GATE
    assert record.passed is True
    assert record.summary["pass_rate"] == 1.0
    assert record.summary["regressions"] == []


def test_failing_gate_is_recorded_as_not_passed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    write_run(home, {"a": "fail"})
    assert main(["--home", str(home), "gate"]) == 1
    capsys.readouterr()
    record = ConclusionStore(home / "reports").list_records()[0]
    assert record.passed is False
    assert record.summary["failed"] == 1


def test_triage_command_records_a_conclusion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "p1",
                        "kind": "process",
                        "steps": [{"target": "echo_tool", "input": {"message": "hi"}}],
                        "checks": [
                            {"kind": "tool_sequence", "expected": ["payout_tool"], "mode": "exact"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["--home", str(home), "run", "--cases", str(cases_path), "--demo"]) == 1
    capsys.readouterr()
    assert main(["--home", str(home), "triage"]) == 0
    capsys.readouterr()

    record = ConclusionStore(home / "reports").list_records()[0]
    assert record.kind == KIND_TRIAGE
    assert record.summary["total_failures"] == 1
    assert record.summary["verified"] == 1
    assert record.summary["candidates"] == ["p1@repro"]


def test_judge_command_records_a_conclusion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    code = main(
        [
            "--home",
            str(home),
            "judge",
            "calibrate",
            "--gold",
            str(GOLD),
            "--judge",
            f"{JUDGES}:build_position_biased_judge",
        ]
    )
    capsys.readouterr()
    assert code == 1

    record = ConclusionStore(home / "reports").list_records()[0]
    assert record.kind == KIND_JUDGE
    assert record.passed is False
    assert record.summary["items"] == 8
    assert record.summary["position_flip_rate"] == 1.0
    assert record.summary["ci_low"] < record.summary["agreement"]
