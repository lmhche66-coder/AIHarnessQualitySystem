from __future__ import annotations

from agenteval.cassette import Cassette, fingerprint, redact
from agenteval.tools import ToolErrorKind, ToolResult


def test_fingerprint_ignores_argument_key_order() -> None:
    assert fingerprint("t", {"a": 1, "b": 2}) == fingerprint("t", {"b": 2, "a": 1})


def test_fingerprint_differs_for_different_values() -> None:
    assert fingerprint("t", {"a": 1}) != fingerprint("t", {"a": 2})


def test_fingerprint_differs_for_different_targets() -> None:
    assert fingerprint("a", {"x": 1}) != fingerprint("b", {"x": 1})


def test_redact_replaces_declared_fields_only() -> None:
    assert redact({"token": "s3cr3t", "message": "hi"}) == {"token": "***", "message": "hi"}


def test_redact_is_case_insensitive() -> None:
    assert redact({"API_KEY": "s3cr3t"}) == {"API_KEY": "***"}


def test_record_redacts_payload_but_keeps_fingerprint_matchable() -> None:
    cassette = Cassette("c")
    cassette.record("t", {"token": "s3cr3t", "message": "hi"}, ToolResult(ok=True))
    stored = cassette.interactions[0]
    assert stored.request == {"token": "***", "message": "hi"}
    cassette.reset_cursors()
    assert cassette.lookup("t", {"token": "s3cr3t", "message": "hi"}) is not None


def test_lookup_returns_recorded_sequence_in_order() -> None:
    cassette = Cassette("c")
    responses = [
        ToolResult(ok=False, error_kind=ToolErrorKind.RATE_LIMITED, error_message="1"),
        ToolResult(ok=False, error_kind=ToolErrorKind.RATE_LIMITED, error_message="2"),
        ToolResult(ok=True, value={"calls": 3}),
    ]
    for response in responses:
        cassette.record("t", {"a": 1}, response)
    cassette.reset_cursors()
    replayed = []
    for _ in range(3):
        interaction = cassette.lookup("t", {"a": 1})
        assert interaction is not None
        replayed.append(interaction.response)
    assert [item.ok for item in replayed] == [False, False, True]
    assert [item.error_message for item in replayed[:2]] == ["1", "2"]


def test_lookup_returns_none_when_sequence_is_exhausted() -> None:
    cassette = Cassette("c")
    cassette.record("t", {"a": 1}, ToolResult(ok=True))
    cassette.reset_cursors()
    assert cassette.lookup("t", {"a": 1}) is not None
    assert cassette.lookup("t", {"a": 1}) is None


def test_unused_reports_unconsumed_interactions() -> None:
    cassette = Cassette("c")
    cassette.record("t", {"a": 1}, ToolResult(ok=True))
    cassette.record("t", {"a": 2}, ToolResult(ok=True))
    cassette.reset_cursors()
    cassette.lookup("t", {"a": 1})
    unused = cassette.unused()
    assert len(unused) == 1
    assert unused[0].request == {"a": 2}


def test_recorded_interactions_are_not_reported_unused() -> None:
    cassette = Cassette("c")
    cassette.record("t", {"a": 1}, ToolResult(ok=True))
    assert cassette.unused() == []
