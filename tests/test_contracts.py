from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agenteval.contracts import check_case
from agenteval.fakes import (
    EchoTool,
    FlakyRateLimitTool,
    IdempotentTool,
    PartialFailureTool,
    SlowTool,
)
from agenteval.models import Case, Status, Trace
from agenteval.tools import ToolErrorKind, ToolResult


def run_check(case: Case, tool: Any) -> tuple[Status, list[str]]:
    trace = Trace(case_id=case.id)
    verdict = check_case(case, tool, trace)
    return verdict.status, [check.name for check in verdict.failed_checks]


def test_missing_required_passes_for_validating_tool() -> None:
    case = Case.model_validate(
        {
            "id": "c1",
            "target": "echo_tool",
            "input": {"message": "hi"},
            "check": {"kind": "missing_required", "drop": ["message"]},
        }
    )
    status, failed = run_check(case, EchoTool())
    assert status is Status.PASS
    assert failed == []


def test_missing_required_fails_when_tool_accepts_bad_input() -> None:
    class PermissiveTool:
        name = "permissive"
        input_schema = {"type": "object"}

        def invoke(self, **kwargs: object) -> ToolResult:
            return ToolResult(ok=True, value={})

    case = Case.model_validate(
        {
            "id": "c1",
            "target": "permissive",
            "input": {"message": "hi"},
            "check": {"kind": "missing_required", "drop": ["message"]},
        }
    )
    status, failed = run_check(case, PermissiveTool())
    assert status is Status.FAIL
    assert failed == ["missing_required.structured_error"]


def test_missing_required_fails_when_tool_raises() -> None:
    class RaisingTool:
        name = "raising"
        input_schema = {"type": "object"}

        def invoke(self, **kwargs: object) -> ToolResult:
            raise RuntimeError("boom")

    case = Case.model_validate(
        {
            "id": "c1b",
            "target": "raising",
            "input": {"message": "hi"},
            "check": {"kind": "missing_required", "drop": ["message"]},
        }
    )
    status, failed = run_check(case, RaisingTool())
    assert status is Status.FAIL
    assert failed == ["missing_required.structured_error"]


def test_wrong_type_passes_for_validating_tool() -> None:
    case = Case.model_validate(
        {
            "id": "c2",
            "target": "echo_tool",
            "input": {"message": "hi"},
            "check": {"kind": "wrong_type", "overrides": {"count": "not-an-integer"}},
        }
    )
    status, failed = run_check(case, EchoTool())
    assert status is Status.PASS
    assert failed == []


def test_timeout_passes_when_tool_exceeds_threshold() -> None:
    case = Case.model_validate(
        {
            "id": "c3",
            "target": "slow_tool",
            "input": {"delay_s": 0.4},
            "check": {"kind": "timeout", "timeout_s": 0.1},
        }
    )
    status, failed = run_check(case, SlowTool())
    assert status is Status.PASS
    assert failed == []


def test_timeout_fails_when_tool_finishes_early() -> None:
    case = Case.model_validate(
        {
            "id": "c3",
            "target": "slow_tool",
            "input": {"delay_s": 0.0},
            "check": {"kind": "timeout", "timeout_s": 0.2},
        }
    )
    status, failed = run_check(case, SlowTool())
    assert status is Status.FAIL
    assert failed == ["timeout.bounded"]


def test_rate_limit_passes_when_retry_eventually_succeeds() -> None:
    case = Case.model_validate(
        {
            "id": "c4",
            "target": "rate_limited_tool",
            "input": {},
            "check": {"kind": "rate_limit", "max_attempts": 5, "expect_success": True},
        }
    )
    status, failed = run_check(case, FlakyRateLimitTool(fail_times=2))
    assert status is Status.PASS
    assert failed == []


def test_rate_limit_passes_when_retries_are_exhausted() -> None:
    case = Case.model_validate(
        {
            "id": "c5",
            "target": "rate_limited_tool",
            "input": {},
            "check": {"kind": "rate_limit", "max_attempts": 3, "expect_success": False},
        }
    )
    trace = Trace(case_id=case.id)
    verdict = check_case(case, FlakyRateLimitTool(fail_times=99), trace)
    assert verdict.status is Status.PASS
    assert trace.attempts == 3


def test_rate_limit_fails_when_terminal_state_is_wrong() -> None:
    case = Case.model_validate(
        {
            "id": "c5b",
            "target": "rate_limited_tool",
            "input": {},
            "check": {"kind": "rate_limit", "max_attempts": 5, "expect_success": True},
        }
    )
    status, failed = run_check(case, FlakyRateLimitTool(fail_times=99))
    assert status is Status.FAIL
    assert "rate_limit.terminal_state" in failed


def test_idempotent_retry_passes_for_deduplicating_tool() -> None:
    case = Case.model_validate(
        {
            "id": "c6",
            "target": "idempotent_tool",
            "input": {},
            "check": {"kind": "idempotent_retry", "idempotency_key": "order-1"},
        }
    )
    status, failed = run_check(case, IdempotentTool())
    assert status is Status.PASS
    assert failed == []


def test_idempotent_retry_fails_when_effects_are_not_observable() -> None:
    class OpaqueTool:
        name = "opaque"
        input_schema = {"type": "object"}

        def invoke(self, **kwargs: object) -> ToolResult:
            return ToolResult(ok=True, value={"charge": kwargs.get("idempotency_key")})

    case = Case.model_validate(
        {
            "id": "c6b",
            "target": "opaque",
            "input": {},
            "check": {"kind": "idempotent_retry", "idempotency_key": "order-1"},
        }
    )
    status, failed = run_check(case, OpaqueTool())
    assert status is Status.FAIL
    assert failed == ["idempotent_retry.single_side_effect"]


def test_partial_rollback_passes_for_compensating_tool() -> None:
    case = Case.model_validate(
        {
            "id": "c7",
            "target": "payout_tool",
            "input": {},
            "check": {
                "kind": "partial_rollback",
                "steps": ["reserve", "capture", "settle"],
                "fail_at": "capture",
            },
        }
    )
    status, failed = run_check(case, PartialFailureTool())
    assert status is Status.PASS
    assert failed == []


def test_partial_rollback_fails_when_tool_leaves_net_effects() -> None:
    class LeakyTool:
        name = "leaky"
        input_schema = {"type": "object"}

        def invoke(self, **kwargs: object) -> ToolResult:
            return ToolResult(
                ok=False,
                error_kind=ToolErrorKind.UPSTREAM,
                error_message="failed",
                value={"rolled_back": [], "remaining": ["reserve"]},
            )

    case = Case.model_validate(
        {
            "id": "c7b",
            "target": "leaky",
            "input": {},
            "check": {
                "kind": "partial_rollback",
                "steps": ["reserve", "capture"],
                "fail_at": "capture",
            },
        }
    )
    status, failed = run_check(case, LeakyTool())
    assert status is Status.FAIL
    assert "partial_rollback.rolled_back" in failed


def test_unsupported_check_kind_reports_error() -> None:
    case = Case.model_construct(
        id="c8",
        target="echo_tool",
        input={},
        check=SimpleNamespace(kind="no_such_check"),
    )
    trace = Trace(case_id=case.id)
    verdict = check_case(case, EchoTool(), trace)
    assert verdict.status is Status.ERROR
    assert verdict.error is not None and "no_such_check" in verdict.error
