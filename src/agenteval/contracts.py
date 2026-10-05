"""工具契约的确定性断言引擎。

每个检查函数返回一组 :class:`CheckOutcome`。断言只观察工具的外部行为
（结构化结果、错误类别、耗时、副作用计数），不依赖工具内部实现。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any, Callable

from agenteval.models import (
    Case,
    CheckOutcome,
    IdempotencyCheck,
    MissingRequiredCheck,
    RateLimitCheck,
    RollbackCheck,
    Status,
    TimeoutCheck,
    Trace,
    Verdict,
    WrongTypeCheck,
)
from agenteval.process import record_tool_call
from agenteval.tools import Tool, ToolErrorKind, ToolResult


@dataclass
class Invocation:
    """一次工具调用的观察结果。"""

    result: ToolResult | None = None
    exception: Exception | None = None
    timed_out: bool = False
    duration_ms: float = 0.0


def check_case(case: Case, tool: Tool, trace: Trace) -> Verdict:
    """对单个用例执行契约断言。"""

    handler = _HANDLERS.get(case.check.kind)
    if handler is None:
        return Verdict(
            case_id=case.id,
            status=Status.ERROR,
            error=f"unsupported check kind: {case.check.kind}",
        )
    checks = handler(case, tool, trace)
    status = Status.PASS if checks and all(check.passed for check in checks) else Status.FAIL
    return Verdict(case_id=case.id, status=status, checks=checks)


def _check_missing_required(case: Case, tool: Tool, trace: Trace) -> list[CheckOutcome]:
    spec: MissingRequiredCheck = case.check  # type: ignore[assignment]
    if not spec.drop:
        return [
            CheckOutcome(
                name="missing_required.configured",
                passed=False,
                expected="a non-empty drop list",
                actual=spec.drop,
                message="case does not declare which parameters to drop",
            )
        ]
    args = dict(case.input)
    for name in spec.drop:
        args.pop(name, None)
    trace.record("invoke_missing_required", payload={"args": args, "dropped": list(spec.drop)})
    invocation = _invoke(tool, args, trace=trace)
    return [
        _expect_structured_error(
            invocation,
            name="missing_required.structured_error",
            expected_kind=ToolErrorKind.VALIDATION,
            expected_field=spec.drop[0],
        )
    ]


def _check_wrong_type(case: Case, tool: Tool, trace: Trace) -> list[CheckOutcome]:
    spec: WrongTypeCheck = case.check  # type: ignore[assignment]
    if not spec.overrides:
        return [
            CheckOutcome(
                name="wrong_type.configured",
                passed=False,
                expected="a non-empty overrides mapping",
                actual=spec.overrides,
                message="case does not declare any wrong-typed parameters",
            )
        ]
    args = dict(case.input)
    args.update(spec.overrides)
    trace.record("invoke_wrong_type", payload={"args": args, "overrides": dict(spec.overrides)})
    invocation = _invoke(tool, args, trace=trace)
    return [
        _expect_structured_error(
            invocation,
            name="wrong_type.structured_error",
            expected_kind=ToolErrorKind.VALIDATION,
            expected_field=next(iter(spec.overrides)),
        )
    ]


def _check_timeout(case: Case, tool: Tool, trace: Trace) -> list[CheckOutcome]:
    spec: TimeoutCheck = case.check  # type: ignore[assignment]
    invocation = _invoke(tool, dict(case.input), timeout_s=spec.timeout_s, trace=trace)
    elapsed_s = invocation.duration_ms / 1000
    trace.record(
        "invoke_timeout",
        payload={"timeout_s": spec.timeout_s, "elapsed_s": round(elapsed_s, 6)},
        error=None if invocation.timed_out else "tool returned before the timeout",
    )
    grace_s = max(0.05, spec.timeout_s * 0.5)
    within_allowance = elapsed_s <= spec.timeout_s + grace_s
    passed = invocation.timed_out and within_allowance
    if not invocation.timed_out:
        message = "tool finished before the timeout; the case does not exercise the timeout path"
    elif not within_allowance:
        message = "timeout was reported later than the agreed allowance"
    else:
        message = None
    return [
        CheckOutcome(
            name="timeout.bounded",
            passed=passed,
            expected={"timed_out": True, "within_s": round(spec.timeout_s + grace_s, 6)},
            actual={"timed_out": invocation.timed_out, "elapsed_s": round(elapsed_s, 6)},
            message=message,
        )
    ]


def _check_rate_limit(case: Case, tool: Tool, trace: Trace) -> list[CheckOutcome]:
    spec: RateLimitCheck = case.check  # type: ignore[assignment]
    attempts = 0
    last: ToolResult | None = None
    failure: Exception | None = None
    for attempt in range(1, spec.max_attempts + 1):
        attempts = attempt
        invocation = _invoke(tool, dict(case.input), trace=trace)
        if invocation.exception is not None:
            failure = invocation.exception
            break
        last = invocation.result
        if last is not None and (last.ok or last.error_kind != ToolErrorKind.RATE_LIMITED):
            break
    trace.attempts = attempts
    trace.record(
        "retry_loop",
        payload={"attempts": attempts, "max_attempts": spec.max_attempts, "expect_success": spec.expect_success},
    )
    if failure is not None or last is None:
        detail = f"{type(failure).__name__}: {failure}" if failure else "no call was made"
        return [
            CheckOutcome(
                name="rate_limit.terminal_state",
                passed=False,
                expected="a structured ToolResult",
                actual=detail,
                message="tool raised instead of returning a structured result",
            )
        ]
    if spec.expect_success:
        terminal_ok = last.ok
        expected_terminal: Any = {"ok": True}
        actual_terminal: Any = {"ok": last.ok, "error_kind": _kind_value(last.error_kind)}
    else:
        terminal_ok = (not last.ok) and last.error_kind == ToolErrorKind.RATE_LIMITED
        expected_terminal = {"ok": False, "error_kind": ToolErrorKind.RATE_LIMITED.value}
        actual_terminal = {"ok": last.ok, "error_kind": _kind_value(last.error_kind)}
    return [
        CheckOutcome(
            name="rate_limit.terminal_state",
            passed=terminal_ok,
            expected=expected_terminal,
            actual=actual_terminal,
        ),
        CheckOutcome(
            name="rate_limit.attempts_bounded",
            passed=attempts <= spec.max_attempts,
            expected=f"<= {spec.max_attempts}",
            actual=attempts,
        ),
    ]


def _check_idempotent(case: Case, tool: Tool, trace: Trace) -> list[CheckOutcome]:
    spec: IdempotencyCheck = case.check  # type: ignore[assignment]
    args = dict(case.input)
    args["idempotency_key"] = spec.idempotency_key
    results: list[ToolResult] = []
    for _ in range(spec.repeat):
        invocation = _invoke(tool, args, trace=trace)
        if invocation.exception is not None:
            return [
                CheckOutcome(
                    name="idempotent_retry.stable_result",
                    passed=False,
                    expected="a structured ToolResult",
                    actual=f"{type(invocation.exception).__name__}: {invocation.exception}",
                    message="tool raised instead of returning a structured result",
                )
            ]
        result = invocation.result
        if result is None or not result.ok:
            return [
                CheckOutcome(
                    name="idempotent_retry.stable_result",
                    passed=False,
                    expected={"ok": True},
                    actual={"ok": None if result is None else result.ok},
                    message="repeated call did not succeed",
                )
            ]
        results.append(result)
    trace.record("idempotent_calls", payload={"repeat": spec.repeat, "idempotency_key": spec.idempotency_key})
    first = results[0]
    stable = all(result.value == first.value for result in results[1:])
    count = first.value.get("side_effect_count") if isinstance(first.value, dict) else None
    single_effect = count == 1
    if isinstance(count, int):
        trace.side_effects.append(spec.idempotency_key)
    message = None
    if not single_effect:
        message = (
            "tool must report an integer 'side_effect_count' in its value; "
            "the contract treats unobservable effects as a failure"
        )
    return [
        CheckOutcome(
            name="idempotent_retry.stable_result",
            passed=stable,
            expected="identical value across repeated calls",
            actual=[result.value for result in results],
        ),
        CheckOutcome(
            name="idempotent_retry.single_side_effect",
            passed=single_effect,
            expected=1,
            actual=count,
            message=message,
        ),
    ]


def _check_rollback(case: Case, tool: Tool, trace: Trace) -> list[CheckOutcome]:
    spec: RollbackCheck = case.check  # type: ignore[assignment]
    if not spec.steps or spec.fail_at not in spec.steps:
        return [
            CheckOutcome(
                name="partial_rollback.configured",
                passed=False,
                expected="fail_at is one of steps",
                actual={"steps": spec.steps, "fail_at": spec.fail_at},
            )
        ]
    prefix = spec.steps[: spec.steps.index(spec.fail_at)]
    args = dict(case.input)
    args["steps"] = list(spec.steps)
    args["fail_at"] = spec.fail_at
    invocation = _invoke(tool, args, trace=trace)
    trace.record("invoke_rollback", payload={"steps": list(spec.steps), "fail_at": spec.fail_at})
    if invocation.exception is not None:
        return [
            CheckOutcome(
                name="partial_rollback.terminal_state",
                passed=False,
                expected="a structured failure result",
                actual=f"{type(invocation.exception).__name__}: {invocation.exception}",
                message="tool raised instead of reporting rollback through its result",
            )
        ]
    result = invocation.result
    if result is None:
        return [
            CheckOutcome(
                name="partial_rollback.terminal_state",
                passed=False,
                expected="a structured failure result",
                actual=None,
            )
        ]
    value = result.value if isinstance(result.value, dict) else {}
    rolled_back = value.get("rolled_back")
    remaining = value.get("remaining")
    rolled_matches = sorted(rolled_back or []) == sorted(prefix)
    net_empty = not remaining
    trace.side_effects.extend(list(rolled_back or []))
    message = None
    if not rolled_matches:
        message = "tool must report the rolled-back steps as 'rolled_back' in its value"
    elif not net_empty:
        message = "tool must report an empty 'remaining' list after rollback"
    return [
        CheckOutcome(
            name="partial_rollback.failure_reported",
            passed=not result.ok,
            expected={"ok": False},
            actual={"ok": result.ok, "error_kind": _kind_value(result.error_kind)},
        ),
        CheckOutcome(
            name="partial_rollback.rolled_back",
            passed=rolled_matches,
            expected=sorted(prefix),
            actual=sorted(rolled_back or []),
            message=message if not rolled_matches else None,
        ),
        CheckOutcome(
            name="partial_rollback.net_effects_empty",
            passed=net_empty,
            expected=[],
            actual=remaining,
            message=message if rolled_matches and not net_empty else None,
        ),
    ]


def _expect_structured_error(
    invocation: Invocation,
    name: str,
    expected_kind: ToolErrorKind,
    expected_field: str | None,
) -> CheckOutcome:
    expected = {"ok": False, "error_kind": expected_kind.value, "error_field": expected_field}
    if invocation.exception is not None:
        return CheckOutcome(
            name=name,
            passed=False,
            expected=expected,
            actual=f"unhandled {type(invocation.exception).__name__}: {invocation.exception}",
            message="tool must return a structured error instead of raising",
        )
    result = invocation.result
    if result is None:
        return CheckOutcome(name=name, passed=False, expected=expected, actual=None)
    problems: list[str] = []
    if result.ok:
        problems.append("tool accepted the call instead of rejecting it")
    if result.error_kind != expected_kind:
        problems.append(f"error_kind={_kind_value(result.error_kind)}")
    if expected_field is not None and result.error_field != expected_field:
        problems.append(f"error_field={result.error_field!r}, expected {expected_field!r}")
    return CheckOutcome(
        name=name,
        passed=not problems,
        expected=expected,
        actual={
            "ok": result.ok,
            "error_kind": _kind_value(result.error_kind),
            "error_field": result.error_field,
        },
        message="; ".join(problems) if problems else None,
    )


def _invoke(
    tool: Tool,
    args: dict[str, Any],
    timeout_s: float | None = None,
    trace: Trace | None = None,
) -> Invocation:
    """调用工具并把异常、超时统一转换为观察结果。"""

    start = time.perf_counter()
    if timeout_s is None:
        try:
            result = tool.invoke(**args)
        except Exception as exc:  # noqa: BLE001 - 任意异常都要转成判定结果
            return Invocation(exception=exc, duration_ms=_elapsed_ms(start))
        if trace is not None:
            record_tool_call(trace, tool.name, args, result)
        return Invocation(result=result, duration_ms=_elapsed_ms(start))

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(_call, tool, args)
    try:
        result = future.result(timeout=timeout_s)
    except FuturesTimeoutError:
        future.cancel()
        return Invocation(
            exception=TimeoutError(f"call exceeded {timeout_s}s"),
            timed_out=True,
            duration_ms=_elapsed_ms(start),
        )
    except Exception as exc:  # noqa: BLE001 - 任意异常都要转成判定结果
        return Invocation(exception=exc, duration_ms=_elapsed_ms(start))
    else:
        if trace is not None:
            record_tool_call(trace, tool.name, args, result)
        return Invocation(result=result, duration_ms=_elapsed_ms(start))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _call(tool: Tool, args: dict[str, Any]) -> ToolResult:
    return tool.invoke(**args)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def _kind_value(kind: ToolErrorKind | None) -> str | None:
    return kind.value if isinstance(kind, ToolErrorKind) else kind


_HANDLERS: dict[str, Callable[[Case, Tool, Trace], list[CheckOutcome]]] = {
    "missing_required": _check_missing_required,
    "wrong_type": _check_wrong_type,
    "timeout": _check_timeout,
    "rate_limit": _check_rate_limit,
    "idempotent_retry": _check_idempotent,
    "partial_rollback": _check_rollback,
}
