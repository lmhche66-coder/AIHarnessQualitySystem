"""示例被测 agent，供 ``--agent examples/demo_agent.py:build_agent`` 使用。

agent 只依赖平台交给它的环境与任务定义，不把解法写进任务。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


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
