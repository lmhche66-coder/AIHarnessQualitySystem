from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from agenteval.models import AnyCase, TaskCase

ADAPTER: TypeAdapter[AnyCase] = TypeAdapter(AnyCase)


def test_task_case_parses() -> None:
    case = ADAPTER.validate_python(
        {
            "id": "t1",
            "kind": "task",
            "checks": [
                {
                    "kind": "final_state",
                    "tool": "ledger_tool",
                    "field": "balance",
                    "expected": 30,
                }
            ],
        }
    )
    assert isinstance(case, TaskCase)
    assert case.checks[0].kind == "final_state"


def test_task_case_accepts_process_checks() -> None:
    case = ADAPTER.validate_python(
        {"id": "t1", "kind": "task", "checks": [{"kind": "no_extra_calls", "allowed": ["a"]}]}
    )
    assert isinstance(case, TaskCase)


def test_task_case_rejects_tool_contract_fields() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "t1", "kind": "task", "target": "echo_tool"})


def test_task_case_rejects_unknown_check_kind() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "t1", "kind": "task", "checks": [{"kind": "nope"}]})


def test_task_case_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "", "kind": "task"})
