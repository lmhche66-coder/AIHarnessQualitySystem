from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.models import Run, Status, Verdict
from agenteval.store import RunStore


def make_run(run_id: str, statuses: dict[str, Status]) -> Run:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z")
    run.verdicts = [Verdict(case_id=case_id, status=status) for case_id, status in statuses.items()]
    run.refresh_summary()
    return run


def test_baseline_then_gate_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    RunStore(home / "runs").save(make_run("run-1", {"a": Status.PASS}))

    assert main(["--home", str(home), "baseline"]) == 0
    capsys.readouterr()

    assert main(["--home", str(home), "gate", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["baseline_run_id"] == "run-1"


def test_gate_fails_on_regression(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    store = RunStore(home / "runs")
    store.save(make_run("run-1", {"a": Status.PASS}))
    assert main(["--home", str(home), "baseline"]) == 0
    store.save(make_run("run-2", {"a": Status.FAIL}))

    assert main(["--home", str(home), "gate"]) == 1
    output = capsys.readouterr().out
    assert "gate: FAIL" in output
    assert "regressions (1): a" in output


def test_gate_defaults_to_latest_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    store = RunStore(home / "runs")
    store.save(make_run("run-1", {"a": Status.PASS}))
    store.save(make_run("run-2", {"a": Status.ERROR}))

    assert main(["--home", str(home), "gate"]) == 1
    assert "run: run-2" in capsys.readouterr().out


def test_gate_without_baseline_uses_thresholds_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    RunStore(home / "runs").save(make_run("run-1", {"a": Status.PASS}))
    assert main(["--home", str(home), "gate"]) == 0
    assert "baseline: none" in capsys.readouterr().out


def test_gate_explicit_missing_baseline_is_an_error(tmp_path: Path) -> None:
    home = tmp_path / "home"
    RunStore(home / "runs").save(make_run("run-1", {"a": Status.PASS}))
    assert main(["--home", str(home), "gate", "--name", "absent"]) == 2


def test_baseline_without_runs_is_an_error(tmp_path: Path) -> None:
    assert main(["--home", str(tmp_path / "home"), "baseline"]) == 2


def test_gate_thresholds_are_configurable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    RunStore(home / "runs").save(make_run("run-1", {"a": Status.PASS, "b": Status.FAIL}))
    assert main(["--home", str(home), "gate", "--min-pass-rate", "0.5"]) == 0
    assert main(["--home", str(home), "gate", "--min-pass-rate", "0.9"]) == 1
