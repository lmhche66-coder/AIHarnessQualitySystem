from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agenteval.cli import main
from agenteval.fakes import build_demo_registry
from agenteval.models import ProcessCase, Status, Trace
from agenteval.process import record_tool_call
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore
from agenteval.trace_store import TraceStore
from agenteval.tools import ToolRegistry, ToolResult

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PROCESS = EXAMPLES / "process_cases.json"


class BombTool:
    """求值已保存轨迹时若被调用即抛错，用于证明该路径不触达工具。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.input_schema = {"type": "object"}
        self.calls = 0

    def invoke(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        raise RuntimeError(f"tool '{self.name}' must not be called when evaluating a stored trace")


def import_mode_cases(cases: list[Any]) -> list[ProcessCase]:
    """把驱动型用例转成引用外部轨迹的用例：保留标识与断言，去掉步骤。"""

    return [ProcessCase(id=case.id, checks=case.checks) for case in cases]


def test_stored_trace_is_evaluated_without_calling_tools(tmp_path: Path) -> None:
    cases = load_cases(PROCESS)
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    recorded = runner.run(cases, run_id="record-run")
    assert recorded.summary.passed == len(cases)

    traces = {trace.case_id: trace for trace in recorded.traces}
    bombs = [BombTool(name) for name in ("echo_tool", "idempotent_tool", "payout_tool")]

    for case in import_mode_cases(cases):
        override_runner = ContractRunner(
            registry=ToolRegistry(bombs),
            store=RunStore(tmp_path / "runs"),
            trace_override=traces[case.id],
        )
        verdict, _ = override_runner.run_case(case)
        assert verdict.status is Status.PASS, (case.id, verdict.error)
    assert sum(bomb.calls for bomb in bombs) == 0


def test_steps_conflict_with_stored_trace(tmp_path: Path) -> None:
    case = load_cases(PROCESS)[0]
    runner = ContractRunner(
        registry=build_demo_registry(),
        store=RunStore(tmp_path / "runs"),
        trace_override=Trace(case_id=case.id),
    )
    verdict, _ = runner.run_case(case)
    assert verdict.status is Status.ERROR
    assert "exactly one trace source" in (verdict.error or "")


def test_case_without_steps_or_trace_is_error(tmp_path: Path) -> None:
    case = ProcessCase.model_validate(
        {"id": "no-source", "checks": [{"kind": "tool_sequence", "expected": ["a"]}]}
    )
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(tmp_path / "runs"))
    verdict, _ = runner.run_case(case)
    assert verdict.status is Status.ERROR
    assert "no stored trace was supplied" in (verdict.error or "")


def test_case_level_trace_reference_wins_over_run_level(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    good = Trace(case_id="p1")
    record_tool_call(good, "echo_tool", {}, ToolResult(ok=True))
    store.save("good", good)
    store.save("empty", Trace(case_id="p1"))

    case = ProcessCase.model_validate(
        {
            "id": "p1",
            "trace": "good",
            "checks": [{"kind": "tool_sequence", "expected": ["echo_tool"], "mode": "exact"}],
        }
    )
    runner = ContractRunner(
        registry=ToolRegistry([]),
        trace_override=store.load("empty"),
        trace_resolver=store.load,
    )
    verdict, trace = runner.run_case(case)
    assert verdict.status is Status.PASS
    assert [event.name for event in trace.events] == ["tool_call"]


def test_missing_case_level_trace_reports_error(tmp_path: Path) -> None:
    case = ProcessCase.model_validate(
        {
            "id": "p1",
            "trace": "ghost",
            "checks": [{"kind": "tool_sequence", "expected": ["a"]}],
        }
    )
    runner = ContractRunner(
        registry=ToolRegistry([]), trace_resolver=TraceStore(tmp_path / "traces").load
    )
    verdict, _ = runner.run_case(case)
    assert verdict.status is Status.ERROR
    assert "trace not found: ghost" in (verdict.error or "")


def write_import_cases(path: Path, case_id: str) -> None:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": case_id,
                        "kind": "process",
                        "checks": [
                            {
                                "kind": "tool_sequence",
                                "expected": ["echo_tool", "idempotent_tool", "payout_tool"],
                                "mode": "exact",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_cli_trace_save_list_and_run_with_trace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(home / "runs"))
    runner.run(load_cases(PROCESS), run_id="record-run")

    assert (
        main(
            [
                "--home",
                str(home),
                "trace",
                "save",
                "--name",
                "demo",
                "--run",
                "record-run",
                "--case",
                "process-exact-sequence",
            ]
        )
        == 0
    )
    assert "events: 3" in capsys.readouterr().out

    assert main(["--home", str(home), "trace", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == ["demo"]

    cases_path = tmp_path / "import_cases.json"
    write_import_cases(cases_path, "process-exact-sequence")
    assert main(["--home", str(home), "run", "--cases", str(cases_path), "--demo", "--trace", "demo"]) == 0
    assert "cases: 1  pass: 1" in capsys.readouterr().out

    run_store = RunStore(home / "runs")
    latest = run_store.latest_run_id()
    assert latest is not None and latest != "record-run"
    stored = run_store.load(latest)
    assert stored.metadata["trace_name"] == "demo"
    assert len(stored.traces[0].events) == 3


def test_cli_run_with_unknown_trace_fails_without_writing_a_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    cases_path = tmp_path / "import_cases.json"
    write_import_cases(cases_path, "process-exact-sequence")

    assert (
        main(["--home", str(home), "run", "--cases", str(cases_path), "--demo", "--trace", "absent"])
        == 2
    )
    assert "trace not found: absent" in capsys.readouterr().err
    assert RunStore(home / "runs").list_runs() == []


def test_gate_consumes_stored_trace_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    runner = ContractRunner(registry=build_demo_registry(), store=RunStore(home / "runs"))
    runner.run(load_cases(PROCESS), run_id="record-run")
    assert (
        main(
            [
                "--home",
                str(home),
                "trace",
                "save",
                "--name",
                "demo",
                "--run",
                "record-run",
                "--case",
                "process-exact-sequence",
            ]
        )
        == 0
    )
    capsys.readouterr()

    cases_path = tmp_path / "import_cases.json"
    write_import_cases(cases_path, "process-exact-sequence")
    assert main(["--home", str(home), "run", "--cases", str(cases_path), "--demo", "--trace", "demo"]) == 0
    capsys.readouterr()

    # 对同一份已保存轨迹再跑一次，门禁验证两次结论一致
    assert main(["--home", str(home), "baseline"]) == 0
    capsys.readouterr()
    assert main(["--home", str(home), "run", "--cases", str(cases_path), "--demo", "--trace", "demo"]) == 0
    capsys.readouterr()
    assert main(["--home", str(home), "gate"]) == 0
    output = capsys.readouterr().out
    assert "gate: PASS" in output
    assert "pass_rate: 1.0000" in output
