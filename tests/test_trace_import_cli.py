from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agenteval.cli import main
from agenteval.process import extract_tool_calls
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore
from agenteval.trace_store import TraceStore
from agenteval.tools import ToolRegistry, ToolResult

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
TRACE_NAME = "kingfar-eeg-sample-loss"
CASE_ID = "task-eeg-sample-loss"


class BombTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.input_schema = {"type": "object"}
        self.calls = 0

    def invoke(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        raise RuntimeError(f"tool '{self.name}' must not be called")


def import_trace(home: Path, source: Path, capsys: pytest.CaptureFixture[str]) -> dict:
    code = main(
        [
            "--home",
            str(home),
            "trace",
            "import",
            "--from",
            str(source),
            "--name",
            TRACE_NAME,
            "--case-id",
            CASE_ID,
            "--json",
        ]
    )
    assert code == 0
    return json.loads(capsys.readouterr().out)


def test_cli_imports_json_audit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    report = import_trace(home, EXAMPLES / "kingfar_audit.json", capsys)
    assert report["records"] == 5
    assert report["case_id"] == CASE_ID
    assert any("state_continuity" in item for item in report["limitations"])

    trace = TraceStore(home / "traces").load(TRACE_NAME)
    assert len(trace.events) == 5
    assert trace.case_id == CASE_ID


def test_cli_imports_csv_audit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    import_trace(home, EXAMPLES / "kingfar_audit.csv", capsys)
    trace = TraceStore(home / "traces").load(TRACE_NAME)
    assert [call.target for call in extract_tool_calls(trace)] == [
        "knowledge_retrieval",
        "ListTopics",
        "SearchLog",
        "SearchLog",
        "knowledge_retrieval",
    ]


def test_cli_rejects_missing_audit_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "trace",
            "import",
            "--from",
            str(tmp_path / "absent.json"),
            "--name",
            "x",
        ]
    )
    assert code == 2
    assert "audit file not found" in capsys.readouterr().err


def test_imported_trace_runs_process_cases_without_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    import_trace(home, EXAMPLES / "kingfar_audit.json", capsys)

    cases = load_cases(EXAMPLES / "kingfar_cases.json")
    bombs = [BombTool("knowledge_retrieval"), BombTool("SearchLog"), BombTool("ListTopics")]
    runner = ContractRunner(
        registry=ToolRegistry(bombs),
        store=RunStore(home / "runs"),
        trace_resolver=TraceStore(home / "traces").load,
    )
    run = runner.run(cases, run_id="imported-run")

    assert run.summary.passed == 1
    assert run.summary.errored == 0
    assert sum(bomb.calls for bomb in bombs) == 0
    verdict = run.verdicts[0]
    assert [check.name for check in verdict.checks] == [
        "tool_sequence.subsequence",
        "no_extra_calls",
        "recovery",
        "budget.max_calls",
        "budget.max_retries",
    ]
    assert verdict.metrics.calls == 5
    assert verdict.metrics.retries == 1
