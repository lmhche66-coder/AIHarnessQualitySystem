"""示例 subprocess agent：在桥接响应里上报感知/规划/记忆/检索模块信号。

对照文章的 EvalTrace：agent 的每个节点把自己的执行信息写进 ``signals``，平台无需
从最终回复里反推，就能对四个核心模块做断言与诊断。这里用固定的假信号演示协议，
真实 agent 只需在对应节点填上真实值。
"""

from __future__ import annotations

import json
import sys
from typing import Any


def decide(request: dict[str, Any]) -> dict[str, Any]:
    case = request.get("case") or {}
    task_id = case.get("id")
    if task_id == "task-trace-skill-hit":
        return {
            "output": "已按 trace 分析工具定位到空指针。",
            "signals": [
                {
                    "module": "perception",
                    "payload": {
                        "intent": "trace_lookup",
                        "skill": "trace-analyzer",
                        "hit": True,
                    },
                    "duration_ms": 18.0,
                },
                {
                    "module": "planning",
                    "payload": {"route": "skill_hit", "tools": ["trace_query"]},
                    "duration_ms": 5.0,
                },
            ],
            "usage": {"input_tokens": 180, "output_tokens": 36, "model_calls": 2},
        }
    if task_id == "task-knowledge-qa-route-miss":
        return {
            "output": "商品 1005007651467330 在韩国不可售，原因是 sale_country_rule 命中黑名单。",
            "signals": [
                {
                    "module": "perception",
                    "payload": {"intent": "knowledge_qa", "skill": None, "hit": False},
                    "duration_ms": 15.0,
                },
                {
                    "module": "planning",
                    "payload": {"route": "skill_miss", "tools": ["repo_vector_search"]},
                    "duration_ms": 4.0,
                },
                {
                    "module": "memory",
                    "payload": {"turns": 2, "injected": ["商品 1005007651467330"]},
                    "duration_ms": 3.0,
                },
                {
                    "module": "retrieval",
                    "payload": {
                        "chunks": [
                            {"id": "chunk-sale-rule", "content": "sale_country_rule 控制可售"},
                            {"id": "chunk-blacklist", "content": "黑名单模式：列表内国家不可售"},
                        ]
                    },
                    "duration_ms": 9.0,
                },
            ],
            "usage": {"input_tokens": 260, "output_tokens": 88, "model_calls": 3},
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
