from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from agenteval.console.server import create_server
from agenteval.models import CheckOutcome, Run, Status, Trace, Verdict
from agenteval.process import record_tool_call
from agenteval.store import RunStore
from agenteval.tools import ToolResult


def make_run(run_id: str, started_at: str) -> Run:
    run = Run(run_id=run_id, started_at=started_at)
    run.verdicts = [
        Verdict(
            case_id="case-ok",
            status=Status.PASS,
            duration_ms=1.5,
            metrics={"calls": 2, "retries": 1},  # type: ignore[arg-type]
        ),
        Verdict(
            case_id="case-bad",
            status=Status.FAIL,
            duration_ms=3.0,
            checks=[
                CheckOutcome(
                    name="tool_sequence.exact",
                    passed=False,
                    expected=["a", "b"],
                    actual=["a"],
                    message="sequence mismatch",
                )
            ],
        ),
    ]
    trace = Trace(case_id="case-ok")
    record_tool_call(trace, "echo_tool", {"message": "hi"}, ToolResult(ok=True, value={"ok": 1}))
    run.traces = [trace]
    run.metadata["metrics"] = {
        "cases": 2,
        "calls": 2,
        "retries": 1,
        "duration_p50_ms": 1.5,
        "duration_p95_ms": 3.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "usage_reported": False,
        "budget_violations": ["case-bad:budget.max_calls"],
        "percentile_method": "nearest-rank",
    }
    run.refresh_summary()
    return run


@contextlib.contextmanager
def running_console(store: RunStore) -> Iterator[str]:
    server = create_server(store, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fetch(url: str, method: str = "GET") -> tuple[int, bytes]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_run_list_is_sorted_newest_first(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(make_run("older", "2026-01-01T00:00:00Z"))
    store.save(make_run("newer", "2026-02-01T00:00:00Z"))
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/runs")
    payload = json.loads(body)
    assert status == 200
    assert [run["run_id"] for run in payload["runs"]] == ["newer", "older"]
    assert payload["runs"][0]["summary"]["total"] == 2


def test_run_detail_exposes_failed_checks(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(make_run("run-1", "2026-01-01T00:00:00Z"))
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/runs/run-1")
    payload = json.loads(body)
    assert status == 200
    failing = next(v for v in payload["verdicts"] if v["case_id"] == "case-bad")
    assert failing["status"] == "fail"
    assert failing["failed_checks"][0]["name"] == "tool_sequence.exact"
    assert failing["failed_checks"][0]["expected"] == ["a", "b"]


def test_trace_endpoint_returns_tool_calls(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(make_run("run-1", "2026-01-01T00:00:00Z"))
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/runs/run-1/traces/case-ok")
    payload = json.loads(body)
    assert status == 200
    events = [event for event in payload["events"] if event["name"] == "tool_call"]
    assert events[0]["payload"]["target"] == "echo_tool"


def test_unknown_run_returns_404(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/runs/absent")
    assert status == 404
    assert "error" in json.loads(body)


def test_console_is_read_only(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, _ = fetch(f"{base}/api/runs", method="POST")
    assert status == 405


def test_missing_run_directory_yields_empty_list(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "does-not-exist")
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/runs")
    assert status == 200
    assert json.loads(body)["runs"] == []


def test_static_assets_are_served(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, body = fetch(f"{base}/")
    html = body.decode("utf-8")
    assert status == 200
    assert "/app.js" in html
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "https://" not in html


def test_frontend_has_no_external_references(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        assets = [fetch(f"{base}/style.css")[1], fetch(f"{base}/app.js")[1]]
    for body in assets:
        text = body.decode("utf-8")
        assert "https://" not in text
        assert "http://" not in text
