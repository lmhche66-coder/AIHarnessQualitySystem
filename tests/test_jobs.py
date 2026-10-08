from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.jobs import JobRecord, JobStatus, JobStore, new_job_id

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def write_registry(tmp_path: Path) -> Path:
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "ok",
                        "kind": "task",
                        "checks": [{"kind": "no_extra_calls", "allowed": ["echo_tool"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "datasets.json"
    registry.write_text(
        json.dumps({"datasets": {"basic_function": ["cases.json"]}}), encoding="utf-8"
    )
    return registry


def make_job(home: Path, registry: Path, **overrides) -> JobRecord:
    record = JobRecord(
        id=new_job_id(),
        scope="basic_function",
        datasets_path=str(registry.resolve()),
        registry="agenteval.fakes:build_agent_registry",
        agent="agenteval.fakes:build_noop_task_agent",
        **overrides,
    )
    JobStore.for_home(home).save(record)
    return record


# --- 作业存储 ---------------------------------------------------------------


def test_job_store_roundtrip_and_cancel(tmp_path: Path) -> None:
    store = JobStore.for_home(tmp_path)
    record = JobRecord(id="j1", scope="tool_call", datasets_path="d.json", demo=True)
    store.save(record)
    assert store.load("j1").status is JobStatus.PENDING
    assert [r.id for r in store.list()] == ["j1"]

    cancelled = store.cancel("j1")
    assert cancelled.cancel_requested is True
    assert store.load("j1").cancel_requested is True


def test_cancel_is_noop_for_finished_job(tmp_path: Path) -> None:
    store = JobStore.for_home(tmp_path)
    store.save(
        JobRecord(id="j1", scope="tool_call", datasets_path="d.json", status=JobStatus.COMPLETED)
    )
    assert store.cancel("j1").cancel_requested is False


def test_job_store_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        JobStore.for_home(tmp_path).load("nope")


# --- 执行作业（worker 路径）-------------------------------------------------


def test_eval_run_job_marks_completed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    job = make_job(home, write_registry(tmp_path))
    code = main(["--home", str(home), "eval", "run", "--job", job.id])
    capsys.readouterr()

    stored = JobStore.for_home(home).load(job.id)
    assert code == 0
    assert stored.status is JobStatus.COMPLETED
    assert stored.run_id
    assert stored.primary_pass_rate == 1.0


def test_eval_run_job_marks_cancelled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    job = make_job(home, write_registry(tmp_path))
    # 提前置位取消标志：执行器在第一条用例前就会停下
    JobStore.for_home(home).cancel(job.id)
    code = main(["--home", str(home), "eval", "run", "--job", job.id])
    capsys.readouterr()

    stored = JobStore.for_home(home).load(job.id)
    assert stored.status is JobStatus.CANCELLED
    assert code == 1


def test_eval_run_requires_scope_and_datasets_without_job(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--home", str(tmp_path / "home"), "eval", "run"])
    capsys.readouterr()
    assert code == 2


# --- 轮询与取消命令 ---------------------------------------------------------


def test_eval_status_and_cancel_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    job = make_job(home, write_registry(tmp_path), status=JobStatus.RUNNING)

    assert main(["--home", str(home), "eval", "status", job.id]) == 0
    assert job.id in capsys.readouterr().out

    assert main(["--home", str(home), "eval", "cancel", job.id]) == 0
    capsys.readouterr()
    assert JobStore.for_home(home).load(job.id).cancel_requested is True

    assert main(["--home", str(home), "eval", "status"]) == 0
    assert job.id in capsys.readouterr().out


# --- §6.4 轮次间同步 --------------------------------------------------------


def test_dialogue_turn_settle_waits_between_turns() -> None:
    from agenteval.dialogue import DialogueRunner, DialogueCase
    from agenteval.tools import ToolRegistry

    case = DialogueCase(
        id="d1",
        script=["你好", "继续"],
        checks=[{"kind": "termination", "marker": "好的"}],
    )
    runner = DialogueRunner(
        agent=lambda registry, conversation: "好的",
        environment=lambda: ToolRegistry([]),
        turn_settle_s=0.02,
    )
    started = time.perf_counter()
    verdict, trace, conversation = runner.run_case(case)
    elapsed = time.perf_counter() - started

    assert verdict.status.value == "pass"
    assert len(conversation.agent_turns()) == 2
    # 两轮各睡 0.02s
    assert elapsed >= 0.03
