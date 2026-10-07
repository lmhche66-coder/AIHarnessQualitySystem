from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.audit_import import (
    AuditImportError,
    build_trace,
    fidelity_report,
    load_audit_records,
    parse_csv_records,
    parse_json_records,
)
from agenteval.process import extract_tool_calls
from agenteval.tools import ToolErrorKind

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
JSON_FIXTURE = EXAMPLES / "audit_export.json"
CSV_FIXTURE = EXAMPLES / "audit_export.csv"


def test_parse_json_records_from_example() -> None:
    records = load_audit_records(JSON_FIXTURE)
    assert [record.tool_name for record in records] == [
        "knowledge_retrieval",
        "ListTopics",
        "SearchLog",
        "SearchLog",
        "knowledge_retrieval",
    ]


def test_csv_and_json_produce_equivalent_traces() -> None:
    from_json = build_trace(load_audit_records(JSON_FIXTURE), "case-1")
    from_csv = build_trace(load_audit_records(CSV_FIXTURE), "case-1")
    assert from_csv == from_json


def test_records_are_ordered_by_start_time() -> None:
    records = load_audit_records(JSON_FIXTURE)
    trace = build_trace(list(reversed(records)), "case-1")
    started = [event.at for event in trace.events]
    assert started == sorted(started)


def test_empty_input_is_rejected() -> None:
    with pytest.raises(AuditImportError, match="no records"):
        parse_json_records([])
    with pytest.raises(AuditImportError, match="no records"):
        parse_csv_records("id,toolName\n")


def test_missing_tool_name_is_rejected_with_position() -> None:
    with pytest.raises(AuditImportError, match=r"record #1 is missing toolName"):
        parse_json_records(
            [
                {"toolName": "a", "startedAt": "2026-01-01T00:00:00Z"},
                {"startedAt": "2026-01-01T00:00:00Z"},
            ]
        )


def test_missing_started_at_is_rejected_with_position() -> None:
    with pytest.raises(AuditImportError, match=r"record #0 is missing startedAt"):
        parse_json_records([{"toolName": "a"}])


def test_unparseable_arguments_are_rejected() -> None:
    with pytest.raises(AuditImportError, match="unparseable argumentsJson"):
        parse_json_records(
            [
                {
                    "toolName": "a",
                    "startedAt": "2026-01-01T00:00:00Z",
                    "argumentsJson": "{not json",
                }
            ]
        )


def test_non_object_arguments_are_rejected() -> None:
    with pytest.raises(AuditImportError, match="arguments must be an object"):
        parse_json_records(
            [{"toolName": "a", "startedAt": "2026-01-01T00:00:00Z", "arguments": "nope"}]
        )


def test_lifecycle_mapping_covers_completed_failed_and_interrupted() -> None:
    calls = extract_tool_calls(build_trace(load_audit_records(JSON_FIXTURE), "case-1"))
    by_index = {call.index: call for call in calls}
    assert by_index[0].ok is True and by_index[0].error_kind is None
    assert by_index[2].ok is False and by_index[2].error_kind == ToolErrorKind.FAILED.value
    assert by_index[2].value_recorded is False
    assert by_index[4].ok is False and by_index[4].error_kind == ToolErrorKind.INTERRUPTED.value


def test_interrupted_call_carries_no_value() -> None:
    trace = build_trace(load_audit_records(JSON_FIXTURE), "case-1")
    interrupted = trace.events[-1]
    assert "value" not in interrupted.payload
    assert interrupted.payload["error_kind"] == ToolErrorKind.INTERRUPTED.value
    assert interrupted.error is None


def test_event_time_prefers_completion() -> None:
    trace = build_trace(load_audit_records(JSON_FIXTURE), "case-1")
    assert trace.events[0].at.isoformat().startswith("2026-10-05T02:10:00.480")


def test_fidelity_report_flags_missing_raw_values() -> None:
    limitations = fidelity_report(load_audit_records(JSON_FIXTURE))
    assert any("state_continuity" in item for item in limitations)
    assert any("no completion" in item for item in limitations)


def test_fidelity_report_is_silent_when_values_are_present() -> None:
    records = parse_json_records(
        [
            {
                "toolName": "a",
                "status": "completed",
                "startedAt": "2026-01-01T00:00:00Z",
                "completedAt": "2026-01-01T00:00:01Z",
                "arguments": {},
                "value": {"id": "o-1"},
            }
        ]
    )
    assert fidelity_report(records) == []
    calls = extract_tool_calls(build_trace(records, "c"))
    assert calls[0].value_recorded is True
    assert calls[0].value == {"id": "o-1"}


def test_json_wrapper_keys_are_accepted() -> None:
    payload = {
        "items": [
            {"toolName": "a", "status": "completed", "startedAt": "2026-01-01T00:00:00Z"}
        ],
        "total": 1,
    }
    assert len(parse_json_records(payload)) == 1
