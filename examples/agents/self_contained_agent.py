"""agent 自带工具的示例 bridge agent（文章 §2.1 的形状）。

工具由 agent 自己的工具模块（MCP / Skill）提供与执行，平台不派发工具、也不执行
工具；agent 只把「已经执行过的调用」回报给平台，供过程断言与记分卡使用。
"""

from __future__ import annotations

import json
import sys
from typing import Any


def decide(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("capability") == "task":
        # agent 用自己的账本工具完成任务（这里以固定动作演示）
        return {
            "output": "已扣款 30 元并完成结算，账本余额 30 元。工具=[ledger_tool]",
            "tool_calls": [
                {
                    "name": "ledger_tool",
                    "arguments": {"op": "charge", "amount": 30},
                    "ok": True,
                    "duration_ms": 12.5,
                },
                {
                    "name": "ledger_tool",
                    "arguments": {"op": "settle"},
                    "ok": True,
                    "duration_ms": 8.0,
                },
            ],
            "signals": [
                {
                    "module": "perception",
                    "payload": {"hit": True, "skill": "ledger-skill", "intent": "charge_and_settle"},
                    "duration_ms": 14.0,
                },
                {"module": "planning", "payload": {"route": "skill_hit"}, "duration_ms": 5.0},
                {"module": "memory", "payload": {"turns": 1}, "duration_ms": 2.0},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40, "model_calls": 2},
        }
    return {
        "output": "已确认：支付网关超时。工具=[echo_tool]",
        "tool_calls": [
            {
                "name": "echo_tool",
                "arguments": {"message": "order-12345"},
                "ok": True,
                "duration_ms": 5.0,
            }
        ],
        "signals": [
            {
                "module": "perception",
                "payload": {"hit": False, "skill": None, "intent": "knowledge_qa"},
                "duration_ms": 11.0,
            },
            {"module": "planning", "payload": {"route": "skill_miss"}, "duration_ms": 3.0},
            {"module": "memory", "payload": {"turns": 2}, "duration_ms": 2.0},
            {
                "module": "retrieval",
                "payload": {
                    "count": 2,
                    "chunks": [
                        {"id": "chunk-pay-timeout", "content": "支付网关超时"},
                        {"id": "chunk-order-status", "content": "订单状态"},
                    ],
                },
                "duration_ms": 9.0,
            },
        ],
        "usage": {"input_tokens": 80, "output_tokens": 20, "model_calls": 1},
    }


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
