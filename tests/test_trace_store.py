from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.models import Run, Status, Trace, Verdict
from agenteval.trace_store import (
    TRACES_DIRNAME,
    TraceStore,
    extract_trace,
    resolve_traces_dir,
)


def make_trace(case_id: str = "case-a", event_count: int = 2) -> Trace:
    trace = Trace(case_id=case_id, attempts=3, side_effects=["k"])
    for index in range(event_count):
        trace.record(f"event-{index}", payload={"index": index})
    return trace


def make_run(traces: list[Trace]) -> Run:
    run = Run(run_id="r1", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [Verdict(case_id=trace.case_id, status=Status.PASS) for trace in traces]
    run.traces = traces
    run.refresh_summary()
    return run


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    original = make_trace()
    path = store.save("demo", original, source="run:r1")
    assert path.is_file()
    assert store.load("demo") == original


def test_metadata_records_source_and_counts(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    store.save("demo", make_trace(), source="run:r1")
    meta = store.metadata("demo")
    assert meta is not None
    assert (meta.name, meta.case_id, meta.event_count, meta.source) == (
        "demo",
        "case-a",
        2,
        "run:r1",
    )


def test_load_missing_trace_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        TraceStore(tmp_path / "traces").load("absent")


def test_list_traces_sorted(tmp_path: Path) -> None:
    store = TraceStore(tmp_path / "traces")
    for name in ("zeta", "alpha"):
        store.save(name, make_trace())
    assert store.list_traces() == ["alpha", "zeta"]


def test_list_traces_empty_when_dir_absent(tmp_path: Path) -> None:
    assert TraceStore(tmp_path / "missing").list_traces() == []


def test_resolve_traces_dir_default_and_env_override(tmp_path: Path) -> None:
    assert resolve_traces_dir(base=tmp_path, env={}) == tmp_path / ".agenteval" / TRACES_DIRNAME
    override = tmp_path / "custom"
    assert (
        resolve_traces_dir(base=tmp_path, env={"AGENTEVAL_HOME": str(override)})
        == override / TRACES_DIRNAME
    )


def test_extract_trace_by_case_id() -> None:
    run = make_run([make_trace("a"), make_trace("b")])
    assert extract_trace(run, "b").case_id == "b"


def test_extract_trace_requires_case_id_when_ambiguous() -> None:
    run = make_run([make_trace("a"), make_trace("b")])
    with pytest.raises(KeyError, match="specify --case"):
        extract_trace(run)


def test_extract_trace_uses_single_trace() -> None:
    run = make_run([make_trace("only")])
    assert extract_trace(run).case_id == "only"


def test_extract_trace_missing_case_reports() -> None:
    run = make_run([make_trace("a")])
    with pytest.raises(KeyError, match="no trace for case"):
        extract_trace(run, "ghost")


def test_extract_trace_empty_run_reports() -> None:
    with pytest.raises(KeyError, match="contains no traces"):
        extract_trace(make_run([]))
