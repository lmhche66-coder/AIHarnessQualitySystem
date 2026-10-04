from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ENVIRONMENT = "agenteval.fakes:build_task_registry"
AGENT = f"{EXAMPLES / 'demo_agent.py'}:build_agent"


def test_cli_task_run_reports_resolved_rate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    code = main(
        [
            "--home",
            str(home),
            "task",
            "run",
            "--tasks",
            str(EXAMPLES / "tasks.json"),
            "--registry",
            ENVIRONMENT,
            "--agent",
            AGENT,
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["total"] == 2
    assert report["resolved_rate"] == 1.0
    assert report["unresolved_tasks"] == []


def test_cli_task_run_returns_nonzero_when_unresolved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "impossible",
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
    home = tmp_path / "home"
    code = main(
        [
            "--home",
            str(home),
            "task",
            "run",
            "--tasks",
            str(tasks_path),
            "--registry",
            ENVIRONMENT,
            "--agent",
            AGENT,
        ]
    )
    output = capsys.readouterr().out
    assert code == 1
    assert "resolved_rate: 0.0000" in output
    assert "errored: impossible" in output


def test_cli_task_run_is_consumable_by_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
                ENVIRONMENT,
                "--agent",
                AGENT,
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--home", str(home), "gate", "--min-pass-rate", "1.0", "--max-errors", "0"]) == 0
    assert "gate: PASS" in capsys.readouterr().out


def test_run_command_rejects_task_cases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    cases_path = tmp_path / "mixed.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "t1",
                        "kind": "task",
                        "checks": [{"kind": "no_extra_calls", "allowed": ["ledger_tool"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code = main(["--home", str(home), "run", "--cases", str(cases_path), "--demo"])
    assert code == 1
    assert "agenteval task run" in capsys.readouterr().out


def test_run_command_reports_unrecognized_cases_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """把任务文件喂给 run 时必须明确报错，而不是跑出一个零用例的通过。"""

    home = tmp_path / "home"
    code = main(["--home", str(home), "run", "--cases", str(EXAMPLES / "tasks.json"), "--demo"])
    assert code == 2
    assert "found keys: tasks" in capsys.readouterr().err
