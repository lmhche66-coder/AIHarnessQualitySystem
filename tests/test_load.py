from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from agenteval.cli import main
from agenteval.load import (
    ERROR_RATE_LIMITED,
    LoadScenario,
    load_scenarios,
    resolve_url,
    run_scenario,
    run_scenarios,
)
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"

USAGE = {
    "usage": {
        "llm_calls": 3,
        "tool_calls": 5,
        "prompt_tokens": 100,
        "completion_tokens": 20,
    }
}


def make_handler(mode: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 约定
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if mode == "slow":
                time.sleep(0.12)
            if mode == "rate_limited":
                self._respond(429, b'{"error":"slow down"}')
                return
            if mode == "server_error":
                self._respond(500, b'{"error":"boom"}')
                return
            payload = b"{}" if mode == "no_fanout" else json.dumps(USAGE).encode("utf-8")
            if mode == "stream":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload[:5])
                self.wfile.flush()
                time.sleep(0.1)
                self.wfile.write(payload[5:])
                self.wfile.flush()
                return
            self._respond(200, payload)

        def _respond(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            """保持安静。"""

    return Handler


@contextlib.contextmanager
def load_server(mode: str = "ok") -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mode))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def scenario(**overrides: object) -> LoadScenario:
    payload: dict[str, object] = {
        "id": "s1",
        "target": {"url": "/work", "method": "POST", "body": "{}"},
        "concurrency": 3,
        "requests": 12,
    }
    payload.update(overrides)
    return LoadScenario.model_validate(payload)


def test_scenario_requires_a_stop_condition() -> None:
    with pytest.raises(ValidationError):
        LoadScenario.model_validate({"id": "s1", "target": {"url": "/x"}})


def test_scenario_rejects_invalid_concurrency() -> None:
    with pytest.raises(ValidationError):
        LoadScenario.model_validate(
            {"id": "s1", "target": {"url": "/x"}, "concurrency": 0, "requests": 1}
        )


def test_resolve_url_uses_base_for_relative_targets() -> None:
    assert resolve_url("/work", "http://host:9") == "http://host:9/work"
    assert resolve_url("http://other/work", "http://host:9") == "http://other/work"
    assert resolve_url("/work", None) == "/work"


def test_successful_load_reports_throughput_and_latency() -> None:
    with load_server("ok") as base:
        report = run_scenario(scenario(requests=15, concurrency=3), base_url=base)
    assert report.completed == 15
    assert report.failed == 0
    assert report.error_rate == 0.0
    assert report.throughput_rps > 0
    assert report.latency_p50_ms > 0
    assert report.latency_p99_ms >= report.latency_p95_ms >= report.latency_p90_ms


def test_rate_limited_responses_are_classified_separately() -> None:
    with load_server("rate_limited") as base:
        report = run_scenario(
            scenario(requests=10, thresholds={"max_error_rate": 0.05}), base_url=base
        )
    assert report.error_kinds[ERROR_RATE_LIMITED] == 10
    assert report.status_counts["429"] == 10
    assert report.passed is False
    assert any("error rate" in reason for reason in report.reasons)


def test_server_errors_are_classified_separately() -> None:
    with load_server("server_error") as base:
        report = run_scenario(scenario(requests=6), base_url=base)
    assert report.error_kinds["server_error"] == 6


def test_slow_target_fails_a_latency_budget() -> None:
    with load_server("slow") as base:
        report = run_scenario(
            scenario(requests=6, concurrency=2, thresholds={"max_p95_ms": 50}), base_url=base
        )
    assert report.latency_p95_ms > 50
    assert report.passed is False
    assert any("p95 latency" in reason for reason in report.reasons)


def test_throughput_budget_can_fail() -> None:
    with load_server("slow") as base:
        report = run_scenario(
            scenario(requests=4, concurrency=1, thresholds={"min_throughput_rps": 100}),
            base_url=base,
        )
    assert report.passed is False
    assert any("throughput" in reason for reason in report.reasons)


def test_streaming_measures_time_to_first_byte() -> None:
    with load_server("stream") as base:
        report = run_scenario(scenario(requests=4, concurrency=1, stream=True), base_url=base)
    assert report.ttfb_observed is True
    assert report.ttfb_p50_ms is not None
    assert report.ttfb_p50_ms < report.latency_p50_ms


def test_non_streaming_does_not_report_ttfb() -> None:
    with load_server("ok") as base:
        report = run_scenario(scenario(requests=4), base_url=base)
    assert report.ttfb_observed is False
    assert report.ttfb_p50_ms is None


def test_fanout_is_extracted_when_declared() -> None:
    with load_server("ok") as base:
        report = run_scenario(
            scenario(
                requests=4,
                extract={"tool_calls": "usage.tool_calls", "input_tokens": "usage.prompt_tokens"},
            ),
            base_url=base,
        )
    assert report.fanout_observed is True
    assert report.fanout["tool_calls"]["mean"] == 5.0
    assert report.fanout["input_tokens"]["p95"] == 100.0
    assert report.limitations == []


def test_declared_but_unobserved_fanout_is_flagged() -> None:
    with load_server("no_fanout") as base:
        report = run_scenario(
            scenario(requests=3, extract={"tool_calls": "usage.tool_calls"}), base_url=base
        )
    assert report.fanout_observed is False
    assert report.fanout == {}
    assert any("no fan-out metrics" in item for item in report.limitations)


def test_without_extract_rules_fanout_is_not_reported() -> None:
    with load_server("ok") as base:
        report = run_scenario(scenario(requests=3), base_url=base)
    assert report.fanout_observed is False
    assert report.fanout == {}
    assert report.limitations == []


def test_duration_based_run_stops_on_time() -> None:
    with load_server("ok") as base:
        report = run_scenario(scenario(requests=0, duration_s=0.3, concurrency=2), base_url=base)
    assert report.completed > 0
    assert 0.2 <= report.duration_s < 3.0


def test_no_budget_means_no_verdict() -> None:
    with load_server("ok") as base:
        report = run_scenario(scenario(requests=3), base_url=base)
    assert report.passed is None
    assert report.reasons == []


def test_run_record_is_console_ready(tmp_path: Path) -> None:
    with load_server("ok") as base:
        run = run_scenarios(
            [scenario(requests=6, extract={"input_tokens": "usage.prompt_tokens"})],
            base_url=base,
            store=RunStore(tmp_path / "runs"),
            run_id="load-run",
        )
    assert run.summary.passed == 1
    assert run.metadata["load_report"][0]["completed"] == 6
    metrics = run.metadata["metrics"]
    assert metrics["calls"] == 6
    assert metrics["usage_reported"] is True
    assert metrics["input_tokens"] == 600
    assert RunStore(tmp_path / "runs").load("load-run").summary.total == 1


def test_example_scenario_file_parses() -> None:
    scenarios = load_scenarios(EXAMPLES / "load_scenarios.json")
    assert scenarios[0].id == "diagnosis-endpoint"
    assert scenarios[0].extract["tool_calls"] == "usage.tool_calls"


def test_cli_load_run_with_base_url(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scenarios_path = tmp_path / "scenarios.json"
    scenarios_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "probe",
                        "target": {"url": "/work", "method": "POST", "body": "{}"},
                        "concurrency": 2,
                        "requests": 6,
                        "thresholds": {"max_error_rate": 0.01},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with load_server("ok") as base:
        code = main(
            [
                "--home",
                str(tmp_path / "home"),
                "load",
                "run",
                "--scenarios",
                str(scenarios_path),
                "--base-url",
                base,
            ]
        )
    output = capsys.readouterr().out
    assert code == 0
    assert "[PASS] probe" in output
    assert "throughput:" in output
    assert "latency ms:" in output
