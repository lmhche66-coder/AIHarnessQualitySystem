from __future__ import annotations

from pathlib import Path

from agenteval.fakes import build_demo_registry
from agenteval.models import Status
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_full_demo_run_produces_six_passing_verdicts(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    runner = ContractRunner(registry=build_demo_registry(), store=store)
    run = runner.run(load_cases(EXAMPLES / "contract_cases.json"), run_id="e2e-run")

    assert run.summary.total == 6
    assert run.summary.passed == 6
    assert run.summary.failed == 0
    assert run.summary.errored == 0
    assert all(verdict.status is Status.PASS for verdict in run.verdicts)
    assert all(verdict.checks for verdict in run.verdicts)

    restored = store.load("e2e-run")
    assert restored.summary == run.summary
    assert len(restored.traces) == 6
