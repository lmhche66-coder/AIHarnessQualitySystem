"""示例被测 agent，供 ``--agent examples/demo_agent.py:build_agent`` 使用。

agent 只依赖平台交给它的环境与任务定义，不把解法写进任务。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_ATTEMPT_COUNTER = {"count": 0}


def build_agent() -> Callable[[Any, Any], None]:
    def run(registry: Any, task: Any) -> None:
        ledger = registry.get("ledger_tool")
        if task.id == "task-charge-and-settle":
            ledger.invoke(op="charge", amount=30)
            ledger.invoke(op="settle")
        elif task.id == "task-refund-reduces-balance":
            ledger.invoke(op="charge", amount=100)
            ledger.invoke(op="refund", amount=30)
        else:
            raise RuntimeError(f"demo agent has no plan for task: {task.id}")

    return run


def build_flaky_agent() -> Callable[[Any, Any], None]:
    """第一次尝试故意不完成，用于演示 pass@k 与 pass@1 的差异。"""

    def run(registry: Any, task: Any) -> None:
        _ATTEMPT_COUNTER["count"] += 1
        if _ATTEMPT_COUNTER["count"] == 1:
            return
        ledger = registry.get("ledger_tool")
        ledger.invoke(op="charge", amount=30)
        ledger.invoke(op="settle")

    return run
