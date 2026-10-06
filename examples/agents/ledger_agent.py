"""示例 subprocess agent：读一行 Bridge 请求，写一行 Bridge 响应。

它演示平台执行模式：agent 只返回 tool_calls，工具由平台调用并把结果回传，
agent 在下一轮看到结果后收尾。整个 agent 不认识平台的 Python 类型，只说协议。
"""

from __future__ import annotations

import json
import sys
from typing import Any


def decide(request: dict[str, Any]) -> dict[str, Any]:
    case = request.get("case") or {}
    messages = request.get("messages") or []
    if any(message.get("role") == "tool" for message in messages):
        return {"output": "已完成。"}
    task_id = case.get("id")
    if task_id == "task-charge-and-settle":
        return {
            "output": "",
            "tool_calls": [
                {"name": "ledger_tool", "arguments": {"op": "charge", "amount": 30}},
                {"name": "ledger_tool", "arguments": {"op": "settle"}},
            ],
            "usage": {"input_tokens": 200, "output_tokens": 40},
        }
    if task_id == "task-refund-reduces-balance":
        return {
            "output": "",
            "tool_calls": [
                {"name": "ledger_tool", "arguments": {"op": "charge", "amount": 100}},
                {"name": "ledger_tool", "arguments": {"op": "refund", "amount": 30}},
            ],
            "usage": {"input_tokens": 220, "output_tokens": 45},
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


if __name__ == "__main__":
    main()
