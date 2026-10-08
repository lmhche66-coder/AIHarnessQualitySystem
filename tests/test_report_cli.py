from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _run_demo(home: Path, cases: Path) -> None:
    assert main(["--home", str(home), "run", "--cases", str(cases), "--demo"]) == 0


def test_report_json_summarizes_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    _run_demo(home, EXAMPLES / "contract_cases.json")
    capsys.readouterr()

    assert main(["--home", str(home), "report", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "end_to_end"
    assert payload["primary_metric"] == "task_completion"
    assert payload["total"] == 6
    assert payload["primary_pass_rate"] == 1.0
    assert payload["eval_mode"] == "e2e_real"
    assert "cost" in payload and "performance" in payload and "quality" in payload


def test_report_records_conclusion(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    _run_demo(home, EXAMPLES / "contract_cases.json")
    capsys.readouterr()

    assert main(["--home", str(home), "report"]) == 0
    output = capsys.readouterr().out
    assert "scorecard:" in output
    assert "primary_pass_rate" in output

    reports = sorted((home / "reports").glob("report-*.json"))
    assert reports, "report should persist a conclusion record"
    record = json.loads(reports[0].read_text(encoding="utf-8"))
    assert record["kind"] == "report"
    assert record["summary"]["primary_metric"] == "task_completion"


def test_report_uses_metric_annotations_for_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    _run_demo(home, EXAMPLES / "scorecard_cases.json")
    capsys.readouterr()

    assert main(["--home", str(home), "report", "--scope", "tool_call", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "tool_call"
    assert payload["primary_metric"] == "tool_call_accuracy"
    metrics = {stat["metric"] for stat in payload["quality"]}
    assert {"tool_call_accuracy", "param_mapping_accuracy"} <= metrics
    scenes = {scene["scene"] for scene in payload["scenes"]}
    assert scenes == {"参数映射", "多步流程"}


def test_report_unknown_run_returns_error(tmp_path: Path) -> None:
    assert main(["--home", str(tmp_path / "home"), "report", "--run", "missing"]) == 2


def test_report_without_runs_returns_error(tmp_path: Path) -> None:
    assert main(["--home", str(tmp_path / "home"), "report"]) == 2
