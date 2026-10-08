from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.engine import (
    AgentEvalRunner,
    DatasetRegistry,
    EvalMode,
    assemble_cases,
    format_plan,
    plan_evaluation,
    run_plan,
)
from agenteval.models import Status
from agenteval.scorecard import EVAL_MODE_MOCK, build_scorecard
from agenteval.store import RunStore
from agenteval.tasks import TaskRunner
from agenteval.tools import ToolRegistry

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

TASK_CASES = [
    {
        "id": "ok",
        "kind": "task",
        "checks": [{"kind": "no_extra_calls", "allowed": ["echo_tool"]}],
    }
]

FAILING_TASK_CASES = [
    {
        "id": "wrong",
        "kind": "task",
        "checks": [{"kind": "tool_sequence", "expected": ["other_tool"], "mode": "exact"}],
    }
]

BROKEN_TASK_CASES = [
    {
        "id": "broken",
        "kind": "task",
        "checks": [{"kind": "no_extra_calls", "allowed": ["echo_tool"]}],
    }
]

SLOW_TASK_CASES = [
    {
        "id": "slow",
        "kind": "task",
        "checks": [{"kind": "no_extra_calls", "allowed": ["echo_tool"]}],
    }
]


def write_cases(path: Path, cases: list[dict], key: str = "cases") -> Path:
    path.write_text(json.dumps({key: cases}), encoding="utf-8")
    return path


def write_registry(tmp_path: Path, mapping: dict[str, list[str]]) -> Path:
    path = tmp_path / "datasets.json"
    path.write_text(json.dumps({"datasets": mapping}), encoding="utf-8")
    return path


def make_runner(tmp_path: Path, agent=None) -> AgentEvalRunner:
    run = agent or (lambda registry, task: None)
    return AgentEvalRunner(
        tasks=TaskRunner(agent=run, environment=lambda: ToolRegistry([])),
        store=RunStore(tmp_path / "runs"),
    )


# --- 装配 -------------------------------------------------------------------


def test_plan_assembles_multiple_datasets_and_reports_missing(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    write_cases(tmp_path / "process.json", TASK_CASES)
    registry = DatasetRegistry.load(
        write_registry(tmp_path, {"basic_function": ["basic.json"], "knowledge_qa": ["process.json"]})
    )
    plan = plan_evaluation("end_to_end", registry)
    assert set(plan.sources) == {"basic_function", "knowledge_qa"}
    assert {d.value for d in plan.missing} == {"multi_turn", "abnormal_input"}
    assert plan.primary_metric == "task_completion"


def test_plan_uses_scope_level_primary_metric_for_modules(tmp_path: Path) -> None:
    write_cases(tmp_path / "tool.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"tool_call": ["tool.json"]}))
    plan = plan_evaluation("tool_module", registry)
    assert plan.primary_metric == "tool_call_accuracy"


def test_plan_rejects_scope_without_registered_cases(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    with pytest.raises(ValueError):
        plan_evaluation("memory_module", registry)


def test_plan_validates_execution_parameters(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    with pytest.raises(ValueError):
        plan_evaluation("basic_function", registry, concurrency=0)
    with pytest.raises(ValueError):
        plan_evaluation("basic_function", registry, timeout_s=0)
    with pytest.raises(ValueError):
        plan_evaluation("basic_function", registry, retry_interval_s=-1)


def test_registry_resolves_paths_relative_to_the_registry_file(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    assert registry.files_for("basic_function")[0].is_file()


def test_assemble_tags_dataset_type(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry)
    cases = assemble_cases(plan)
    assert cases and cases[0].dataset_type == "basic_function"


def test_assemble_reads_tasks_key(tmp_path: Path) -> None:
    write_cases(tmp_path / "tasks.json", TASK_CASES, key="tasks")
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["tasks.json"]}))
    plan = plan_evaluation("basic_function", registry)
    assert [case.id for case in assemble_cases(plan)] == ["ok"]


# --- 执行（agent 链路）-----------------------------------------------------


def test_run_plan_executes_and_sets_metadata(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry, eval_mode=EvalMode.MOCK)
    run = run_plan(plan, make_runner(tmp_path))
    assert run.metadata["scope"] == "basic_function"
    assert run.metadata["eval_mode"] == "e2e_mock"
    assert run.metadata["engine"]["kinds"] == {"task": 1, "dialogue": 0}
    assert run.verdicts[0].status is Status.PASS


def test_eval_mode_flows_to_scorecard(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry, eval_mode=EvalMode.MOCK)
    run = run_plan(plan, make_runner(tmp_path))
    assert build_scorecard(run).eval_mode == EVAL_MODE_MOCK


def test_engine_rejects_contract_cases(tmp_path: Path) -> None:
    # 文章的执行链路只驱动 agent；契约用例归 run
    contract = [
        {
            "id": "c1",
            "target": "echo_tool",
            "input": {"message": "hi"},
            "check": {"kind": "missing_required", "drop": ["message"]},
        }
    ]
    write_cases(tmp_path / "basic.json", contract)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry)
    with pytest.raises(ValueError):
        run_plan(plan, make_runner(tmp_path))


def test_fail_is_not_retried(tmp_path: Path) -> None:
    write_cases(tmp_path / "fail.json", FAILING_TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"tool_call": ["fail.json"]}))
    plan = plan_evaluation("tool_call", registry, retries=2)
    run = run_plan(plan, make_runner(tmp_path))
    assert run.metadata["engine"]["attempts"]["wrong"] == 1
    assert run.verdicts[0].status is Status.FAIL


def test_execution_error_is_retried(tmp_path: Path) -> None:
    write_cases(tmp_path / "broken.json", BROKEN_TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"tool_call": ["broken.json"]}))

    def boom(registry, task):
        raise RuntimeError("agent exploded")

    plan = plan_evaluation("tool_call", registry, retries=2, retry_interval_s=0.0)
    run = run_plan(plan, make_runner(tmp_path, agent=boom))
    assert run.metadata["engine"]["attempts"]["broken"] == 3
    assert run.verdicts[0].status is Status.ERROR


def test_concurrency_is_recorded(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry, concurrency=4)
    run = run_plan(plan, make_runner(tmp_path))
    assert run.metadata["engine"]["concurrency"] == 4
    assert run.verdicts[0].status is Status.PASS


def test_case_timeout_marks_error(tmp_path: Path) -> None:
    import time

    write_cases(tmp_path / "slow.json", SLOW_TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"tool_call": ["slow.json"]}))

    def slow(registry, task):
        time.sleep(0.4)

    plan = plan_evaluation("tool_call", registry, timeout_s=0.05)
    run = run_plan(plan, make_runner(tmp_path, agent=slow))
    assert run.verdicts[0].status is Status.ERROR
    assert "timed out" in (run.verdicts[0].error or "")


def test_agent_output_is_captured(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry)
    run = run_plan(
        plan, make_runner(tmp_path, agent=lambda registry, task: "任务已完成")
    )
    assert run.verdicts[0].output == "任务已完成"


def test_judge_runs_inside_eval_and_merges_checks(tmp_path: Path) -> None:
    """文章 §6.1 步骤 4d：Agent 输出 + EvalTrace 一起交裁判，结论并入同一运行。"""

    from agenteval.judge_tasks import JudgeTask, JudgeTaskKind, build_tasks

    cases = [
        {
            "id": "ok",
            "kind": "task",
            "user_input": "把 30 元扣款并结算",
            "expected_output": "已完成，余额 30 元。",
            "expected": {"task_completion": "pass"},
            "checks": [
                {"kind": "no_extra_calls", "allowed": ["echo_tool"], "metric": "task_completion"}
            ],
        }
    ]
    write_cases(tmp_path / "basic.json", cases)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry)

    runner = make_runner(tmp_path, agent=lambda registry, task: "已完成，余额 30 元。")
    runner.judge_tasks = build_tasks(["task_completion"])
    runner.judge = lambda task, case: '{"reasoning": "完成", "verdict": "pass"}'
    run = run_plan(plan, runner)

    assert run.metadata["engine"]["judged_checks"] == 1
    metrics = [check.metric for check in run.verdicts[0].checks]
    # 1 条确定性断言 + 1 条裁判结论
    assert metrics.count("task_completion") == 2
    assert run.verdicts[0].status is Status.PASS


def test_judge_failure_marks_case_failed(tmp_path: Path) -> None:
    from agenteval.judge_tasks import build_tasks

    cases = [
        {
            "id": "ok",
            "kind": "task",
            "expected": {"task_completion": "pass"},
            "checks": [
                {"kind": "no_extra_calls", "allowed": ["echo_tool"], "metric": "task_completion"}
            ],
        }
    ]
    write_cases(tmp_path / "basic.json", cases)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    plan = plan_evaluation("basic_function", registry)

    runner = make_runner(tmp_path)
    runner.judge_tasks = build_tasks(["task_completion"])
    runner.judge = lambda task, case: '{"reasoning": "未完成", "verdict": "fail"}'
    run = run_plan(plan, runner)

    assert run.verdicts[0].status is Status.FAIL


def test_format_plan_mentions_missing_datasets(tmp_path: Path) -> None:
    write_cases(tmp_path / "basic.json", TASK_CASES)
    registry = DatasetRegistry.load(write_registry(tmp_path, {"basic_function": ["basic.json"]}))
    text = format_plan(plan_evaluation("end_to_end", registry))
    assert "missing" in text


# --- 端到端（真实示例文件）--------------------------------------------------


def test_eval_run_example_registry_covers_examples() -> None:
    registry = DatasetRegistry.load(EXAMPLES / "eval_datasets.json")
    plan = plan_evaluation("end_to_end", registry)
    assert "basic_function" in plan.sources and "multi_turn" in plan.sources


def test_eval_run_cli_produces_scorecard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agenteval.cli import main

    home = tmp_path / "home"
    code = main(
        [
            "--home",
            str(home),
            "eval",
            "run",
            "--scope",
            "end_to_end",
            "--datasets",
            str(EXAMPLES / "eval_datasets.json"),
            "--agents",
            str(EXAMPLES / "self_contained.agents.json"),
            "--agent",
            "@self-contained",
            "--retries",
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "eval plan: end_to_end" in out
    assert "scorecard:" in out
    assert list((home / "reports").glob("report-*.json"))


def test_eval_run_cli_requires_agent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agenteval.cli import main

    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "eval",
            "run",
            "--scope",
            "basic_function",
            "--datasets",
            str(EXAMPLES / "eval_datasets.json"),
        ]
    )
    capsys.readouterr()
    assert code == 2
