"""把运行记录导出为 OTLP/HTTP JSON 轨迹。

语义约定优先：工具调用与 token 用量用约定名，平台自有字段放 ``agenteval.*``。
标识确定性生成，同一份运行重复导出得到相同标识，后端可据此去重。
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from agenteval.models import Run, Status, Trace
from agenteval.process import extract_tool_calls

SCOPE_NAME = "agenteval"
SCOPE_VERSION = "0.1.0"
SERVICE_NAME = "agenteval"
DEFAULT_TIMEOUT_S = 10.0

SPAN_KIND_INTERNAL = 1
STATUS_CODE_OK = 1
STATUS_CODE_ERROR = 2

OPERATION_EXECUTE_TOOL = "execute_tool"


class ExportError(RuntimeError):
    """导出失败，消息中包含端点与原因。"""


@dataclass(frozen=True)
class ExportResult:
    endpoint: str
    spans: int
    status: int
    payload: dict[str, Any]


def trace_id_for(run_id: str) -> str:
    """32 位十六进制的 trace 标识，对同一运行稳定。"""

    return hashlib.sha256(f"trace:{run_id}".encode("utf-8")).hexdigest()[:32]


def span_id_for(*parts: object) -> str:
    """16 位十六进制的 span 标识，对同一组部件稳定。"""

    key = "span:" + ":".join(str(part) for part in parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _nanos(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return str(int(value.timestamp() * 1_000_000_000))


def _attribute(key: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    if isinstance(value, (list, tuple)):
        values = [
            attribute["value"]
            for item in value
            if (attribute := _attribute("", item)) is not None
        ]
        return {"key": key, "value": {"arrayValue": {"values": values}}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _attributes(pairs: Mapping[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    for key, value in pairs.items():
        attribute = _attribute(key, value)
        if attribute is not None:
            collected.append(attribute)
    return collected


def _span(
    *,
    name: str,
    trace_id: str,
    span_id: str,
    parent_id: str | None,
    start: datetime,
    end: datetime,
    attributes: Mapping[str, Any],
    failed: bool = False,
    message: str | None = None,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": SPAN_KIND_INTERNAL,
        "startTimeUnixNano": _nanos(start),
        "endTimeUnixNano": _nanos(end),
        "attributes": _attributes(attributes),
        "status": {"code": STATUS_CODE_ERROR if failed else STATUS_CODE_OK},
    }
    if parent_id is not None:
        span["parentSpanId"] = parent_id
    if message:
        span["status"]["message"] = message
    return span


def _case_window(trace: Trace | None, fallback: datetime) -> tuple[datetime, datetime]:
    if trace is None or not trace.events:
        return fallback, fallback
    return trace.events[0].at, trace.events[-1].at


def build_otlp_payload(run: Run) -> dict[str, Any]:
    """把一次运行转换为 OTLP/HTTP JSON 结构。"""

    trace_id = trace_id_for(run.run_id)
    root_span_id = span_id_for(run.run_id, "run")
    started = run.started_at
    finished = run.finished_at or run.started_at
    summary = run.summary

    spans: list[dict[str, Any]] = [
        _span(
            name="agenteval.run",
            trace_id=trace_id,
            span_id=root_span_id,
            parent_id=None,
            start=started,
            end=finished,
            attributes={
                "gen_ai.operation.name": "agenteval.run",
                "agenteval.run.id": run.run_id,
                "agenteval.run.total": summary.total,
                "agenteval.run.passed": summary.passed,
                "agenteval.run.failed": summary.failed,
                "agenteval.run.errored": summary.errored,
            },
            failed=summary.failed > 0 or summary.errored > 0,
        )
    ]

    traces = {trace.case_id: trace for trace in run.traces}
    for case_index, verdict in enumerate(run.verdicts):
        case_span_id = span_id_for(run.run_id, "case", case_index, verdict.case_id)
        case_trace = traces.get(verdict.case_id)
        case_start, case_end = _case_window(case_trace, started)
        spans.append(
            _span(
                name=f"agenteval.case {verdict.case_id}",
                trace_id=trace_id,
                span_id=case_span_id,
                parent_id=root_span_id,
                start=case_start,
                end=case_end,
                attributes={
                    "gen_ai.operation.name": "agenteval.case",
                    "agenteval.case.id": verdict.case_id,
                    "agenteval.case.status": verdict.status.value,
                    "agenteval.case.duration_ms": verdict.duration_ms,
                    "agenteval.case.failed_checks": [
                        check.name for check in verdict.failed_checks
                    ],
                },
                failed=verdict.status is not Status.PASS,
                message=verdict.error,
            )
        )
        if case_trace is None:
            continue
        for call_index, call in enumerate(extract_tool_calls(case_trace)):
            usage = call.usage
            spans.append(
                _span(
                    name=f"{OPERATION_EXECUTE_TOOL} {call.target}",
                    trace_id=trace_id,
                    span_id=span_id_for(
                        run.run_id, "tool", case_index, call_index, call.target
                    ),
                    parent_id=case_span_id,
                    start=case_trace.events[min(call_index, len(case_trace.events) - 1)].at,
                    end=case_trace.events[min(call_index, len(case_trace.events) - 1)].at,
                    attributes={
                        "gen_ai.operation.name": OPERATION_EXECUTE_TOOL,
                        "gen_ai.tool.name": call.target,
                        "gen_ai.tool.call.id": f"{run.run_id}:{case_index}:{call_index}",
                        "gen_ai.usage.input_tokens": usage.input_tokens if usage else None,
                        "gen_ai.usage.output_tokens": usage.output_tokens if usage else None,
                        "agenteval.tool.ok": call.ok,
                        "agenteval.tool.error_kind": call.error_kind,
                        "agenteval.tool.attempts": call.attempts,
                        "agenteval.tool.arguments": (
                            json.dumps(call.args, ensure_ascii=False) if call.args else None
                        ),
                    },
                    failed=not call.ok,
                )
            )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes({"service.name": SERVICE_NAME})
                },
                "scopeSpans": [
                    {
                        "scope": {"name": SCOPE_NAME, "version": SCOPE_VERSION},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def count_spans(payload: Mapping[str, Any]) -> int:
    total = 0
    for resource in payload.get("resourceSpans", []):
        for scope in resource.get("scopeSpans", []):
            total += len(scope.get("spans", []))
    return total


def export_run(
    run: Run,
    endpoint: str,
    headers: Mapping[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> ExportResult:
    """构造并推送 OTLP/HTTP JSON；失败时抛出 :class:`ExportError`。"""

    payload = build_otlp_payload(run)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **dict(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            status = int(response.status)
            response.read()
    except urllib.error.HTTPError as exc:
        raise ExportError(
            f"endpoint {endpoint} returned {exc.code}: {exc.read()[:200]!r}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExportError(f"could not reach endpoint {endpoint}: {exc}") from exc
    if not 200 <= status < 300:
        raise ExportError(f"endpoint {endpoint} returned {status}")
    return ExportResult(
        endpoint=endpoint,
        spans=count_spans(payload),
        status=status,
        payload=payload,
    )
