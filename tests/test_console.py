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
def running_console(store: RunStore, allow_actions: bool | None = None) -> Iterator[str]:
    server = create_server(store, port=0, allow_actions=allow_actions)
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


def fetch(url: str, method: str = "GET", body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def test_reflow_endpoint_reports_dataset_and_ledger(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    reflow_dir = tmp_path / "reflow"
    reflow_dir.mkdir(parents=True)
    (reflow_dir / "regression_cases.json").write_text(
        json.dumps({"cases": [{"id": "case@repro-1", "kind": "process", "checks": []}]}),
        encoding="utf-8",
    )
    (reflow_dir / "ledger.json").write_text(
        json.dumps({"processed": {"abc": "2026-01-01T00:00:00Z"}}),
        encoding="utf-8",
    )
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/reflow")
    assert status == 200
    payload = json.loads(body)
    assert payload["counts"] == {"cases": 1, "processed": 1}
    assert payload["dataset"]["cases"][0]["id"] == "case@repro-1"
    assert payload["ledger"]["processed"] == {"abc": "2026-01-01T00:00:00Z"}


def test_reflow_endpoint_is_empty_without_a_dataset(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/reflow")
    assert status == 200
    payload = json.loads(body)
    assert payload["counts"] == {"cases": 0, "processed": 0}
    assert payload["dataset"]["cases"] == []


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
        status, _ = fetch(f"{base}/api/nope", method="POST")
    assert status == 405


def test_actions_are_refused_when_not_on_loopback(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(make_run("run-1", "2026-01-01T00:00:00Z"))
    with running_console(store, allow_actions=False) as base:
        status, body = fetch(
            f"{base}/api/baselines", method="POST", body={"run_id": "run-1", "name": "main"}
        )
    assert status == 403
    assert "read-only" in json.loads(body)["error"]


def test_missing_run_directory_yields_empty_list(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "does-not-exist")
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/runs")
    assert status == 200
    assert json.loads(body)["runs"] == []


def test_console_page_is_served_from_the_build(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, body = fetch(f"{base}/")
    html = body.decode("utf-8")
    assert status == 200
    assert "/assets/" in html
    assert "https://" not in html


def test_built_assets_are_served(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        _, html = fetch(f"{base}/")
        marker = "/assets/"
        start = html.decode("utf-8").index(marker)
        asset = html.decode("utf-8")[start:].split('"')[0]
        status, _ = fetch(f"{base}{asset}")
    assert status == 200


def test_asset_path_traversal_is_refused(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, _ = fetch(f"{base}/assets/../../server.py")
    assert status in (400, 404)


def test_reports_endpoint_lists_conclusions(tmp_path: Path) -> None:
    from agenteval.reports import KIND_GATE, ConclusionStore, write_conclusion

    store = RunStore(tmp_path / "runs")
    write_conclusion(
        ConclusionStore(tmp_path / "reports"),
        kind=KIND_GATE,
        title="gate run-1",
        passed=True,
        run_id="run-1",
        summary={"run_id": "run-1", "pass_rate": 1.0},
        payload={"run_id": "run-1"},
    )
    with running_console(store) as base:
        status, body = fetch(f"{base}/api/reports")
        detail_status, detail_body = fetch(
            f"{base}/api/reports/{json.loads(body)['reports'][0]['id']}"
        )
    assert status == 200
    assert json.loads(body)["reports"][0]["kind"] == "gate"
    assert detail_status == 200
    assert json.loads(detail_body)["summary"]["pass_rate"] == 1.0


def test_baselines_endpoint_lists_and_captures(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(make_run("run-1", "2026-01-01T00:00:00Z"))
    with running_console(store) as base:
        empty_status, empty_body = fetch(f"{base}/api/baselines")
        create_status, create_body = fetch(
            f"{base}/api/baselines",
            method="POST",
            body={"run_id": "run-1", "name": "main"},
        )
        list_status, list_body = fetch(f"{base}/api/baselines")
    assert empty_status == 200
    assert json.loads(empty_body)["baselines"] == []
    assert create_status == 201
    assert json.loads(create_body)["cases"] == 2
    baselines = json.loads(list_body)["baselines"]
    assert list_status == 200
    assert baselines[0]["name"] == "main"
    assert baselines[0]["run_id"] == "run-1"
    assert baselines[0]["counts"] == {"pass": 1, "fail": 1}


def test_capturing_a_baseline_from_an_unknown_run_fails(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        status, body = fetch(
            f"{base}/api/baselines", method="POST", body={"run_id": "absent", "name": "x"}
        )
    assert status == 404
    assert "absent" in json.loads(body)["error"]


def test_page_has_no_external_resources(tmp_path: Path) -> None:
    """页面不引用任何外部脚本或样式；打包产物里的错误提示 URL 不算外部引用。"""

    store = RunStore(tmp_path / "runs")
    with running_console(store) as base:
        html = fetch(f"{base}/")[1].decode("utf-8")
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert "cdn" not in html
