from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.fakes import build_task_registry
from agenteval.models import FinalStateCheck, TaskCase, Trace
from agenteval.process import record_tool_call
from agenteval.tasks import (
    TaskRunner,
    check_final_state,
    evaluate_task_checks,
    load_tasks,
    summarize_tasks,
)
from agenteval.tools import ToolRegistry, ToolResult

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def make_task(task_id: str, checks: list[dict[str, Any]]) -> TaskCase:
    return TaskCase.model_validate({"id": task_id, "checks": checks})


def test_task_registry_factory_returns_isolated_instances() -> None:
    assert build_task_registry().get("ledger_tool") is not build_task_registry().get("ledger_tool")


def test_final_state_matches() -> None:
    registry = build_task_registry()
    registry.get("ledger_tool").invoke(op="charge", amount=30)
    outcome = check_final_state(
        FinalStateCheck(tool="ledger_tool", field="balance", expected=30), registry
    )
    assert outcome.passed is True


def test_final_state_mismatch_reports_values() -> None:
    outcome = check_final_state(
        FinalStateCheck(tool="ledger_tool", field="balance", expected=30), build_task_registry()
    )
    assert outcome.passed is False
    assert (outcome.expected, outcome.actual) == (30, 0)


def test_final_state_missing_tool() -> None:
    outcome = check_final_state(
        FinalStateCheck(tool="ghost", field="balance", expected=1), build_task_registry()
    )
    assert outcome.passed is False
    assert "not registered" in (outcome.message or "")


def test_final_state_without_state_reader() -> None:
    class NoState:
        name = "nostate"
        input_schema = {"type": "object"}

        def invoke(self, **kwargs: Any) -> ToolResult:  # pragma: no cover - 不应被调用
            return ToolResult(ok=True)

    outcome = check_final_state(
        FinalStateCheck(tool="nostate", field="balance", expected=1), ToolRegistry([NoState()])
    )
    assert outcome.passed is False
    assert "does not expose state()" in (outcome.message or "")


def test_final_state_missing_field() -> None:
    outcome = check_final_state(
        FinalStateCheck(tool="ledger_tool", field="ghost", expected=1), build_task_registry()
    )
    assert outcome.passed is False
    assert "no field" in (outcome.message or "")


def test_final_state_requires_configuration() -> None:
    outcome = check_final_state(FinalStateCheck(), build_task_registry())
    assert outcome.passed is False
    assert outcome.name == "final_state.configured"


def test_evaluate_task_checks_combines_process_and_state() -> None:
    task = make_task(
        "t1",
        [
            {"kind": "no_extra_calls", "allowed": ["ledger_tool"]},
            {"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 30},
        ],
    )
    registry = build_task_registry()
    result = registry.get("ledger_tool").invoke(op="charge", amount=30)
    trace = Trace(case_id="t1")
    record_tool_call(trace, "ledger_tool", {"op": "charge", "amount": 30}, result)
    outcomes = evaluate_task_checks(task, registry, trace)
    assert [outcome.name for outcome in outcomes] == ["no_extra_calls", "final_state"]
    assert all(outcome.passed for outcome in outcomes)


def test_runner_resolves_task_with_reference_agent() -> None:
    task = make_task(
        "t1",
        [
            {"kind": "no_extra_calls", "allowed": ["ledger_tool"]},
            {"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 30},
        ],
    )

    def agent(registry: Any, current: Any) -> None:
        registry.get("ledger_tool").invoke(op="charge", amount=30)

    run = TaskRunner(agent=agent, environment=build_task_registry).run([task], run_id="r1")
    assert run.summary.passed == 1
    assert run.metadata["task_report"]["resolved_rate"] == 1.0


def test_runner_marks_task_unresolved_when_check_fails() -> None:
    task = make_task(
        "t1",
        [{"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 999}],
    )

    def agent(registry: Any, current: Any) -> None:
        registry.get("ledger_tool").invoke(op="charge", amount=30)

    run = TaskRunner(agent=agent, environment=build_task_registry).run([task], run_id="r1")
    assert run.summary.failed == 1
    assert run.metadata["task_report"]["unresolved_tasks"] == ["t1"]


def test_runner_marks_error_when_agent_raises() -> None:
    task = make_task("t1", [{"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 0}])

    def agent(registry: Any, current: Any) -> None:
        raise RuntimeError("boom")

    run = TaskRunner(agent=agent, environment=build_task_registry).run([task], run_id="r1")
    assert run.summary.errored == 1
    assert "boom" in (run.verdicts[0].error or "")


def test_runner_marks_error_when_environment_fails() -> None:
    def broken_environment() -> ToolRegistry:
        raise RuntimeError("no environment")

    task = make_task("t1", [{"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 0}])
    run = TaskRunner(agent=lambda registry, current: None, environment=broken_environment).run(
        [task], run_id="r1"
    )
    assert run.summary.errored == 1
    assert "environment setup failed" in (run.verdicts[0].error or "")


def test_environment_isolation_between_tasks() -> None:
    tasks = [
        make_task(
            "dirty",
            [{"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 100}],
        ),
        make_task(
            "clean",
            [{"kind": "final_state", "tool": "ledger_tool", "field": "balance", "expected": 0}],
        ),
    ]

    def agent(registry: Any, task: Any) -> None:
        if task.id == "dirty":
            registry.get("ledger_tool").invoke(op="charge", amount=100)

    run = TaskRunner(agent=agent, environment=build_task_registry).run(tasks, run_id="r1")
    assert run.summary.passed == 2


def test_task_without_checks_is_unresolved() -> None:
    run = TaskRunner(agent=lambda registry, task: None, environment=build_task_registry).run(
        [make_task("t1", [])], run_id="r1"
    )
    assert run.summary.failed == 1
    assert run.verdicts[0].failed_checks[0].name == "task.checks_configured"


def test_empty_task_set_is_not_a_pass() -> None:
    run = TaskRunner(agent=lambda registry, task: None, environment=build_task_registry).run(
        [], run_id="r1"
    )
    report = run.metadata["task_report"]
    assert report["total"] == 0
    assert report["resolved_rate"] == 0.0


def test_summarize_tasks_separates_unresolved_and_errored() -> None:
    from agenteval.models import Status, Verdict

    tasks = [make_task("a", []), make_task("b", []), make_task("c", [])]
    verdicts = [
        Verdict(case_id="a", status=Status.PASS),
        Verdict(case_id="b", status=Status.FAIL),
        Verdict(case_id="c", status=Status.ERROR),
    ]
    report = summarize_tasks(tasks, verdicts)
    assert report["resolved_tasks"] == ["a"]
    assert report["unresolved_tasks"] == ["b"]
    assert report["errored_tasks"] == ["c"]
    assert report["resolved_rate"] == 1 / 3


def test_load_tasks_from_example_file() -> None:
    tasks = load_tasks(EXAMPLES / "tasks.json")
    assert [task.id for task in tasks] == [
        "task-charge-and-settle",
        "task-refund-reduces-balance",
    ]
