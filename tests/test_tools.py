from __future__ import annotations

import pytest

from agenteval.fakes import EchoTool
from agenteval.tools import ToolError, ToolErrorKind, ToolRegistry, ToolResult


def test_schema_tool_passes_valid_arguments() -> None:
    result = EchoTool().invoke(message="hi", count=2)
    assert result.ok is True
    assert result.value == {"message": "hihi"}


def test_schema_tool_reports_missing_required_field() -> None:
    result = EchoTool().invoke(count=1)
    assert result.ok is False
    assert result.error_kind is ToolErrorKind.VALIDATION
    assert result.error_field == "message"


def test_schema_tool_reports_wrong_type_field() -> None:
    result = EchoTool().invoke(message="hi", count="not-an-integer")
    assert result.ok is False
    assert result.error_kind is ToolErrorKind.VALIDATION
    assert result.error_field == "count"


def test_tool_error_carries_kind_and_field() -> None:
    error = ToolError("validation", "bad input", field="count")
    assert error.kind is ToolErrorKind.VALIDATION
    assert error.field == "count"
    assert str(error) == "bad input"


def test_tool_result_defaults_to_single_attempt() -> None:
    assert ToolResult(ok=True).attempts == 1


def test_registry_dispatches_by_name() -> None:
    registry = ToolRegistry([EchoTool()])
    assert "echo_tool" in registry
    assert len(registry) == 1
    assert registry.names() == ["echo_tool"]
    assert registry.get("echo_tool").name == "echo_tool"


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry([EchoTool()])
    with pytest.raises(ValueError):
        registry.register(EchoTool())


def test_registry_rejects_nameless_tool() -> None:
    class Nameless:
        name = ""
        input_schema = {"type": "object"}

        def invoke(self, **kwargs: object) -> ToolResult:  # pragma: no cover - 不应被调用
            return ToolResult(ok=True)

    with pytest.raises(ValueError):
        ToolRegistry([Nameless()])


def test_registry_raises_on_unknown_tool() -> None:
    registry = ToolRegistry()
    with pytest.raises(KeyError, match="tool not registered"):
        registry.get("missing")
