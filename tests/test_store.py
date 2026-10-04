from __future__ import annotations

from pathlib import Path

import pytest

from agenteval.models import Run, Status, Trace, Verdict
from agenteval.store import (
    RUNS_DIRNAME,
    TRACES_FILENAME,
    VERDICTS_FILENAME,
    RunStore,
    resolve_home,
    resolve_runs_dir,
)


def make_run(run_id: str = "run-1") -> Run:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T00:00:01Z")
    run.verdicts = [
        Verdict(case_id="a", status=Status.PASS),
        Verdict(case_id="b", status=Status.FAIL),
    ]
    run.traces = [Trace(case_id="a", attempts=1)]
    run.refresh_summary()
    return run


def test_save_creates_directory_and_files(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    target = store.save(make_run())
    assert target.is_dir()
    assert (target / "summary.json").is_file()
    assert (target / VERDICTS_FILENAME).is_file()
    assert (target / TRACES_FILENAME).is_file()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    original = make_run()
    store.save(original)
    restored = store.load(original.run_id)
    assert restored == original


def test_load_missing_run_raises(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    with pytest.raises(FileNotFoundError):
        store.load("does-not-exist")


def test_list_runs_sorted(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    store.save(make_run("b-run"))
    store.save(make_run("a-run"))
    assert store.list_runs() == ["a-run", "b-run"]


def test_list_runs_empty_when_dir_absent(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "missing")
    assert store.list_runs() == []


def test_resolve_runs_dir_defaults_under_base(tmp_path: Path) -> None:
    resolved = resolve_runs_dir(base=tmp_path, env={})
    assert resolved == tmp_path / ".agenteval" / RUNS_DIRNAME


def test_resolve_home_prefers_env_override(tmp_path: Path) -> None:
    override = tmp_path / "custom-home"
    assert resolve_home(base=tmp_path, env={"AGENTEVAL_HOME": str(override)}) == override
    assert resolve_runs_dir(base=tmp_path, env={"AGENTEVAL_HOME": str(override)}) == override / RUNS_DIRNAME


def test_run_store_default_uses_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTEVAL_HOME", str(tmp_path / "env-home"))
    store = RunStore.default()
    assert store.runs_dir == tmp_path / "env-home" / RUNS_DIRNAME
