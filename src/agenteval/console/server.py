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

from agenteval.console.actions import ConsoleServices, OperationError
from agenteval.gate import Baseline, BaselineStore
from agenteval.models import Run
from agenteval.store import SUMMARY_FILENAME, RunStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_RUNS = 200
DIST_DIRNAME = "dist"
MAX_BODY_BYTES = 2_000_000
HARD_BODY_BYTES = 32_000_000

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


def trace_listing(services: ConsoleServices) -> list[dict[str, Any]]:
    """列出已保存轨迹及其来源，便于导入视图确认结果。"""

    entries: list[dict[str, Any]] = []
    for name in services.traces.store.list_traces():
        meta = services.traces.store.metadata(name)
        entries.append(
            {
                "name": name,
                "case_id": meta.case_id if meta else name,
                "events": meta.event_count if meta else 0,
                "source": meta.source if meta else None,
            }
        )
    return entries


def create_handler(services: ConsoleServices) -> type[BaseHTTPRequestHandler]:
    """构造绑定到指定存储的请求处理器，便于测试直接注入临时目录。"""

    from agenteval.gate import DEFAULT_BASELINE_NAME

    store = services.runs
    reports = services.reports
    baselines = services.baselines
    allow_actions = services.allow_actions

    class ConsoleHandler(BaseHTTPRequestHandler):
        server_version = "agenteval-console"

        def do_GET(self) -> None:  # noqa: N802 - http.server 约定
            self._route(urlparse(self.path).path)

        def do_POST(self) -> None:  # noqa: N802
            if not allow_actions:
                self._drain_body()
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
                if parts == ["api", "gold"]:
                    self._send_json({"sets": services.gold.list_sets()})
                    return
                if len(parts) == 3 and parts[:2] == ["api", "gold"]:
                    self._send_json(services.gold.detail(parts[2]))
                    return
                if parts == ["api", "traces"]:
                    self._send_json({"traces": trace_listing(services)})
                    return
                if parts == ["api", "cases"]:
                    self._send_json({"cases": services.list_cases_files()})
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
            handler = self._action_handler(parts)
            if handler is None:
                self._drain_body()
                self._send_json({"error": "method not allowed"}, status=405)
                return
            body = self._read_json_body()
            if body is None:
                return
            try:
                payload, status = handler(body)
            except OperationError as exc:
                self._send_json({"error": str(exc)}, status=exc.status)
                return
            except Exception as exc:  # noqa: BLE001 - 兜底，避免异常只留在线程里
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)
                return
            self._send_json(payload, status=status)

        def _action_handler(self, parts: list[str]) -> Any:
            if parts == ["api", "baselines"]:
                return self._capture_baseline
            if parts == ["api", "gold"]:
                return lambda body: (services.gold.create(body), 201)
            if len(parts) == 4 and parts[:2] == ["api", "gold"] and parts[3] == "labels":
                return lambda body: (services.gold.label(parts[2], body), 200)
            if parts == ["api", "traces"]:
                return lambda body: (services.traces.upload(body), 201)
            if parts == ["api", "reflow"]:
                return lambda body: (services.reflow.analyze(body), 200)
            if parts == ["api", "cases"]:
                return lambda body: (services.reflow.write(body), 201)
            if parts == ["api", "runs"]:
                return lambda body: (services.runner.trigger(body), 201)
            if parts == ["api", "tasks"]:
                return lambda body: (services.runner.trigger_tasks(body), 201)
            if parts == ["api", "gate"]:
                return lambda body: (services.gate.run(body), 201)
            if parts == ["api", "judge"]:
                return lambda body: (services.judge.run(body), 201)
            return None

        def _capture_baseline(self, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
            run_id = str(body.get("run_id") or "").strip()
            name = str(body.get("name") or DEFAULT_BASELINE_NAME).strip()
            if not run_id:
                raise OperationError("run_id is required")
            try:
                run = store.load(run_id)
            except FileNotFoundError as exc:
                raise OperationError(str(exc), status=404) from exc
            baseline = Baseline.from_run(run, name=name or DEFAULT_BASELINE_NAME)
            baselines.save(baseline)
            return (
                {
                    "name": baseline.name,
                    "run_id": baseline.run_id,
                    "cases": len(baseline.verdicts),
                },
                201,
            )

        def _read_json_body(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._send_json({"error": "request body is required"}, status=400)
                return None
            if length > HARD_BODY_BYTES:
                self.close_connection = True
                self._send_json(
                    {"error": f"request body exceeds {MAX_BODY_BYTES} bytes"}, status=413
                )
                return None
            raw = self.rfile.read(length)
            if length > MAX_BODY_BYTES:
                self._send_json(
                    {"error": f"request body exceeds {MAX_BODY_BYTES} bytes"}, status=413
                )
                return None
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json({"error": "request body must be valid JSON"}, status=400)
                return None
            if not isinstance(payload, Mapping):
                self._send_json({"error": "request body must be a JSON object"}, status=400)
                return None
            return dict(payload)

        def _drain_body(self) -> None:
            """拒绝请求前先把请求体读掉，否则客户端写完之前连接就会被重置。"""

            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return
            if length > HARD_BODY_BYTES:
                self.close_connection = True
                return
            self.rfile.read(length)

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
    handler = create_handler(ConsoleServices.for_home(store, allow_actions))
    return ThreadingHTTPServer((host, port), handler)
