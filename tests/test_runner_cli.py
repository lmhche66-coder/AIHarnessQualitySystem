from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.fakes import build_demo_registry
from agenteval.models import Status
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_load_cases_from_json() -> None:
    cases = load_cases(EXAMPLES / "contract_cases.json")
    assert len(cases) == 6
    assert cases[0].id == "contract-missing-required"


def test_load_cases_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(
        "cases:\n"
        "  - id: yaml-case\n"
        "    target: echo_tool\n"
        "    input: {message: hi}\n"
        "    check: {kind: missing_required, drop: [message]}\n",
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert [case.id for case in cases] == ["yaml-case"]


def test_load_cases_rejects_non_list(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text('{"cases": "nope"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


def test_runner_saves_and_reloads(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    runner = ContractRunner(registry=build_demo_registry(), store=store)
    cases = load_cases(EXAMPLES / "contract_cases.json")
    run = runner.run(cases, run_id="fixed-run")
    assert run.summary.total == 6
    assert run.summary.passed == 6
    restored = store.load("fixed-run")
    assert restored.run_id == run.run_id
    assert [verdict.status for verdict in restored.verdicts] == [Status.PASS] * 6


def test_runner_reports_unregistered_tool(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    runner = ContractRunner(registry=build_demo_registry(), store=store)
    cases = load_cases(EXAMPLES / "contract_cases.json")
    cases[0] = cases[0].model_copy(update={"target": "ghost_tool"})
    run = runner.run(cases, run_id="ghost-run")
    assert run.summary.errored == 1
    assert "ghost_tool" in (run.verdicts[0].error or "")


def test_cli_run_list_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    cases = EXAMPLES / "contract_cases.json"
    assert main(["--home", str(home), "run", "--cases", str(cases), "--demo"]) == 0
    capsys.readouterr()  # 丢弃 run 命令自身的输出

    assert main(["--home", str(home), "list", "--json"]) == 0
    run_ids = json.loads(capsys.readouterr().out)
    assert len(run_ids) == 1

    assert main(["--home", str(home), "show", run_ids[0]]) == 0
    assert "cases: 6" in capsys.readouterr().out


def test_cli_show_unknown_run_returns_error(tmp_path: Path) -> None:
    assert main(["--home", str(tmp_path / "home"), "show", "missing-run"]) == 2
