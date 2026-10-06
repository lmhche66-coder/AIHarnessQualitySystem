from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.models import CheckOutcome, Run, Status, TokenUsage, Trace, Verdict
from agenteval.otel import (
    ExportError,
    build_otlp_payload,
    count_spans,
    export_run,
    span_id_for,
    trace_id_for,
)
from agenteval.process import record_tool_call
from agenteval.store import RunStore
from agenteval.tools import ToolResult


def make_run(run_id: str = "run-1") -> Run:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:00:02Z")
    run.verdicts = [
        Verdict(case_id="c1", status=Status.PASS, duration_ms=10.0),
        Verdict(
            case_id="c2",
            status=Status.FAIL,
            duration_ms=20.0,
            checks=[CheckOutcome(name="ui.visible", passed=False, expected=True, actual=False)],
        ),
    ]
    trace = Trace(case_id="c1")
    record_tool_call(
        trace,
        "echo_tool",
        {"message": "hi"},
        ToolResult(ok=True, value={"ok": 1}, usage=TokenUsage(input_tokens=7, output_tokens=3)),
    )
    record_tool_call(
        trace,
        "payout_tool",
        {"steps": ["reserve"]},
        ToolResult(ok=False, error_kind="upstream", error_message="boom", attempts=2),
    )
    run.traces = [trace]
    run.refresh_summary()
    return run


def spans_of(payload: dict) -> list[dict]:
    return payload["resourceSpans"][0]["scopeSpans"][0]["spans"]


def attr(span: dict, key: str):
    for item in span["attributes"]:
        if item["key"] == key:
            value = item["value"]
            return next(iter(value.values()))
    return None


# --------------------------------------------------------------------------- 结构


def test_payload_has_resource_scope_and_spans() -> None:
    payload = build_otlp_payload(make_run())
    resource = payload["resourceSpans"][0]["resource"]
    assert attr({"attributes": resource["attributes"]}, "service.name") == "agenteval"
    scope = payload["resourceSpans"][0]["scopeSpans"][0]["scope"]
    assert scope["name"] == "agenteval"
    assert count_spans(payload) == 5  # 1 运行 + 2 用例 + 2 工具调用


def test_identifiers_have_valid_length() -> None:
    payload = build_otlp_payload(make_run())
    for span in spans_of(payload):
        assert len(span["traceId"]) == 32
        assert len(span["spanId"]) == 16
        assert int(span["traceId"], 16) >= 0


def test_span_hierarchy_is_three_levels() -> None:
    payload = build_otlp_payload(make_run())
    root, case_one, tool_one, tool_two, case_two = spans_of(payload)
    assert "parentSpanId" not in root
    assert case_one["parentSpanId"] == root["spanId"]
    assert case_two["parentSpanId"] == root["spanId"]
    assert tool_one["parentSpanId"] == case_one["spanId"]
    assert tool_two["parentSpanId"] == case_one["spanId"]


def test_identifiers_are_deterministic_and_run_specific() -> None:
    # 同一份运行导出两次必须完全一致；重新构造的运行会带不同的时间戳，
    # 那不是「同一份运行」，所以只比较它的 trace 标识。
    run = make_run("run-1")
    first = build_otlp_payload(run)
    second = build_otlp_payload(run)
    other = build_otlp_payload(make_run("run-2"))
    assert first == second
    assert first["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] != (
        other["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"]
    )
    assert trace_id_for("run-1") != trace_id_for("run-2")
    assert span_id_for("run-1", "case", 0) == span_id_for("run-1", "case", 0)


# --------------------------------------------------------------------------- 语义


def test_tool_spans_follow_genai_conventions() -> None:
    payload = build_otlp_payload(make_run())
    tools = [span for span in spans_of(payload) if span["name"].startswith("execute_tool ")]
    assert [span["name"] for span in tools] == ["execute_tool echo_tool", "execute_tool payout_tool"]
    assert attr(tools[0], "gen_ai.operation.name") == "execute_tool"
    assert attr(tools[0], "gen_ai.tool.name") == "echo_tool"
    assert attr(tools[0], "gen_ai.usage.input_tokens") == "7"
    assert attr(tools[0], "gen_ai.usage.output_tokens") == "3"


def test_platform_fields_use_the_agenteval_namespace() -> None:
    payload = build_otlp_payload(make_run())
    tools = [span for span in spans_of(payload) if span["name"].startswith("execute_tool ")]
    assert attr(tools[1], "agenteval.tool.ok") is False
    assert attr(tools[1], "agenteval.tool.error_kind") == "upstream"
    assert attr(tools[1], "agenteval.tool.attempts") == "2"
    reserved = {
        item["key"]
        for span in spans_of(payload)
        for item in span["attributes"]
        if item["key"].startswith("gen_ai.")
    }
    assert reserved <= {
        "gen_ai.operation.name",
        "gen_ai.tool.name",
        "gen_ai.tool.call.id",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
    }


def test_failed_spans_carry_error_status() -> None:
    payload = build_otlp_payload(make_run())
    root, case_one, _, tool_two, case_two = spans_of(payload)
    assert root["status"]["code"] == 2
    assert case_one["status"]["code"] == 1
    assert case_two["status"]["code"] == 2
    assert tool_two["status"]["code"] == 2


# --------------------------------------------------------------------------- 推送


def make_receiver(status: int = 200) -> tuple[type[BaseHTTPRequestHandler], list[dict]]:
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 约定
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            if status == 200:
                received.append(json.loads(body.decode("utf-8")))
            self.send_response(status)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:
            """保持安静。"""

    return Handler, received


@contextlib.contextmanager
def receiver(status: int = 200) -> Iterator[tuple[str, list[dict]]]:
    handler, received = make_receiver(status)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1/traces", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_export_delivers_a_parseable_payload() -> None:
    with receiver() as (endpoint, received):
        result = export_run(make_run(), endpoint)
    assert result.status == 200
    assert result.spans == 5
    assert len(received) == 1
    payload = received[0]
    assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "agenteval.run"
    assert count_spans(payload) == 5


def test_export_reports_endpoint_errors() -> None:
    with receiver(status=500) as (endpoint, _):
        with pytest.raises(ExportError, match="returned 500"):
            export_run(make_run(), endpoint)


def test_export_reports_unreachable_endpoint() -> None:
    with pytest.raises(ExportError, match="could not reach endpoint"):
        export_run(make_run(), "http://127.0.0.1:9/v1/traces", timeout_s=2)


# --------------------------------------------------------------------------- CLI


def write_run(home: Path, run_id: str = "run-1") -> None:
    RunStore(home / "runs").save(make_run(run_id))


def test_cli_exports_a_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    write_run(home, "run-1")
    with receiver() as (endpoint, received):
        code = main(["--home", str(home), "otel", "export", "--run", "run-1", "--endpoint", endpoint])
    output = capsys.readouterr().out
    assert code == 0
    assert "exported 5 spans from run run-1" in output
    assert len(received) == 1


def test_cli_can_print_the_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    write_run(home, "run-1")
    with receiver() as (endpoint, _):
        code = main(
            ["--home", str(home), "otel", "export", "--run", "run-1", "--endpoint", endpoint, "--json"]
        )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert count_spans(payload) == 5


def test_cli_fails_when_the_endpoint_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    write_run(home, "run-1")
    with receiver(status=503) as (endpoint, _):
        code = main(["--home", str(home), "otel", "export", "--run", "run-1", "--endpoint", endpoint])
    assert code == 1
    assert "503" in capsys.readouterr().err


def test_cli_rejects_an_unknown_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "otel",
            "export",
            "--run",
            "absent",
            "--endpoint",
            "http://127.0.0.1:9/v1/traces",
        ]
    )
    assert code == 2
    assert "absent" in capsys.readouterr().err


def test_export_does_not_touch_local_artifacts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write_run(home, "run-1")
    before = {
        path.relative_to(home): path.stat().st_mtime_ns
        for path in sorted(home.rglob("*"))
        if path.is_file()
    }
    with receiver() as (endpoint, _):
        export_run(make_run(), endpoint)
    after = {
        path.relative_to(home): path.stat().st_mtime_ns
        for path in sorted(home.rglob("*"))
        if path.is_file()
    }
    assert before == after
