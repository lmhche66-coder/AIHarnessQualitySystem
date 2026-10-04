from __future__ import annotations

from pathlib import Path

from agenteval.fakes import build_demo_registry
from agenteval.gate import Baseline, evaluate
from agenteval.models import ProcessCase, ToolSequenceCheck
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PROCESS = EXAMPLES / "process_cases.json"
CONTRACT = EXAMPLES / "replayable_cases.json"


def test_process_cases_run_and_pass(tmp_path: Path) -> None:
    cases = load_cases(PROCESS)
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    run = runner.run(cases, run_id="process-run")
    assert run.summary.total == len(cases)
    assert run.summary.passed == len(cases)
    assert run.summary.errored == 0


def test_process_and_contract_cases_share_one_run(tmp_path: Path) -> None:
    cases = list(load_cases(CONTRACT)) + list(load_cases(PROCESS))
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    run = runner.run(cases, run_id="mixed-run")
    assert run.summary.total == len(cases)
    assert run.summary.passed == len(cases)


def test_process_case_with_unregistered_tool_is_error(tmp_path: Path) -> None:
    case = ProcessCase.model_validate(
        {
            "id": "bad-step",
            "steps": [{"target": "ghost_tool", "input": {}}],
            "checks": [{"kind": "tool_sequence", "expected": ["ghost_tool"]}],
        }
    )
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    run = runner.run([case], run_id="ghost-run")
    assert run.summary.errored == 1
    assert "ghost_tool" in (run.verdicts[0].error or "")


def test_failing_process_case_is_caught_by_the_gate(tmp_path: Path) -> None:
    cases = load_cases(PROCESS)
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    baseline = Baseline.from_run(runner.run(cases, run_id="base-run"))

    failing = ProcessCase(
        id=cases[0].id,
        steps=cases[0].steps,
        checks=[ToolSequenceCheck(expected=["ghost_tool"], mode="exact")],
    )
    run = runner.run([failing], run_id="fail-run")
    assert run.summary.failed == 1

    result = evaluate(run, baseline=baseline)
    assert result.passed is False
    assert cases[0].id in result.regressions
