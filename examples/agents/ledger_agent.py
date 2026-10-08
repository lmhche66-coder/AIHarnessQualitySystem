"""示例 subprocess agent：工具属于 agent 自己（文章 §2.1）。

agent 自己执行账本工具，只把「已经执行过的调用」回报给平台；平台不派发工具、也不
代执行。整个 agent 不认识平台的 Python 类型，只说 bridge 协议。
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _call(op: str, amount: int | None = None, duration_ms: float = 10.0) -> dict[str, Any]:
    arguments: dict[str, Any] = {"op": op}
    if amount is not None:
        arguments["amount"] = amount
    return {
        "name": "ledger_tool",
        "arguments": arguments,
        "ok": True,
        "duration_ms": duration_ms,
    }


def decide(request: dict[str, Any]) -> dict[str, Any]:
    case = request.get("case") or {}
    task_id = case.get("id")
    if task_id == "task-charge-and-settle":
        return {
            "output": "已扣款 30 元并完成结算，账本余额 30 元。",
            "tool_calls": [
                _call("charge", 30, 12.0),
                _call("settle", None, 6.0),
            ],
            "usage": {"input_tokens": 200, "output_tokens": 40, "model_calls": 2},
        }
    if task_id == "task-refund-reduces-balance":
        return {
            "output": "已扣款 100 元并退款 30 元，账本余额 70 元。",
            "tool_calls": [
                _call("charge", 100, 10.0),
                _call("refund", 30, 9.0),
            ],
            "usage": {"input_tokens": 220, "output_tokens": 45, "model_calls": 2},
        }
    return {"output": f"没有针对 {task_id} 的方案。"}


def main() -> None:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        request = json.loads(stripped)
        response = {"protocol": "agenteval.bridge/1", **decide(request)}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return
    return


if __name__ == "__main__":
    main()
