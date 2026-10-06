"""示例 HTTP agent：一个 endpoint 同时服务 dialogue、selection 与 redteam。

它演示「一次接入、多条评测链路复用」：平台用同一份协议调用它，只是 capability
和用例数据不同。端口可用 ``AGENTEVAL_BRIDGE_PORT`` 覆盖。
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

DEFAULT_PORT = 9210


def _dialogue(request: dict[str, Any]) -> dict[str, Any]:
    messages = request.get("messages") or []
    if not any(message.get("role") == "assistant" for message in messages):
        return {"output": "请先告诉我故障发生的时间范围。"}
    return {
        "output": "已确认：电极接触阻抗偏高。",
        "tool_calls": [
            {"name": "echo_tool", "arguments": {"message": "EEG sample loss"}}
        ],
    }


def _selection(request: dict[str, Any]) -> dict[str, Any]:
    case = request.get("case") or {}
    plans: dict[str, list[dict[str, Any]]] = {
        "single-correct": [{"name": "get_weather", "arguments": {"city": "北京"}}],
        "parallel-complete": [
            {"name": "get_weather", "arguments": {"city": "北京"}},
            {"name": "get_weather", "arguments": {"city": "上海"}},
        ],
        "irrelevant-no-call": [],
    }
    return {
        "output": "",
        "tool_calls": plans.get(case.get("id"), []),
        "usage": {"input_tokens": 150, "output_tokens": 25},
    }


def decide(request: dict[str, Any]) -> dict[str, Any]:
    capability = request.get("capability")
    if capability == "dialogue":
        return _dialogue(request)
    if capability == "selection":
        return _selection(request)
    if capability == "redteam":
        return {"output": "我不能协助该请求。", "tool_calls": []}
    return {"error": f"unsupported capability: {capability}"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 的约定
        length = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(length).decode("utf-8"))
        response = {"protocol": "agenteval.bridge/1", **decide(request)}
        body = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


def main() -> None:
    port = int(os.environ.get("AGENTEVAL_BRIDGE_PORT", str(DEFAULT_PORT)))
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
