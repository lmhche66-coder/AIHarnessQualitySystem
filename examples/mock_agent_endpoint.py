"""示例被测目标：返回带用量字段的 JSON，供压测演示与 CI 使用。

用法::

    python examples/mock_agent_endpoint.py 8799
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LATENCY_S = 0.05
BODY = json.dumps(
    {
        "answer": "根因：电极接触阻抗偏高。",
        "usage": {
            "llm_calls": 3,
            "tool_calls": 5,
            "prompt_tokens": 820,
            "completion_tokens": 140,
        },
    },
    ensure_ascii=False,
).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        time.sleep(LATENCY_S)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, format: str, *args: object) -> None:
        """保持安静。"""


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
