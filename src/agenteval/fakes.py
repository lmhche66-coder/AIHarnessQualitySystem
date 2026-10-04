"""故障注入工具桩件。

超时、限流、部分失败无法用真实服务稳定复现。这些桩件既用于平台自测，也作为
示例注册表，让契约用例在无外部依赖的前提下可重复执行。
"""

from __future__ import annotations

import time
from typing import Any

from agenteval.tools import SchemaTool, ToolErrorKind, ToolRegistry, ToolResult


class EchoTool(SchemaTool):
    """带必填参数与类型约束的示例工具，用于参数校验类契约。"""

    name = "echo_tool"
    input_schema = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["message"],
        "additionalProperties": False,
    }

    def run(self, message: str, count: int = 1, **kwargs: Any) -> ToolResult:
        return ToolResult(ok=True, value={"message": message * count})


class SlowTool(SchemaTool):
    """按声明时长睡眠的工具，用于超时契约。"""

    name = "slow_tool"
    input_schema = {
        "type": "object",
        "properties": {"delay_s": {"type": "number", "minimum": 0}},
        "required": ["delay_s"],
        "additionalProperties": False,
    }

    def __init__(self, default_delay_s: float = 0.5) -> None:
        self.default_delay_s = default_delay_s

    def run(self, delay_s: float | None = None, **kwargs: Any) -> ToolResult:
        delay = self.default_delay_s if delay_s is None else float(delay_s)
        time.sleep(delay)
        return ToolResult(ok=True, value={"slept_s": delay})


class FlakyRateLimitTool(SchemaTool):
    """前若干次调用返回限流错误，之后成功，用于限流重试契约。"""

    name = "rate_limited_tool"
    input_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, fail_times: int = 2) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def run(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            return ToolResult(
                ok=False,
                error_kind=ToolErrorKind.RATE_LIMITED,
                error_message=f"rate limited on attempt {self.calls}",
                attempts=self.calls,
            )
        return ToolResult(ok=True, value={"calls": self.calls}, attempts=self.calls)


class IdempotentTool(SchemaTool):
    """按幂等键去重的工具，用于幂等重试契约。

    返回值中必须包含 ``side_effect_count``，否则断言无法观察副作用，会被判为失败。
    """

    name = "idempotent_tool"
    input_schema = {
        "type": "object",
        "properties": {"idempotency_key": {"type": "string"}},
        "required": ["idempotency_key"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.side_effects: list[str] = []
        self._results: dict[str, dict[str, Any]] = {}

    def run(self, idempotency_key: str, **kwargs: Any) -> ToolResult:
        if idempotency_key not in self._results:
            self.side_effects.append(idempotency_key)
            self._results[idempotency_key] = {
                "charge": idempotency_key,
                "side_effect_count": len(self.side_effects),
            }
        return ToolResult(ok=True, value=dict(self._results[idempotency_key]))


class PartialFailureTool(SchemaTool):
    """多步操作中途中止并回滚已完成步骤，用于部分失败回滚契约。

    失败时返回值中必须包含 ``rolled_back`` 与 ``remaining``，供断言观察回滚结果。
    """

    name = "payout_tool"
    input_schema = {
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "fail_at": {"type": "string"},
        },
        "required": ["steps", "fail_at"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.applied: list[str] = []
        self.rolled_back: list[str] = []

    def run(self, steps: list[str], fail_at: str, **kwargs: Any) -> ToolResult:
        self.applied = []
        self.rolled_back = []
        for step in steps:
            if step == fail_at:
                self.rolled_back = list(reversed(self.applied))
                self.applied = []
                return ToolResult(
                    ok=False,
                    error_kind=ToolErrorKind.UPSTREAM,
                    error_message=f"step failed: {step}",
                    value={"rolled_back": list(self.rolled_back), "remaining": []},
                )
            self.applied.append(step)
        return ToolResult(ok=True, value={"applied": list(self.applied)})


def build_demo_registry() -> ToolRegistry:
    """构造示例注册表，供 CLI 的 ``--demo`` 与端到端测试使用。"""

    return ToolRegistry(
        [
            EchoTool(),
            SlowTool(),
            FlakyRateLimitTool(fail_times=2),
            IdempotentTool(),
            PartialFailureTool(),
        ]
    )
