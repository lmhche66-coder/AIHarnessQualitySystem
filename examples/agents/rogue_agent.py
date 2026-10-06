"""示例选择 agent：故意选错工具，用来验证选择打分能透过 Bridge 把它拦下。"""

from __future__ import annotations

import json
import sys
from typing import Any


def decide(request: dict[str, Any]) -> dict[str, Any]:
    case = request.get("case") or {}
    if case.get("id") == "single-correct":
        return {
            "tool_calls": [
                {"name": "send_email", "arguments": {"to": "someone@example.com"}}
            ]
        }
    return {"tool_calls": []}


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
