"""控制台的只读 HTTP 服务。

只读取既有产物，不写入、不删除。默认绑定回环地址，因为运行记录里可能含敏感
参数与结果。
"""

from __future__ import annotations

import json
from importlib import resources
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from agenteval.models import Run
from agenteval.store import SUMMARY_FILENAME, RunStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_RUNS = 200

STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

STATIC_PATHS = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/style.css": "style.css",
}


def _asset_bytes(name: str) -> bytes:
    return (resources.files("agenteval.console") / name).read_bytes()


def _load_summary(store: RunStore, run_id: str) -> Run | None:
    path = store.run_dir(run_id) / SUMMARY_FILENAME
    if not path.is_file():
        return None
    try:
        return Run.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def list_runs(store: RunStore, limit: int = MAX_RUNS) -> list[dict[str, Any]]:
    """按开始时间倒序列出运行；只读汇总文件，不加载判定与轨迹。"""

    entries: list[dict[str, Any]] = []
    for run_id in store.list_runs():
        run = _load_summary(store, run_id)
        if run is None:
            continue
        entries.append(_summary_payload(run))
    entries.sort(key=lambda item: item["started_at"], reverse=True)
    return entries[:limit]


def _summary_payload(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "summary": run.summary.model_dump(),
        "metadata": run.metadata,
    }


def run_detail_payload(store: RunStore, run_id: str) -> dict[str, Any]:
    run = store.load(run_id)
    return {
        **_summary_payload(run),
        "verdicts": [
            {
                "case_id": verdict.case_id,
                "status": verdict.status.value,
                "duration_ms": verdict.duration_ms,
                "error": verdict.error,
                "metrics": verdict.metrics.model_dump(),
                "checks": [check.model_dump() for check in verdict.checks],
                "failed_checks": [check.model_dump() for check in verdict.failed_checks],
            }
            for verdict in run.verdicts
        ],
    }


def trace_payload(store: RunStore, run_id: str, case_id: str) -> dict[str, Any]:
    run = store.load(run_id)
    for trace in run.traces:
        if trace.case_id == case_id:
            return {
                "case_id": case_id,
                "events": [
                    {
                        "seq": event.seq,
                        "name": event.name,
                        "at": event.at.isoformat(),
                        "payload": event.payload,
                        "error": event.error,
                    }
                    for event in trace.events
                ],
            }
    raise KeyError(f"run {run_id} has no trace for case: {case_id}")


def create_handler(store: RunStore) -> type[BaseHTTPRequestHandler]:
    """构造绑定到指定存储的请求处理器，便于测试直接注入临时目录。"""

    class ConsoleHandler(BaseHTTPRequestHandler):
        server_version = "agenteval-console"

        def do_GET(self) -> None:  # noqa: N802 - http.server 约定
            self._route(urlparse(self.path).path)

        def do_POST(self) -> None:  # noqa: N802
            self._reject()

        def do_PUT(self) -> None:  # noqa: N802
            self._reject()

        def do_PATCH(self) -> None:  # noqa: N802
            self._reject()

        def do_DELETE(self) -> None:  # noqa: N802
            self._reject()

        def log_message(self, format: str, *args: Any) -> None:
            """保持安静：控制台是给人看的，不要往终端刷访问日志。"""

        def _reject(self) -> None:
            self._send_json({"error": "console is read-only"}, status=405)

        def _route(self, path: str) -> None:
            asset = STATIC_PATHS.get(path)
            if asset is not None:
                self._send_bytes(_asset_bytes(asset), STATIC_TYPES[asset])
                return
            parts = [unquote(part) for part in path.strip("/").split("/") if part]
            try:
                if parts == ["api", "runs"]:
                    self._send_json({"runs": list_runs(store)})
                    return
                if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    self._send_json(run_detail_payload(store, parts[2]))
                    return
                if len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "traces":
                    self._send_json(trace_payload(store, parts[2], parts[4]))
                    return
            except FileNotFoundError as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            except KeyError as exc:
                message = exc.args[0] if exc.args else str(exc)
                self._send_json({"error": message}, status=404)
                return
            self._send_json({"error": "not found"}, status=404)

        def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(body, "application/json; charset=utf-8", status)

    return ConsoleHandler


def create_server(
    store: RunStore,
    host: str = DEFAULT_HOST,
    port: int = 0,
) -> ThreadingHTTPServer:
    """创建控制台服务器；端口为 0 时由系统分配，便于测试。"""

    return ThreadingHTTPServer((host, port), create_handler(store))
