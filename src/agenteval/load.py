"""HTTP 压测。

agent 服务在负载下的表现与普通 HTTP 服务不同：单请求成本无上界、流式连接数
不等于吞吐、第一个饱和点往往在外部模型限额、限流触发重试再推高负载。因此这里
除常规吞吐与延迟外，把扇出与用量作为一等指标，并把错误按原因分类。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from itertools import count as _count
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenteval.metrics import RunMetrics, percentile
from agenteval.models import CaseMetrics, CheckOutcome, Run, Status, Verdict
from agenteval.runner import new_run_id
from agenteval.store import RunStore

ERROR_TIMEOUT = "timeout"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_SERVER = "server_error"
ERROR_CLIENT = "client_error"
ERROR_TRANSPORT = "transport"


class LoadTarget(BaseModel):
    """被施压的 HTTP 目标。"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class LoadThresholds(BaseModel):
    """非功能预算。未声明的项不参与判定。"""

    model_config = ConfigDict(extra="forbid")

    max_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    max_p95_ms: float | None = Field(default=None, gt=0)
    min_throughput_rps: float | None = Field(default=None, gt=0)


class LoadScenario(BaseModel):
    """一次压测场景。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str | None = None
    target: LoadTarget
    concurrency: int = Field(default=4, ge=1)
    requests: int = Field(default=0, ge=0)
    duration_s: float = Field(default=0.0, ge=0)
    timeout_s: float = Field(default=30.0, gt=0)
    stream: bool = False
    think_time_ms: float = Field(default=0.0, ge=0)
    extract: dict[str, str] = Field(default_factory=dict)
    thresholds: LoadThresholds | None = None

    @model_validator(mode="after")
    def _require_stop_condition(self) -> "LoadScenario":
        if self.requests <= 0 and self.duration_s <= 0:
            raise ValueError("scenario must declare requests or duration_s")
        return self


class RequestSample(BaseModel):
    """单次请求的观测结果。"""

    model_config = ConfigDict(extra="forbid")

    index: int
    started_at: datetime
    status: int | None = None
    ok: bool = False
    error_kind: str | None = None
    error: str | None = None
    duration_ms: float = 0.0
    ttfb_ms: float | None = None
    fanout: dict[str, float] = Field(default_factory=dict)


class LoadReport(BaseModel):
    """一次场景施压的汇总，同时也是机器可读报告。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    concurrency: int = 0
    requested: int = 0
    completed: int = 0
    failed: int = 0
    error_rate: float = 0.0
    duration_s: float = 0.0
    throughput_rps: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p90_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    ttfb_observed: bool = False
    ttfb_p50_ms: float | None = None
    ttfb_p95_ms: float | None = None
    status_counts: dict[str, int] = Field(default_factory=dict)
    error_kinds: dict[str, int] = Field(default_factory=dict)
    fanout_observed: bool = False
    fanout: dict[str, dict[str, float]] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    thresholds: LoadThresholds | None = None
    passed: bool | None = None
    reasons: list[str] = Field(default_factory=list)


def load_scenarios(path: Path) -> list[LoadScenario]:
    """载入压测场景，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "scenarios" not in payload:
            raise ValueError(f"scenario file must contain a list or a 'scenarios' key: {path}")
        payload = payload["scenarios"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"scenario file must contain at least one scenario: {path}")
    return [LoadScenario.model_validate(entry) for entry in payload]


def resolve_url(url: str, base_url: str | None) -> str:
    """把相对地址解析到基础地址，便于同一份场景指向不同环境。"""

    if base_url is None or "://" in url:
        return url
    return f"{base_url.rstrip('/')}/{url.lstrip('/')}"


def run_scenario(scenario: LoadScenario, base_url: str | None = None) -> LoadReport:
    """按并发施压，直到请求总量或持续时间到达。"""

    url = resolve_url(scenario.target.url, base_url)
    counter = _count()
    samples: list[RequestSample] = []
    lock = threading.Lock()
    deadline = (time.monotonic() + scenario.duration_s) if scenario.duration_s > 0 else None

    def worker() -> None:
        while True:
            index = next(counter)
            if scenario.requests > 0 and index >= scenario.requests:
                return
            if deadline is not None and time.monotonic() >= deadline:
                return
            sample = _perform(scenario, url, index)
            with lock:
                samples.append(sample)
            if scenario.think_time_ms > 0:
                time.sleep(scenario.think_time_ms / 1000.0)

    threads = [
        threading.Thread(target=worker, daemon=True) for _ in range(scenario.concurrency)
    ]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = max(time.monotonic() - started, 1e-6)
    return summarize(scenario, samples, elapsed)


def _perform(scenario: LoadScenario, url: str, index: int) -> RequestSample:
    target = scenario.target
    body = target.body.encode("utf-8") if target.body is not None else None
    headers = dict(target.headers)
    if body is not None and not any(key.lower() == "content-type" for key in headers):
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=body, method=target.method.upper(), headers=headers
    )

    started_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    status: int | None = None
    error: str | None = None
    error_kind: str | None = None
    ttfb_ms: float | None = None
    raw = b""

    try:
        with urllib.request.urlopen(request, timeout=scenario.timeout_s) as response:
            status = int(response.status)
            if scenario.stream:
                raw = response.read(1)
                ttfb_ms = _elapsed_ms(start)
                raw += response.read()
            else:
                raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except TimeoutError as exc:
        error = f"{type(exc).__name__}: {exc}"
        error_kind = ERROR_TIMEOUT
    except urllib.error.URLError as exc:
        error = f"{type(exc).__name__}: {exc}"
        error_kind = ERROR_TIMEOUT if isinstance(exc.reason, TimeoutError) else ERROR_TRANSPORT
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
        error_kind = ERROR_TRANSPORT

    if error_kind is None and status is not None:
        if status == 429:
            error_kind = ERROR_RATE_LIMITED
        elif status >= 500:
            error_kind = ERROR_SERVER
        elif status >= 400:
            error_kind = ERROR_CLIENT

    ok = error_kind is None and status is not None and 200 <= status < 400
    return RequestSample(
        index=index,
        started_at=started_at,
        status=status,
        ok=ok,
        error_kind=error_kind,
        error=error,
        duration_ms=_elapsed_ms(start),
        ttfb_ms=ttfb_ms,
        fanout=_extract(scenario, raw),
    )


def summarize(
    scenario: LoadScenario,
    samples: Sequence[RequestSample],
    elapsed_s: float,
) -> LoadReport:
    """把请求样本汇总成报告。"""

    total = len(samples)
    durations = [sample.duration_ms for sample in samples]
    failures = [sample for sample in samples if not sample.ok]

    status_counts: dict[str, int] = {}
    for sample in samples:
        key = str(sample.status) if sample.status is not None else "no-response"
        status_counts[key] = status_counts.get(key, 0) + 1

    error_kinds: dict[str, int] = {}
    for sample in failures:
        key = sample.error_kind or ERROR_TRANSPORT
        error_kinds[key] = error_kinds.get(key, 0) + 1

    ttfb_values = [sample.ttfb_ms for sample in samples if sample.ttfb_ms is not None]
    fanout, fanout_observed, limitations = _summarize_fanout(scenario, samples)

    report = LoadReport(
        scenario_id=scenario.id,
        concurrency=scenario.concurrency,
        requested=scenario.requests,
        completed=total,
        failed=len(failures),
        error_rate=(len(failures) / total) if total else 0.0,
        duration_s=round(elapsed_s, 4),
        throughput_rps=round(total / elapsed_s, 4),
        latency_p50_ms=percentile(durations, 0.50),
        latency_p90_ms=percentile(durations, 0.90),
        latency_p95_ms=percentile(durations, 0.95),
        latency_p99_ms=percentile(durations, 0.99),
        ttfb_observed=bool(ttfb_values),
        ttfb_p50_ms=percentile(ttfb_values, 0.50) if ttfb_values else None,
        ttfb_p95_ms=percentile(ttfb_values, 0.95) if ttfb_values else None,
        status_counts=dict(sorted(status_counts.items())),
        error_kinds=dict(sorted(error_kinds.items())),
        fanout_observed=fanout_observed,
        fanout=fanout,
        limitations=limitations,
        thresholds=scenario.thresholds,
    )
    report.passed, report.reasons = _judge(report)
    return report


def _summarize_fanout(
    scenario: LoadScenario,
    samples: Sequence[RequestSample],
) -> tuple[dict[str, dict[str, float]], bool, list[str]]:
    if not scenario.extract:
        return {}, False, []
    values: dict[str, list[float]] = {name: [] for name in scenario.extract}
    for sample in samples:
        for name, value in sample.fanout.items():
            values.setdefault(name, []).append(value)
    summary = {
        name: {"mean": round(sum(items) / len(items), 4), "p95": percentile(items, 0.95)}
        for name, items in values.items()
        if items
    }
    if summary:
        return summary, True, []
    return {}, False, [
        "no fan-out metrics were observed: the declared extract paths did not appear "
        "in any response"
    ]


def _judge(report: LoadReport) -> tuple[bool | None, list[str]]:
    limits = report.thresholds
    if limits is None:
        return None, []
    reasons: list[str] = []
    if limits.max_error_rate is not None and report.error_rate > limits.max_error_rate:
        reasons.append(
            f"error rate {report.error_rate:.4f} exceeds the maximum {limits.max_error_rate:.4f}"
        )
    if limits.max_p95_ms is not None and report.latency_p95_ms > limits.max_p95_ms:
        reasons.append(
            f"p95 latency {report.latency_p95_ms:.1f} ms exceeds the maximum "
            f"{limits.max_p95_ms:.1f} ms"
        )
    if (
        limits.min_throughput_rps is not None
        and report.throughput_rps < limits.min_throughput_rps
    ):
        reasons.append(
            f"throughput {report.throughput_rps:.4f} rps is below the minimum "
            f"{limits.min_throughput_rps:.4f}"
        )
    return (not reasons), reasons


def report_checks(report: LoadReport) -> list[CheckOutcome]:
    """把预算判定映射成判据结果，供运行记录统一消费。"""

    limits = report.thresholds
    if limits is None:
        return [
            CheckOutcome(
                name="load.budget",
                passed=True,
                expected="a declared budget",
                actual="not declared",
                message="no budget declared; the metrics are informational",
            )
        ]
    checks: list[CheckOutcome] = []
    if limits.max_error_rate is not None:
        checks.append(
            CheckOutcome(
                name="load.error_rate",
                passed=report.error_rate <= limits.max_error_rate,
                expected=f"<= {limits.max_error_rate}",
                actual=report.error_rate,
            )
        )
    if limits.max_p95_ms is not None:
        checks.append(
            CheckOutcome(
                name="load.p95_latency_ms",
                passed=report.latency_p95_ms <= limits.max_p95_ms,
                expected=f"<= {limits.max_p95_ms}",
                actual=report.latency_p95_ms,
            )
        )
    if limits.min_throughput_rps is not None:
        checks.append(
            CheckOutcome(
                name="load.throughput_rps",
                passed=report.throughput_rps >= limits.min_throughput_rps,
                expected=f">= {limits.min_throughput_rps}",
                actual=report.throughput_rps,
            )
        )
    return checks


def run_scenarios(
    scenarios: Sequence[LoadScenario],
    base_url: str | None = None,
    store: RunStore | None = None,
    run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Run:
    """执行全部场景，产出与既有各层一致的运行记录。"""

    scenario_list = list(scenarios)
    run = Run(
        run_id=run_id or new_run_id(),
        started_at=datetime.now(timezone.utc),
        metadata=dict(metadata or {}),
    )
    reports: list[LoadReport] = []
    for scenario in scenario_list:
        report = run_scenario(scenario, base_url=base_url)
        reports.append(report)
        checks = report_checks(report)
        status = Status.FAIL if report.passed is False else Status.PASS
        verdict = Verdict(case_id=scenario.id, status=status, checks=checks)
        verdict.duration_ms = round(report.duration_s * 1000, 3)
        verdict.metrics = CaseMetrics(
            calls=report.completed,
            retries=report.error_kinds.get(ERROR_RATE_LIMITED, 0),
            duration_ms=report.latency_p50_ms,
            usage_reported=report.fanout_observed,
        )
        run.verdicts.append(verdict)
    run.metadata["load_report"] = [report.model_dump(mode="json") for report in reports]
    run.metadata["metrics"] = _standard_metrics(reports).model_dump(mode="json")
    run.finished_at = datetime.now(timezone.utc)
    run.refresh_summary()
    if store is not None:
        store.save(run)
    return run


def _standard_metrics(reports: Sequence[LoadReport]) -> RunMetrics:
    """把压测报告映射到标准指标，使控制台无需新增代码路径即可展示。"""

    completed = sum(report.completed for report in reports)
    input_tokens = 0
    output_tokens = 0
    usage_reported = False
    for report in reports:
        for name, target in (("input_tokens", "input"), ("output_tokens", "output")):
            entry = report.fanout.get(name)
            if entry is None:
                continue
            total = int(entry.get("mean", 0.0) * report.completed)
            usage_reported = True
            if target == "input":
                input_tokens += total
            else:
                output_tokens += total
    violations = sorted(
        f"{report.scenario_id}:{check.name}"
        for report in reports
        for check in report_checks(report)
        if not check.passed
    )
    return RunMetrics(
        cases=len(reports),
        calls=completed,
        retries=sum(report.error_kinds.get(ERROR_RATE_LIMITED, 0) for report in reports),
        duration_p50_ms=percentile([report.latency_p50_ms for report in reports], 0.50),
        duration_p95_ms=max((report.latency_p95_ms for report in reports), default=0.0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usage_reported=usage_reported,
        budget_violations=violations,
    )


def _extract(scenario: LoadScenario, raw: bytes) -> dict[str, float]:
    if not scenario.extract or not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    extracted: dict[str, float] = {}
    for name, path in scenario.extract.items():
        value = _dig(payload, path)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        extracted[name] = float(value)
    return extracted


def _dig(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        return None
    return current


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)
