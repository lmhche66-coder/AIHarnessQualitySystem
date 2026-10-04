from __future__ import annotations

from pathlib import Path

from agenteval.gate import (
    BASELINES_DIRNAME,
    Baseline,
    BaselineStore,
    resolve_baselines_dir,
)
from agenteval.models import Run, Status, Verdict


def make_run(run_id: str, statuses: dict[str, Status]) -> Run:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z")
    run.verdicts = [Verdict(case_id=case_id, status=status) for case_id, status in statuses.items()]
    run.refresh_summary()
    return run


def test_baseline_from_run_captures_verdict_statuses() -> None:
    baseline = Baseline.from_run(make_run("r1", {"a": Status.PASS, "b": Status.FAIL}), name="main")
    assert baseline.run_id == "r1"
    assert baseline.name == "main"
    assert baseline.verdicts == {"a": Status.PASS, "b": Status.FAIL}


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    store = BaselineStore(tmp_path / "baselines")
    baseline = Baseline.from_run(make_run("r1", {"a": Status.PASS}), name="main")
    path = store.save(baseline)
    assert path.is_file()
    assert store.load("main") == baseline


def test_load_missing_baseline_returns_none(tmp_path: Path) -> None:
    assert BaselineStore(tmp_path / "baselines").load("absent") is None


def test_list_baselines_sorted(tmp_path: Path) -> None:
    store = BaselineStore(tmp_path / "baselines")
    for name in ("zeta", "alpha"):
        store.save(Baseline.from_run(make_run("r1", {"a": Status.PASS}), name=name))
    assert store.list_baselines() == ["alpha", "zeta"]


def test_resolve_baselines_dir_default_and_env_override(tmp_path: Path) -> None:
    assert (
        resolve_baselines_dir(base=tmp_path, env={}) == tmp_path / ".agenteval" / BASELINES_DIRNAME
    )
    override = tmp_path / "custom"
    assert (
        resolve_baselines_dir(base=tmp_path, env={"AGENTEVAL_HOME": str(override)})
        == override / BASELINES_DIRNAME
    )
