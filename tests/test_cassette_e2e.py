from __future__ import annotations

from pathlib import Path
from typing import Any

from agenteval.cassette import (
    Cassette,
    CassetteMode,
    CassetteSession,
    CassetteStore,
    wrap_registry,
)
from agenteval.fakes import build_demo_registry
from agenteval.models import Status
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore
from agenteval.tools import ToolRegistry, ToolResult

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REPLAYABLE = EXAMPLES / "replayable_cases.json"
FULL = EXAMPLES / "contract_cases.json"


class BombTool:
    """回放期间被调用即抛错，用来证明回放没有触达真实工具。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.input_schema = {"type": "object"}
        self.calls = 0

    def invoke(self, **kwargs: Any) -> ToolResult:
        self.calls += 1
        raise RuntimeError(f"real tool '{self.name}' must not be called during replay")


def make_runner(
    registry: ToolRegistry,
    run_store: RunStore,
    session: CassetteSession | None,
) -> ContractRunner:
    return ContractRunner(registry=registry, store=run_store, cassette_session=session)


def test_record_then_replay_is_hermetic(tmp_path: Path) -> None:
    cassette_store = CassetteStore(tmp_path / "cassettes")
    run_store = RunStore(tmp_path / "runs")
    cases = load_cases(REPLAYABLE)

    record_session = CassetteSession(
        cassette=Cassette("demo"), mode=CassetteMode.RECORD, store=cassette_store
    )
    recorded = make_runner(
        wrap_registry(build_demo_registry(), record_session), run_store, record_session
    ).run(cases, run_id="record-run")
    assert recorded.summary.passed == len(cases)
    assert cassette_store.exists("demo") is True

    replay_cassette = cassette_store.load("demo")
    names = sorted({interaction.target for interaction in replay_cassette.interactions})
    bombs = [BombTool(name) for name in names]
    replay_session = CassetteSession(
        cassette=replay_cassette, mode=CassetteMode.REPLAY, store=cassette_store
    )
    replayed = make_runner(
        wrap_registry(ToolRegistry(bombs), replay_session), run_store, replay_session
    ).run(cases, run_id="replay-run")

    assert replayed.summary.passed == len(cases)
    assert replayed.summary.errored == 0
    assert replayed.metadata["cassette"]["unused"] == []
    assert sum(bomb.calls for bomb in bombs) == 0


def test_replay_rejects_latency_dependent_check(tmp_path: Path) -> None:
    """耗时相关契约在回放模式下必须报错，而不是给出无意义的结论。"""

    cases = load_cases(FULL)
    timeout_case = next(case for case in cases if case.check.kind == "timeout")
    session = CassetteSession(cassette=Cassette("empty"), mode=CassetteMode.REPLAY)
    runner = make_runner(
        wrap_registry(ToolRegistry([BombTool("slow_tool")]), session),
        RunStore(tmp_path / "runs"),
        session,
    )
    verdict, trace = runner.run_case(timeout_case)
    assert verdict.status is Status.ERROR
    assert "cannot be replayed" in (verdict.error or "")
    assert [event.name for event in trace.events] == ["cassette_unreplayable_check"]


def test_record_mode_still_evaluates_latency_dependent_check(tmp_path: Path) -> None:
    cases = load_cases(FULL)
    timeout_case = next(case for case in cases if case.check.kind == "timeout")
    session = CassetteSession(cassette=Cassette("live"), mode=CassetteMode.RECORD)
    runner = make_runner(
        wrap_registry(build_demo_registry(), session),
        RunStore(tmp_path / "runs"),
        session,
    )
    verdict, _ = runner.run_case(timeout_case)
    assert verdict.status is Status.PASS


def test_unused_interactions_are_reported_and_strict_mode_fails(tmp_path: Path) -> None:
    cassette_store = CassetteStore(tmp_path / "cassettes")
    run_store = RunStore(tmp_path / "runs")
    cases = load_cases(REPLAYABLE)[:1]

    record_session = CassetteSession(
        cassette=Cassette("demo"), mode=CassetteMode.RECORD, store=cassette_store
    )
    make_runner(
        wrap_registry(build_demo_registry(), record_session), run_store, record_session
    ).run(cases, run_id="record-one")

    extra = Cassette("demo")
    cassette_store.append("demo", extra.record("ghost_tool", {"a": 1}, ToolResult(ok=True)))
    names = sorted({interaction.target for interaction in cassette_store.load("demo").interactions})

    lenient_session = CassetteSession(
        cassette=cassette_store.load("demo"), mode=CassetteMode.REPLAY, store=cassette_store
    )
    lenient = make_runner(
        wrap_registry(ToolRegistry([BombTool(name) for name in names]), lenient_session),
        run_store,
        lenient_session,
    ).run(cases, run_id="lenient-run")
    assert lenient.metadata["cassette"]["unused"]
    assert lenient.summary.failed == 0

    strict_session = CassetteSession(
        cassette=cassette_store.load("demo"),
        mode=CassetteMode.REPLAY,
        store=cassette_store,
        strict=True,
    )
    strict = make_runner(
        wrap_registry(ToolRegistry([BombTool(name) for name in names]), strict_session),
        run_store,
        strict_session,
    ).run(cases, run_id="strict-run")
    assert strict.summary.failed == 1
    assert any(verdict.case_id == "cassette:unused-interactions" for verdict in strict.verdicts)
