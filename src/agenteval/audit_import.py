"""外部工具调用审计到平台轨迹的导入。

以「工具调用审计」为中心而不是以某个框架为中心：工具名、参数、三态生命周期、
起止时间与耗时是跨框架都成立的观察面，接入方只需导出这些字段。

保真度是硬约束：审计通常只保留有界结果摘要而非原始返回值。缺失就是缺失，
不能把摘要塞进返回值字段，否则状态传递判据会产生假失败。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import Trace, TraceEvent
from agenteval.tools import ToolErrorKind

TOOL_CALL_EVENT = "tool_call"

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_STARTED = "started"


class AuditImportError(ValueError):
    """导入失败，消息中指明出错的记录位置。"""


@dataclass(frozen=True)
class AuditRecord:
    """一条归一化后的工具调用审计记录。"""

    id: str
    tool_name: str
    status: str
    arguments: dict[str, Any]
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    result_summary: str | None = None
    error_message: str | None = None
    value: Any = None
    has_value: bool = False


class ImportReport(BaseModel):
    """导入结果与保真度说明。"""

    model_config = ConfigDict(extra="forbid")

    records: int
    case_id: str
    trace_name: str
    limitations: list[str] = Field(default_factory=list)


def load_audit_records(path: Path) -> list[AuditRecord]:
    """按扩展名选择解析方式，载入审计记录。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        return parse_csv_records(text)
    return parse_json_records(json.loads(text))


def parse_json_records(payload: Any) -> list[AuditRecord]:
    """解析 JSON 审计导出；接受裸数组，也接受带 items 或 records 键的对象。"""

    if isinstance(payload, Mapping):
        for key in ("items", "records", "toolCalls"):
            if key in payload:
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise AuditImportError("audit input must be a list of records")
    if not payload:
        raise AuditImportError("audit input contains no records")
    return [_record_from_mapping(item, index) for index, item in enumerate(payload)]


def parse_csv_records(text: str) -> list[AuditRecord]:
    """解析审计 CSV 导出。"""

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise AuditImportError("audit input contains no records")
    return [_record_from_mapping(row, index) for index, row in enumerate(rows)]


def _record_from_mapping(item: Any, index: int) -> AuditRecord:
    where = f"record #{index}"
    if not isinstance(item, Mapping):
        raise AuditImportError(f"{where} is not an object")

    tool_name = str(item.get("toolName") or item.get("tool_name") or "").strip()
    if not tool_name:
        raise AuditImportError(f"{where} is missing toolName")

    started_at = _parse_datetime(item.get("startedAt") or item.get("started_at"))
    if started_at is None:
        raise AuditImportError(f"{where} is missing startedAt")

    arguments = _parse_arguments(item, where)
    has_value = "value" in item
    duration_raw = item.get("durationMs", item.get("duration_ms"))
    duration_ms: int | None
    if duration_raw in (None, ""):
        duration_ms = None
    else:
        try:
            duration_ms = int(duration_raw)
        except (TypeError, ValueError) as exc:
            raise AuditImportError(f"{where} has a non-numeric durationMs") from exc

    return AuditRecord(
        id=str(item.get("id") or f"{where}"),
        tool_name=tool_name,
        status=str(item.get("status") or STATUS_STARTED).strip().lower(),
        arguments=arguments,
        started_at=started_at,
        completed_at=_parse_datetime(item.get("completedAt") or item.get("completed_at")),
        duration_ms=duration_ms,
        result_summary=_optional_text(item.get("resultSummary") or item.get("result_summary")),
        error_message=_optional_text(item.get("errorMessage") or item.get("error_message")),
        value=item.get("value"),
        has_value=has_value,
    )


def _parse_arguments(item: Mapping[str, Any], where: str) -> dict[str, Any]:
    raw = item.get("arguments")
    if raw is None:
        raw = item.get("argumentsJson") or item.get("arguments_json")
        if raw in (None, ""):
            return {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise AuditImportError(f"{where} has unparseable argumentsJson") from exc
    if not isinstance(raw, Mapping):
        raise AuditImportError(f"{where} arguments must be an object")
    return dict(raw)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuditImportError(f"invalid timestamp: {value!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _error_kind(status: str) -> str | None:
    if status == STATUS_COMPLETED:
        return None
    if status == STATUS_FAILED:
        return ToolErrorKind.FAILED.value
    return ToolErrorKind.INTERRUPTED.value


def build_trace(records: Sequence[AuditRecord], case_id: str) -> Trace:
    """把审计记录转成轨迹，按开始时间排序。

    只有真正拿到原始返回值时才写入 ``value`` 字段；该字段的缺失本身就是信息，
    会被归一化层识别为「值未被记录」。
    """

    ordered = sorted(records, key=lambda record: (record.started_at, record.id))
    trace = Trace(case_id=case_id)
    for record in ordered:
        payload: dict[str, Any] = {
            "target": record.tool_name,
            "args": dict(record.arguments),
            "ok": record.status == STATUS_COMPLETED,
            "error_kind": _error_kind(record.status),
            "attempts": 1,
        }
        if record.has_value:
            payload["value"] = record.value
        if record.result_summary is not None:
            payload["result_summary"] = record.result_summary
        trace.events.append(
            TraceEvent(
                seq=len(trace.events) + 1,
                name=TOOL_CALL_EVENT,
                at=record.completed_at or record.started_at,
                payload=payload,
                error=record.error_message,
            )
        )
    return trace


def fidelity_report(records: Sequence[AuditRecord]) -> list[str]:
    """列明源数据缺失导致的不可评估项。"""

    limitations: list[str] = []
    if records and not any(record.has_value for record in records):
        limitations.append(
            "no raw result values were recorded; the state_continuity check "
            "cannot be evaluated on this trace"
        )
    interrupted = [record for record in records if record.status == STATUS_STARTED]
    if interrupted:
        limitations.append(
            f"{len(interrupted)} call(s) have no completion; they are recorded as "
            "interrupted without a result or duration"
        )
    return limitations
