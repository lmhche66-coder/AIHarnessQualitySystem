"""控制台的只读 HTTP 服务。

只读取既有产物，不写入、不删除。默认绑定回环地址，因为运行记录里可能含敏感
参数与结果。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import resources
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from agenteval.gate import Baseline, BaselineStore
from agenteval.models import Run
from agenteval.reports import ConclusionStore
from agenteval.store import SUMMARY_FILENAME, RunStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_RUNS = 200
DIST_DIRNAME = "dist"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _content_type(name: str) -> str:
    for suffix, content_type in CONTENT_TYPES.items():
        if name.endswith(suffix):
            return content_type
    return "application/octet-stream"


def _dist_bytes(name: str) -> bytes | None:
    """读取前端构建产物；拒绝任何带路径回溯的名字。"""

    if ".." in name:
        return None
    target = resources.files("agenteval.console") / DIST_DIRNAME / name
    try:
        if not target.is_file():
            return None
        return target.read_bytes()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


def _dist_available() -> bool:
    try:
        return (resources.files("agenteval.console") / DIST_DIRNAME).is_dir()
    except (FileNotFoundError, NotADirectoryError):
        return False


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


def baselines_payload(store: BaselineStore) -> list[dict[str, Any]]:
    """列出基线，附带其来源运行的汇总计数。"""

    entries: list[dict[str, Any]] = []
    for name in store.list_baselines():
        baseline = store.load(name)
        if baseline is None:
            continue
        counts: dict[str, int] = {}
        for status in baseline.verdicts.values():
            counts[status.value] = counts.get(status.value, 0) + 1
        entries.append(
            {
                "name": baseline.name,
                "run_id": baseline.run_id,
                "captured_at": baseline.captured_at.isoformat(),
                "cases": len(baseline.verdicts),
                "counts": counts,
            }
        )
    return entries


def create_handler(
    store: RunStore,
    *,
    reports: ConclusionStore,
    baselines: BaselineStore,
    allow_actions: bool,
) -> type[BaseHTTPRequestHandler]:
    """构造绑定到指定存储的请求处理器，便于测试直接注入临时目录。"""

    from agenteval.gate import DEFAULT_BASELINE_NAME

    class ConsoleHandler(BaseHTTPRequestHandler):
        server_version = "agenteval-console"

        def do_GET(self) -> None:  # noqa: N802 - http.server 约定
            self._route(urlparse(self.path).path)

        def do_POST(self) -> None:  # noqa: N802
            if not allow_actions:
                self._send_json(
                    {
                        "error": (
                            "this console is read-only because it is not bound to a "
                            "loopback address"
                        )
                    },
                    status=403,
                )
                return
            self._route_action(urlparse(self.path).path)

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
            asset = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
            if asset == "index.html" or asset.startswith("assets/"):
                body = _dist_bytes(asset)
                if body is not None:
                    self._send_bytes(body, _content_type(asset))
                    return
                if not _dist_available():
                    self._send_json(
                        {
                            "error": (
                                "console assets are not built; run 'npm run build' in web/"
                            )
                        },
                        status=500,
                    )
                    return
                self._send_json({"error": "not found"}, status=404)
                return
            parts = [unquote(part) for part in path.strip("/").split("/") if part]
            try:
                if parts == ["api", "runs"]:
                    self._send_json({"runs": list_runs(store)})
                    return
                if parts == ["api", "reports"]:
                    self._send_json(
                        {
                            "reports": [
                                record.model_dump(mode="json")
                                for record in reports.list_records()
                            ]
                        }
                    )
                    return
                if len(parts) == 3 and parts[:2] == ["api", "reports"]:
                    self._send_json(reports.load(parts[2]).model_dump(mode="json"))
                    return
                if parts == ["api", "baselines"]:
                    self._send_json({"baselines": baselines_payload(baselines)})
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
            self._send_json({"error": "method not allowed"}, status=405)

        def _route_action(self, path: str) -> None:
            parts = [unquote(part) for part in path.strip("/").split("/") if part]
            if parts == ["api", "baselines"]:
                body = self._read_json_body()
                if body is None:
                    return
                run_id = str(body.get("run_id") or "").strip()
                name = str(body.get("name") or DEFAULT_BASELINE_NAME).strip()
                if not run_id:
                    self._send_json({"error": "run_id is required"}, status=400)
                    return
                try:
                    run = store.load(run_id)
                except FileNotFoundError as exc:
                    self._send_json({"error": str(exc)}, status=404)
                    return
                baseline = Baseline.from_run(run, name=name or DEFAULT_BASELINE_NAME)
                baselines.save(baseline)
                self._send_json(
                    {"name": baseline.name, "run_id": baseline.run_id, "cases": len(baseline.verdicts)},
                    status=201,
                )
                return
            self._send_json({"error": "method not allowed"}, status=405)

        def _read_json_body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._send_json({"error": "request body is required"}, status=400)
                return None
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "request body must be valid JSON"}, status=400)
                return None
            if not isinstance(payload, Mapping):
                self._send_json({"error": "request body must be a JSON object"}, status=400)
                return None
            return dict(payload)

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
    *,
    allow_actions: bool | None = None,
) -> ThreadingHTTPServer:
    """创建控制台服务器；端口为 0 时由系统分配，便于测试。

    写接口默认只在绑定回环地址时开启：浏览器操作与命令行不同，用户看不到自己
    在打哪个环境，因此把可写范围收在只对本机可见的地址上。
    """

    if allow_actions is None:
        allow_actions = host in LOOPBACK_HOSTS
    handler = create_handler(
        store,
        reports=ConclusionStore(store.runs_dir.parent / "reports"),
        baselines=BaselineStore(store.runs_dir.parent / "baselines"),
        allow_actions=allow_actions,
    )
    return ThreadingHTTPServer((host, port), handler)
