from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.mcp import (
    McpCase,
    McpCaseSet,
    McpRunner,
    McpServerSpec,
    load_mcp_cases,
)
from agenteval.models import Status
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SERVER = EXAMPLES / "mcp_demo_server.py"
CASES = EXAMPLES / "mcp_cases.json"
FLAKY_CASES = EXAMPLES / "mcp_flaky_cases.json"
PYTHON = sys.executable


def spec(**overrides: object) -> McpServerSpec:
    payload: dict[str, object] = {
        "command": PYTHON,
        "args": [str(SERVER)],
        "timeout_s": 20,
    }
    payload.update(overrides)
    return McpServerSpec.model_validate(payload)


def case(**overrides: object) -> McpCase:
    payload: dict[str, object] = {"id": "m1", "checks": [{"kind": "capability"}]}
    payload.update(overrides)
    return McpCase.model_validate(payload)


# --------------------------------------------------------------------------- 载入


def test_load_example_mcp_cases() -> None:
    case_set = load_mcp_cases(CASES)
    assert case_set.server.command
    assert [item.id for item in case_set.cases] == ["handshake-schema-and-arguments"]


def test_load_rejects_file_without_server(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="server"):
        load_mcp_cases(path)


# --------------------------------------------------------------------------- 会话


def test_handshake_and_capability_pass() -> None:
    verdict, _ = McpRunner().run_case(case(), spec())
    assert verdict.status is Status.PASS


def test_missing_tools_capability_fails() -> None:
    verdict, _ = McpRunner().run_case(case(), spec(env={"AGENTEVAL_MCP_NO_TOOLS": "1"}))
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "mcp.capability"
    assert "tools" in (verdict.failed_checks[0].message or "")


def test_missing_tool_is_reported() -> None:
    verdict, _ = McpRunner().run_case(
        case(checks=[{"kind": "tool_listed", "tool": "does_not_exist"}]), spec()
    )
    assert verdict.status is Status.FAIL
    assert "was not listed" in (verdict.failed_checks[0].message or "")


def test_missing_schema_is_reported() -> None:
    verdict, _ = McpRunner().run_case(
        case(checks=[{"kind": "tool_listed", "tool": "echo"}]),
        spec(env={"AGENTEVAL_MCP_NO_SCHEMA": "1"}),
    )
    assert verdict.status is Status.FAIL
    assert "inputSchema" in (verdict.failed_checks[0].message or "")


# --------------------------------------------------------------------------- 参数契约


def test_argument_contracts_pass_on_a_correct_server() -> None:
    verdict, _ = McpRunner().run_case(
        case(
            checks=[
                {"kind": "missing_required", "tool": "echo", "args": {}},
                {"kind": "wrong_type", "tool": "echo", "args": {"message": 123}},
                {"kind": "valid_call", "tool": "echo", "args": {"message": "hi"}},
            ]
        ),
        spec(),
    )
    assert verdict.status is Status.PASS
    assert [check.name for check in verdict.checks] == [
        "mcp.missing_required[echo]",
        "mcp.wrong_type[echo]",
        "mcp.valid_call[echo]",
    ]


def test_schema_consistency_passes_on_a_correct_server() -> None:
    verdict, _ = McpRunner().run_case(
        case(checks=[{"kind": "schema_consistency", "tool": "add", "args": {"a": 1, "b": 2}}]),
        spec(),
    )
    assert verdict.status is Status.PASS
    assert len(verdict.checks) == 2


def test_schema_consistency_detects_a_declared_but_unenforced_required_field() -> None:
    verdict, _ = McpRunner().run_case(
        case(checks=[{"kind": "schema_consistency", "tool": "echo", "args": {"message": "hi"}}]),
        spec(env={"AGENTEVAL_MCP_FLAKY": "1"}),
    )
    assert verdict.status is Status.FAIL
    failed = verdict.failed_checks[0]
    assert failed.name == "mcp.schema_consistency[echo.message]"
    assert "accepted a call without it" in (failed.message or "")


# --------------------------------------------------------------------------- 错误处理


def test_timeout_is_an_error() -> None:
    stalled = spec(command=PYTHON, args=["-c", "import time; time.sleep(60)"], timeout_s=0.5)
    verdict, _ = McpRunner().run_case(case(), stalled)
    assert verdict.status is Status.ERROR
    assert "McpTimeout" in (verdict.error or "")


def test_missing_executable_is_an_error() -> None:
    verdict, _ = McpRunner().run_case(case(), spec(command="definitely-not-a-real-mcp-binary"))
    assert verdict.status is Status.ERROR
    assert "could not start" in (verdict.error or "")


def test_one_failing_case_does_not_stop_the_run() -> None:
    case_set = McpCaseSet.model_validate(
        {
            "server": {"command": PYTHON, "args": [str(SERVER)]},
            "cases": [
                {
                    "id": "mismatch",
                    "server": {
                        "command": PYTHON,
                        "args": [str(SERVER)],
                        "env": {"AGENTEVAL_MCP_FLAKY": "1"},
                    },
                    "checks": [
                        {"kind": "schema_consistency", "tool": "echo", "args": {"message": "hi"}}
                    ],
                },
                {"id": "healthy", "checks": [{"kind": "capability"}]},
            ],
        }
    )
    run = McpRunner().run(case_set)
    assert run.summary.failed == 1
    assert run.summary.passed == 1


def test_run_record_is_persisted(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    run = McpRunner(store=store).run(load_mcp_cases(CASES), run_id="mcp-run")
    assert run.summary.passed == 1
    assert store.load("mcp-run").verdicts[0].status is Status.PASS


# --------------------------------------------------------------------------- CLI


def test_cli_runs_a_healthy_server(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--home", str(tmp_path / "home"), "mcp", "run", "--cases", str(CASES)])
    output = capsys.readouterr().out
    assert code == 0
    assert "cases: 1  pass: 1" in output


def test_cli_fails_the_schema_mismatch_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--home", str(tmp_path / "home"), "mcp", "run", "--cases", str(FLAKY_CASES)])
    output = capsys.readouterr().out
    assert code == 1
    assert "mcp.schema_consistency" in output


def test_cli_emits_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--home", str(tmp_path / "home"), "mcp", "run", "--cases", str(CASES), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload[0]["status"] == "pass"
