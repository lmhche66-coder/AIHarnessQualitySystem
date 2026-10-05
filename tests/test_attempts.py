from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agenteval.cli import main
from agenteval.fakes import build_task_registry
from agenteval.models import Status, TaskCase
from agenteval.tasks import (
    TaskRunner,
    attempt_case_id,
    load_tasks,
    summarize_attempts,
    summarize_tasks,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FLAKY = EXAMPLES / "flaky_task.json"


def make_task(task_id: str, attempts: int = 1) -> TaskCase:
    return TaskCase.model_validate(
        {
            "id": task_id,
            "attempts": attempts,
            "checks": [
                {"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 30}
            ],
        }
    )


def charge_agent(registry: Any, task: Any) -> None:
    ledger = registry.get("ledger_tool")
    ledger.invoke(op="charge", amount=30)


def test_attempts_default_to_one() -> None:
    assert TaskCase.model_validate({"id": "t1"}).attempts == 1


def test_attempts_below_one_are_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskCase.model_validate({"id": "t1", "attempts": 0})


def test_single_attempt_keeps_plain_case_id() -> None:
    assert attempt_case_id("t1", 1, 1) == "t1"


def test_multiple_attempts_suffix_case_id() -> None:
    assert attempt_case_id("t1", 2, 3) == "t1#2"


def test_multiple_attempts_produce_one_verdict_each(tmp_path: Path) -> None:
    runner = TaskRunner(agent=charge_agent, environment=build_task_registry)
    run = runner.run([make_task("t1", attempts=3)], run_id="r1")
    assert [verdict.case_id for verdict in run.verdicts] == ["t1#1", "t1#2", "t1#3"]
    assert run.summary.passed == 3


def test_single_attempt_verdict_id_unchanged(tmp_path: Path) -> None:
    runner = TaskRunner(agent=charge_agent, environment=build_task_registry)
    run = runner.run([make_task("t1")], run_id="r1")
    assert [verdict.case_id for verdict in run.verdicts] == ["t1"]


def test_environment_is_rebuilt_for_every_attempt() -> None:
    # 持有引用，避免对象被回收后 id 复用造成误判
    seen: list[Any] = []

    def environment() -> Any:
        registry = build_task_registry()
        seen.append(registry.get("ledger_tool"))
        return registry

    runner = TaskRunner(agent=charge_agent, environment=environment)
    runner.run([make_task("t1", attempts=3)], run_id="r1")
    assert len(seen) == 3
    assert seen[0] is not seen[1]
    assert seen[1] is not seen[2]
    assert seen[0] is not seen[2]


def test_agent_is_rebuilt_for_every_attempt() -> None:
    built = {"count": 0}

    def build() -> Any:
        built["count"] += 1
        return charge_agent

    def agent(registry: Any, task: Any) -> None:
        build()(registry, task)

    runner = TaskRunner(agent=agent, environment=build_task_registry)
    runner.run([make_task("t1", attempts=3)], run_id="r1")
    assert built["count"] == 3


def test_flaky_task_is_resolved_but_pass_at_1_is_zero() -> None:
    from agenteval.models import Verdict

    task = make_task("t1", attempts=3)
    verdicts = [
        Verdict(case_id="t1#1", status=Status.FAIL),
        Verdict(case_id="t1#2", status=Status.PASS),
        Verdict(case_id="t1#3", status=Status.PASS),
    ]
    report = summarize_attempts([task], verdicts)
    assert report["pass_at_1"] == 0.0
    assert report["pass_at_k"] == 1.0
    assert report["attempts"] == 3
    assert report["details"][0]["passed"] == 2
    assert report["details"][0]["resolved"] is True


def test_task_counts_as_resolved_when_any_attempt_passes() -> None:
    from agenteval.models import Verdict

    task = make_task("t1", attempts=2)
    verdicts = [
        Verdict(case_id="t1#1", status=Status.FAIL),
        Verdict(case_id="t1#2", status=Status.PASS),
    ]
    report = summarize_tasks([task], verdicts)
    assert report["resolved_tasks"] == ["t1"]
    assert report["unresolved_tasks"] == []


def test_task_with_only_errors_counts_as_errored() -> None:
    from agenteval.models import Verdict

    task = make_task("t1", attempts=2)
    verdicts = [
        Verdict(case_id="t1#1", status=Status.ERROR),
        Verdict(case_id="t1#2", status=Status.ERROR),
    ]
    report = summarize_tasks([task], verdicts)
    assert report["errored_tasks"] == ["t1"]


def test_example_flaky_task_declares_three_attempts() -> None:
    tasks = load_tasks(FLAKY)
    assert [task.attempts for task in tasks] == [3]


def test_cli_reports_pass_at_1_and_pass_at_k(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "task",
            "run",
            "--tasks",
            str(FLAKY),
            "--registry",
            "agenteval.fakes:build_task_registry",
            "--agent",
            f"{EXAMPLES / 'demo_agent.py'}:build_flaky_agent",
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "pass@1: 0.0000" in output
    assert "pass@k: 1.0000" in output
    assert "[fail ] task-flaky-charge#1" in output
    assert "[pass ] task-flaky-charge#2" in output
