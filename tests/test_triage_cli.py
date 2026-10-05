from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def write_process_case(path: Path, case_id: str, expected: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": case_id,
                        "kind": "process",
                        "steps": [{"target": "echo_tool", "input": {"message": "hi"}}],
                        "checks": [{"kind": "tool_sequence", "expected": expected, "mode": "exact"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_cli_triage_emits_reproducible_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    cases_path = tmp_path / "cases.json"
    write_process_case(cases_path, "p1", ["payout_tool"])

    # 步骤只调用 echo_tool，判据期望 payout_tool，因此必然失败
    assert main(["--home", str(home), "run", "--cases", str(cases_path), "--demo"]) == 1
    capsys.readouterr()

    emitted = tmp_path / "candidates.json"
    assert main(["--home", str(home), "triage", "--emit", str(emitted), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total_failures"] == 1
    assert report["reflowable"] == 1
    assert report["verified"] == 1

    payload = json.loads(emitted.read_text(encoding="utf-8"))
    candidate = payload["cases"][0]
    assert candidate["id"] == "p1@repro"
    assert candidate["trace"] == "repro-p1"
    assert candidate["steps"] == []

    # 候选用例引用的证据轨迹必须在无 agent 的情况下复现同一失败
    assert main(["--home", str(home), "run", "--cases", str(emitted), "--demo"]) == 1
    output = capsys.readouterr().out
    assert "[fail ] p1@repro" in output
    assert "tool_sequence.exact" in output


def test_cli_triage_marks_state_dependent_failure_as_not_reflowable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "task-charge-and-settle",
                        "kind": "task",
                        "checks": [
                            {
                                "kind": "final_state",
                                "tool": "ledger_tool",
                                "field": "balance",
                                "expected": 999,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--home",
                str(home),
                "task",
                "run",
                "--tasks",
                str(tasks_path),
                "--registry",
                "agenteval.fakes:build_task_registry",
                "--agent",
                f"{EXAMPLES / 'demo_agent.py'}:build_agent",
            ]
        )
        == 1
    )
    capsys.readouterr()

    emitted = tmp_path / "candidates.json"
    assert main(["--home", str(home), "triage", "--emit", str(emitted), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total_failures"] == 1
    assert report["reflowable"] == 0
    assert "environment state" in report["details"][0]["reason"]
    assert json.loads(emitted.read_text(encoding="utf-8"))["cases"] == []


def test_cli_triage_reports_no_failures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert (
        main(
            [
                "--home",
                str(home),
                "task",
                "run",
                "--tasks",
                str(EXAMPLES / "tasks.json"),
                "--registry",
                "agenteval.fakes:build_task_registry",
                "--agent",
                f"{EXAMPLES / 'demo_agent.py'}:build_agent",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--home", str(home), "triage"]) == 0
    assert "no failures" in capsys.readouterr().out
