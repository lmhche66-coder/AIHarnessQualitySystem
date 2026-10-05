from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from agenteval.console.server import create_server
from agenteval.gold import GOLD_DIRNAME, GoldStore
from agenteval.models import Run, Status, Verdict
from agenteval.store import RunStore
from agenteval.trace_store import TraceStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@contextlib.contextmanager
def console(tmp_path: Path, allow_actions: bool | None = None) -> Iterator[str]:
    store = RunStore(tmp_path / "home" / "runs")
    server = create_server(store, port=0, allow_actions=allow_actions)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{server.server_address[0]}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def call(url: str, method: str = "GET", body: object = None) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read().decode("utf-8")
            return response.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8")
        return error.code, (json.loads(text) if text else None)


def write_run(home: Path, run_id: str = "run-1") -> None:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z")
    run.verdicts = [Verdict(case_id="p1", status=Status.PASS)]
    run.refresh_summary()
    RunStore(home / "runs").save(run)


def gold_items() -> list[dict[str, object]]:
    return [
        {
            "id": "i1",
            "prompt": "哪个更好？",
            "response_a": "根因：电极接触阻抗偏高。",
            "response_b": "再查查。",
        },
        {
            "id": "i2",
            "prompt": "第二个呢？",
            "response_a": "再查查。",
            "response_b": "根因：补光不足。",
            "expected": "b",
        },
    ]


# --------------------------------------------------------------------------- 金标


def test_gold_set_can_be_created_and_listed(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        status, payload = call(
            f"{base}/api/gold", "POST", {"name": "demo", "items": gold_items()}
        )
        list_status, listing = call(f"{base}/api/gold")
    assert status == 201
    assert payload["items"] == 2
    assert payload["labeled"] == 1
    assert list_status == 200
    assert listing["sets"] == [{"name": "demo", "items": 2, "labeled": 1}]


def test_labeling_updates_progress_and_persists(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        call(f"{base}/api/gold", "POST", {"name": "demo", "items": gold_items()})
        status, payload = call(
            f"{base}/api/gold/demo/labels", "POST", {"item_id": "i1", "expected": "a"}
        )
        detail_status, detail = call(f"{base}/api/gold/demo")
    assert status == 200
    assert payload["labeled"] == 2
    assert detail_status == 200
    labels = {item["id"]: item["expected"] for item in detail["items"]}
    assert labels == {"i1": "a", "i2": "b"}
    assert GoldStore(tmp_path / "home" / GOLD_DIRNAME).load("demo").labeled() == 2


def test_labeling_rejects_unknown_item_and_invalid_preference(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        call(f"{base}/api/gold", "POST", {"name": "demo", "items": gold_items()})
        missing_status, missing = call(
            f"{base}/api/gold/demo/labels", "POST", {"item_id": "nope", "expected": "a"}
        )
        bad_status, bad = call(
            f"{base}/api/gold/demo/labels", "POST", {"item_id": "i1", "expected": "maybe"}
        )
        absent_status, _ = call(
            f"{base}/api/gold/absent/labels", "POST", {"item_id": "i1", "expected": "a"}
        )
    assert missing_status == 404 and "nope" in missing["error"]
    assert bad_status == 400 and "expected must be one of" in bad["error"]
    assert absent_status == 404


def test_gold_creation_rejects_invalid_items(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        status, payload = call(
            f"{base}/api/gold", "POST", {"name": "demo", "items": [{"prompt": "x"}]}
        )
        empty_status, _ = call(f"{base}/api/gold", "POST", {"name": "demo2", "items": []})
    assert status == 400 and "gold item #0" in payload["error"]
    assert empty_status == 400


# --------------------------------------------------------------------------- 轨迹


def test_trace_upload_imports_json_audit(tmp_path: Path) -> None:
    content = (EXAMPLES / "kingfar_audit.json").read_text(encoding="utf-8")
    with console(tmp_path) as base:
        status, payload = call(
            f"{base}/api/traces",
            "POST",
            {"name": "imported", "case_id": "task-1", "content": content, "format": "json"},
        )
        list_status, listing = call(f"{base}/api/traces")
    assert status == 201
    assert payload["records"] == 5
    assert any("state_continuity" in item for item in payload["limitations"])
    assert list_status == 200
    assert listing["traces"][0]["name"] == "imported"
    trace = TraceStore(tmp_path / "home" / "traces").load("imported")
    assert trace.case_id == "task-1"


def test_trace_upload_imports_csv_audit(tmp_path: Path) -> None:
    content = (EXAMPLES / "kingfar_audit.csv").read_text(encoding="utf-8")
    with console(tmp_path) as base:
        status, payload = call(
            f"{base}/api/traces",
            "POST",
            {"name": "csv-trace", "content": content, "format": "csv"},
        )
    assert status == 201
    assert payload["records"] == 5


def test_trace_upload_rejects_bad_input(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        no_name, _ = call(f"{base}/api/traces", "POST", {"content": "[]"})
        bad_json, bad_json_payload = call(
            f"{base}/api/traces", "POST", {"name": "x", "content": "{not json"}
        )
        empty, _ = call(f"{base}/api/traces", "POST", {"name": "x", "content": "[]"})
    assert no_name == 400
    assert bad_json == 400 and "not valid JSON" in bad_json_payload["error"]
    assert empty == 400


# --------------------------------------------------------------------------- 回流


def test_reflow_reports_and_writes_selected_candidates(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "p1",
                        "kind": "process",
                        "steps": [{"target": "echo_tool", "input": {"message": "hi"}}],
                        "checks": [
                            {"kind": "tool_sequence", "expected": ["payout_tool"], "mode": "exact"}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = RunStore(home / "runs")
    from agenteval.runner import ContractRunner, load_cases
    from agenteval.fakes import build_demo_registry

    runner = ContractRunner(registry=build_demo_registry(), store=store)
    run = runner.run(
        load_cases(cases_path), metadata={"cases_file": str(cases_path)}, run_id="failing-run"
    )
    assert run.summary.failed == 1

    with console(tmp_path) as base:
        status, payload = call(f"{base}/api/reflow", "POST", {"run_id": "failing-run"})
        write_status, written = call(
            f"{base}/api/cases",
            "POST",
            {"name": "repro", "cases": payload.get("candidates", [])},
        )
        listing_status, listing = call(f"{base}/api/cases")
    assert status == 200
    assert payload["report"]["verified"] == 1
    assert payload["candidates"][0]["id"] == "p1@repro"
    assert write_status == 201
    assert written["written"] == 1
    assert (home / "cases" / "repro.json").is_file()
    assert listing_status == 200
    assert listing["cases"] == ["repro"]


def test_reflow_without_selection_writes_nothing(tmp_path: Path) -> None:
    write_run(tmp_path / "home")
    with console(tmp_path) as base:
        status, payload = call(f"{base}/api/cases", "POST", {"name": "empty", "cases": []})
    assert status == 201
    assert payload["written"] == 0
    assert payload["path"] is None
    assert not (tmp_path / "home" / "cases" / "empty.json").exists()


def test_reflow_reports_missing_run_and_cases_file(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        missing_run, _ = call(f"{base}/api/reflow", "POST", {"run_id": "absent"})
        write_run(tmp_path / "home", "run-1")
        no_cases, payload = call(f"{base}/api/reflow", "POST", {"run_id": "run-1"})
    assert missing_run == 404
    assert no_cases == 400
    assert "cases file" in payload["error"]


# --------------------------------------------------------------------------- 运行


def test_run_trigger_executes_and_stores(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "c1",
                        "target": "echo_tool",
                        "input": {"message": "hi"},
                        "check": {"kind": "missing_required", "drop": ["message"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with console(tmp_path) as base:
        status, payload = call(
            f"{base}/api/runs",
            "POST",
            {"cases_file": str(cases_path), "registry": "agenteval.fakes:build_demo_registry"},
        )
    assert status == 201
    assert payload["total"] == 1
    assert payload["passed"] == 1
    stored = RunStore(tmp_path / "home" / "runs").load(payload["run_id"])
    assert stored.metadata["triggered_by"] == "console"


def test_run_trigger_rejects_missing_file_and_bad_registry(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [{"id": "c1", "target": "echo_tool", "input": {}, "check": {"kind": "missing_required", "drop": ["message"]}}]}),
        encoding="utf-8",
    )
    with console(tmp_path) as base:
        missing, payload = call(
            f"{base}/api/runs", "POST", {"cases_file": str(tmp_path / "no.json"), "registry": "x:y"}
        )
        bad_registry, registry_payload = call(
            f"{base}/api/runs", "POST", {"cases_file": str(cases_path), "registry": "nope"}
        )
    assert missing == 404 and "cases file not found" in payload["error"]
    assert bad_registry == 400
    assert "registry" in registry_payload["error"]


# --------------------------------------------------------------------------- 边界


def test_every_action_is_refused_off_loopback(tmp_path: Path) -> None:
    write_run(tmp_path / "home", "run-1")
    actions = [
        ("/api/gold", {"name": "g", "items": gold_items()}),
        ("/api/traces", {"name": "t", "content": "[]"}),
        ("/api/reflow", {"run_id": "run-1"}),
        ("/api/cases", {"name": "c", "cases": []}),
        ("/api/runs", {"cases_file": "x", "registry": "y"}),
        ("/api/baselines", {"run_id": "run-1"}),
    ]
    with console(tmp_path, allow_actions=False) as base:
        for path, body in actions:
            status, payload = call(f"{base}{path}", "POST", body)
            assert status == 403, path
            assert "read-only" in payload["error"]
        read_status, _ = call(f"{base}/api/runs")
    assert read_status == 200


def test_oversized_body_is_refused(tmp_path: Path) -> None:
    with console(tmp_path) as base:
        status, payload = call(
            f"{base}/api/traces", "POST", {"name": "big", "content": "x" * 2_100_000}
        )
    assert status == 413
    assert "exceeds" in payload["error"]
