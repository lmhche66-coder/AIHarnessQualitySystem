"""最小 OTLP/HTTP 接收端，用于本地确认导出内容。

用法::

    python tools/otlp_receiver.py 4318
    python -m agenteval otel export --run <run_id> --endpoint http://127.0.0.1:4318/v1/traces

它只打印收到的 span 摘要，不做存储。真正的 trace 后端请用 Langfuse、Opik 或 Phoenix。
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            print(f"[{self.path}] body is not JSON")
            payload = None
        if payload is not None:
            print(f"[{self.path}] received", flush=True)
            for resource in payload.get("resourceSpans", []):
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        print(
                            f"  {span.get('name'):<32} "
                            f"trace={span.get('traceId', '')[:8]} "
                            f"span={span.get('spanId', '')[:8]} "
                            f"parent={(span.get('parentSpanId') or '-')[:8]} "
                            f"status={(span.get('status') or {}).get('code')}",
                            flush=True,
                        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        """保持安静，只打印 span 摘要。"""


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 4318
    print(f"OTLP receiver listening on http://127.0.0.1:{port}/v1/traces")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
