from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.models import Status
from agenteval.selection import SelectionCase, SelectionRunner, load_selection_cases
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CASES = EXAMPLES / "selection_cases.json"


def case(**overrides: object) -> SelectionCase:
    payload: dict[str, object] = {"id": "s1"}
    payload.update(overrides)
    return SelectionCase.model_validate(payload)


def call(name: str, **arguments: object) -> dict[str, object]:
    return {"name": name, "arguments": arguments}


# --------------------------------------------------------------------------- 载入


def test_load_example_selection_cases() -> None:
    cases = load_selection_cases(CASES)
    assert [item.id for item in cases] == [
        "single-correct",
        "parallel-complete",
        "irrelevant-no-call",
    ]


# --------------------------------------------------------------------------- 名称


def test_matching_names_pass() -> None:
    verdict = SelectionRunner().run_case(
        case(expected=[call("get_weather", city="北京")], actual=[call("get_weather", city="北京")])
    )
    assert verdict.status is Status.PASS


def test_wrong_function_is_reported() -> None:
    verdict = SelectionRunner().run_case(
        case(expected=[call("get_weather", city="北京")], actual=[call("send_email", to="a@b.c")])
    )
    assert verdict.status is Status.FAIL
    names = [check for check in verdict.failed_checks if check.name == "selection.function_names"]
    assert names and "unexpected function calls" in (names[0].message or "")


def test_missing_function_is_reported() -> None:
    verdict = SelectionRunner().run_case(
        case(
            expected=[call("get_weather", city="北京"), call("send_email", to="a@b.c")],
            actual=[call("get_weather", city="北京")],
        )
    )
    assert verdict.status is Status.FAIL
    failed = verdict.failed_checks[0]
    assert "missing function calls" in (failed.message or "")


# --------------------------------------------------------------------------- 参数


def test_mismatched_argument_is_reported() -> None:
    verdict = SelectionRunner().run_case(
        case(expected=[call("get_weather", city="北京")], actual=[call("get_weather", city="上海")])
    )
    assert verdict.status is Status.FAIL
    failure = [check for check in verdict.failed_checks if "arguments" in check.name][0]
    assert "mismatched arguments" in (failure.message or "")
    assert failure.expected == {"city": "北京"}
    assert failure.actual == {"city": "上海"}


def test_missing_argument_is_reported() -> None:
    verdict = SelectionRunner().run_case(
        case(expected=[call("get_weather", city="北京")], actual=[call("get_weather")])
    )
    assert verdict.status is Status.FAIL
    assert "missing arguments" in (verdict.failed_checks[0].message or "")


def test_unexpected_argument_is_reported() -> None:
    verdict = SelectionRunner().run_case(
        case(
            expected=[call("get_weather", city="北京")],
            actual=[call("get_weather", city="北京", unit="c")],
        )
    )
    assert verdict.status is Status.FAIL
    assert "unexpected arguments" in (verdict.failed_checks[0].message or "")


# --------------------------------------------------------------------------- 并行与无关


def test_complete_parallel_calls_pass() -> None:
    verdict = SelectionRunner().run_case(
        case(
            expected=[call("get_weather", city="北京"), call("get_weather", city="上海")],
            actual=[call("get_weather", city="北京"), call("get_weather", city="上海")],
        )
    )
    assert verdict.status is Status.PASS
    assert "selection.parallel_completeness" in [check.name for check in verdict.checks]


def test_incomplete_parallel_calls_fail() -> None:
    verdict = SelectionRunner().run_case(
        case(
            expected=[call("get_weather", city="北京"), call("get_weather", city="上海")],
            actual=[call("get_weather", city="北京")],
        )
    )
    assert verdict.status is Status.FAIL
    parallel = [
        check for check in verdict.failed_checks if check.name == "selection.parallel_completeness"
    ]
    assert parallel and "incomplete" in (parallel[0].message or "")


def test_irrelevant_request_without_calls_passes() -> None:
    verdict = SelectionRunner().run_case(case(expected=[], actual=[]))
    assert verdict.status is Status.PASS
    assert verdict.checks[0].name == "selection.irrelevance"


def test_irrelevant_request_with_a_call_fails() -> None:
    verdict = SelectionRunner().run_case(case(expected=[], actual=[call("send_email", to="a@b.c")]))
    assert verdict.status is Status.FAIL
    assert "irrelevant request" in (verdict.failed_checks[0].message or "")


# --------------------------------------------------------------------------- 运行与 CLI


def test_run_record_is_persisted(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = SelectionRunner(store=store).run(load_selection_cases(CASES), run_id="selection-run")
    assert run.summary.passed == 3
    assert store.load("selection-run").summary.total == 3


def test_cli_runs_selection_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--home", str(tmp_path / "home"), "selection", "run", "--cases", str(CASES)])
    output = capsys.readouterr().out
    assert code == 0
    assert "cases: 3  pass: 3" in output


def test_cli_can_drive_actual_calls_from_an_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = f"{EXAMPLES / 'demo_selection_agent.py'}:build_agent"
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "selection",
            "run",
            "--cases",
            str(CASES),
            "--agent",
            agent,
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "cases: 3  pass: 3" in output


def test_cli_emits_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["--home", str(tmp_path / "home"), "selection", "run", "--cases", str(CASES), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert [entry["status"] for entry in payload] == ["pass", "pass", "pass"]
