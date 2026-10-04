from __future__ import annotations

import time

from agenteval.fakes import (
    FlakyRateLimitTool,
    IdempotentTool,
    PartialFailureTool,
    SlowTool,
    build_demo_registry,
)
from agenteval.tools import ToolErrorKind


def test_slow_tool_sleeps_for_requested_duration() -> None:
    start = time.perf_counter()
    result = SlowTool().invoke(delay_s=0.1)
    elapsed = time.perf_counter() - start
    assert result.ok is True
    assert elapsed >= 0.09


def test_rate_limit_tool_fails_then_succeeds() -> None:
    tool = FlakyRateLimitTool(fail_times=2)
    first = tool.invoke()
    second = tool.invoke()
    third = tool.invoke()
    assert first.ok is False and first.error_kind is ToolErrorKind.RATE_LIMITED
    assert second.ok is False and second.error_kind is ToolErrorKind.RATE_LIMITED
    assert third.ok is True
    assert third.value == {"calls": 3}


def test_idempotent_tool_deduplicates_side_effects() -> None:
    tool = IdempotentTool()
    first = tool.invoke(idempotency_key="order-1")
    second = tool.invoke(idempotency_key="order-1")
    assert first.value == second.value
    assert first.value["side_effect_count"] == 1
    assert tool.side_effects == ["order-1"]


def test_partial_failure_tool_rolls_back_completed_steps() -> None:
    tool = PartialFailureTool()
    result = tool.invoke(steps=["reserve", "capture", "settle"], fail_at="capture")
    assert result.ok is False
    assert result.error_kind is ToolErrorKind.UPSTREAM
    assert result.value == {"rolled_back": ["reserve"], "remaining": []}
    assert tool.applied == []


def test_partial_failure_tool_succeeds_when_no_step_fails() -> None:
    tool = PartialFailureTool()
    result = tool.invoke(steps=["reserve", "capture"], fail_at="never")
    assert result.ok is True
    assert result.value == {"applied": ["reserve", "capture"]}


def test_demo_registry_exposes_expected_tools() -> None:
    registry = build_demo_registry()
    assert registry.names() == [
        "echo_tool",
        "idempotent_tool",
        "payout_tool",
        "rate_limited_tool",
        "slow_tool",
    ]
