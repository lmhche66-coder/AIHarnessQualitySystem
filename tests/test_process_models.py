from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from agenteval.models import AnyCase, Case, ProcessCase

ADAPTER: TypeAdapter[AnyCase] = TypeAdapter(AnyCase)


def test_process_case_parses_with_steps_and_checks() -> None:
    case = ADAPTER.validate_python(
        {
            "id": "p1",
            "kind": "process",
            "steps": [{"target": "echo_tool", "input": {"message": "hi"}}],
            "checks": [{"kind": "tool_sequence", "expected": ["echo_tool"]}],
        }
    )
    assert isinstance(case, ProcessCase)
    assert case.steps[0].target == "echo_tool"
    assert case.checks[0].kind == "tool_sequence"


def test_tool_contract_case_still_parses() -> None:
    case = ADAPTER.validate_python(
        {
            "id": "c1",
            "kind": "tool_contract",
            "target": "echo_tool",
            "input": {"message": "hi"},
            "check": {"kind": "missing_required", "drop": ["message"]},
        }
    )
    assert isinstance(case, Case)


def test_process_case_rejects_tool_contract_fields() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "p1", "kind": "process", "target": "echo_tool"})


def test_tool_contract_case_rejects_process_check() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "id": "c1",
                "kind": "tool_contract",
                "target": "echo_tool",
                "check": {"kind": "tool_sequence", "expected": ["a"]},
            }
        )


def test_process_check_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "p1", "kind": "process", "checks": [{"kind": "nope"}]})


def test_process_step_requires_target() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "p1", "kind": "process", "steps": [{"input": {}}]})


def test_process_case_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"id": "", "kind": "process"})
