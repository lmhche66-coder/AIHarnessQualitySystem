from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.cassette import (
    Cassette,
    CassetteMode,
    CassetteSession,
    CassetteStore,
    CassetteTool,
    wrap_registry,
)
from agenteval.models import Trace
from agenteval.tools import ToolErrorKind, ToolRegistry, ToolResult


class CountingTool:
    """记录真实调用次数，用于验证回放不触达真实工具。"""

    def __init__(self, name: str = "counter", responses: list[ToolResult] | None = None) -> None:
        self.name = name
        self.input_schema = {"type": "object", "properties": {}}
        self.calls = 0
        self._responses = list(responses or [ToolResult(ok=True, value={"ok": True})])

    def invoke(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        if self.calls <= len(self._responses):
            return self._responses[self.calls - 1]
        return self._responses[-1]


def test_record_mode_calls_real_tool_and_stores_result() -> None:
    tool = CountingTool()
    session = CassetteSession(cassette=Cassette("c"), mode=CassetteMode.RECORD)
    result = CassetteTool(tool, session).invoke(a=1)
    assert result.ok is True
    assert tool.calls == 1
    assert len(session.cassette.interactions) == 1
    assert session.cassette.interactions[0].target == "counter"


def test_record_mode_stores_failures_too() -> None:
    failure = ToolResult(ok=False, error_kind=ToolErrorKind.RATE_LIMITED, error_message="slow down")
    session = CassetteSession(cassette=Cassette("c"), mode=CassetteMode.RECORD)
    CassetteTool(CountingTool(responses=[failure]), session).invoke()
    assert session.cassette.interactions[0].response.error_kind is ToolErrorKind.RATE_LIMITED


def test_replay_does_not_touch_real_tool() -> None:
    cassette = Cassette("c")
    cassette.record("counter", {}, ToolResult(ok=True, value={"replayed": True}))
    cassette.reset_cursors()
    tool = CountingTool()
    session = CassetteSession(cassette=cassette, mode=CassetteMode.REPLAY)
    result = CassetteTool(tool, session).invoke()
    assert result.value == {"replayed": True}
    assert tool.calls == 0


def test_replay_returns_missing_error_without_calling_tool() -> None:
    tool = CountingTool()
    session = CassetteSession(cassette=Cassette("c"), mode=CassetteMode.REPLAY)
    result = CassetteTool(tool, session).invoke()
    assert result.ok is False
    assert result.error_kind is ToolErrorKind.CASSETTE_MISS
    assert tool.calls == 0


def test_replay_reproduces_recorded_retry_sequence() -> None:
    cassette = Cassette("c")
    responses = [
        ToolResult(ok=False, error_kind=ToolErrorKind.RATE_LIMITED, error_message="1"),
        ToolResult(ok=False, error_kind=ToolErrorKind.RATE_LIMITED, error_message="2"),
        ToolResult(ok=True, value={"calls": 3}),
    ]
    for response in responses:
        cassette.record("counter", {}, response)
    cassette.reset_cursors()
    tool = CountingTool()
    session = CassetteSession(cassette=cassette, mode=CassetteMode.REPLAY)
    wrapped = CassetteTool(tool, session)
    assert [wrapped.invoke().ok for _ in range(3)] == [False, False, True]
    assert tool.calls == 0


def test_replay_exhaustion_is_a_miss() -> None:
    cassette = Cassette("c")
    cassette.record("counter", {}, ToolResult(ok=True))
    cassette.reset_cursors()
    session = CassetteSession(cassette=cassette, mode=CassetteMode.REPLAY)
    wrapped = CassetteTool(CountingTool(), session)
    assert wrapped.invoke().ok is True
    assert wrapped.invoke().error_kind is ToolErrorKind.CASSETTE_MISS


def test_auto_mode_replays_when_interaction_exists() -> None:
    cassette = Cassette("c")
    cassette.record("counter", {}, ToolResult(ok=True, value={"source": "cassette"}))
    cassette.reset_cursors()
    tool = CountingTool()
    session = CassetteSession(cassette=cassette, mode=CassetteMode.AUTO)
    result = CassetteTool(tool, session).invoke()
    assert result.value == {"source": "cassette"}
    assert tool.calls == 0


def test_auto_mode_records_when_interaction_missing(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    tool = CountingTool()
    session = CassetteSession(cassette=Cassette("c"), mode=CassetteMode.AUTO, store=store)
    result = CassetteTool(tool, session).invoke()
    assert result.ok is True
    assert tool.calls == 1
    assert store.exists("c") is True


def test_trace_records_hit_and_miss() -> None:
    cassette = Cassette("c")
    cassette.record("counter", {}, ToolResult(ok=True))
    cassette.reset_cursors()
    session = CassetteSession(cassette=cassette, mode=CassetteMode.REPLAY)
    wrapped = CassetteTool(CountingTool(), session)

    hit_trace = Trace(case_id="hit")
    session.begin_case(hit_trace)
    wrapped.invoke()
    session.end_case()

    miss_trace = Trace(case_id="miss")
    session.begin_case(miss_trace)
    wrapped.invoke()
    session.end_case()

    assert [event.name for event in hit_trace.events] == ["cassette_hit"]
    assert [event.name for event in miss_trace.events] == ["cassette_miss"]
    assert miss_trace.events[0].error is not None


def test_wrap_registry_preserves_names_and_schema() -> None:
    registry = ToolRegistry([CountingTool("alpha"), CountingTool("beta")])
    session = CassetteSession(cassette=Cassette("c"), mode=CassetteMode.REPLAY)
    wrapped = wrap_registry(registry, session)
    assert wrapped.names() == ["alpha", "beta"]
    assert wrapped.get("alpha").input_schema == registry.get("alpha").input_schema
