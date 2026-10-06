from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.fakes import build_task_registry
from agenteval.models import Status
from agenteval.sandbox import (
    CommandResult,
    DockerSandbox,
    SandboxError,
    SandboxSpec,
    load_sandbox_spec,
)
from agenteval.tasks import TaskRunner, load_tasks

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
SANDBOX_JSON = EXAMPLES / "sandbox.json"
COMPOSE = EXAMPLES / "sandbox" / "compose.yaml"
TASKS = EXAMPLES / "tasks.json"


def _docker_available() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


requires_docker = pytest.mark.skipif(not _docker_available(), reason="docker is not available")
DOCKER_MARK = pytest.mark.docker


@dataclass
class FakeRunner:
    responses: list[CommandResult] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def run(self, args: list[str], timeout_s: float) -> CommandResult:
        self.calls.append(list(args))
        if not self.responses:
            return CommandResult(0, "", "", list(args))
        return self.responses.pop(0)


def spec(**overrides: object) -> SandboxSpec:
    payload: dict[str, object] = {
        "id": "demo",
        "project": "agenteval-demo",
        "compose_file": COMPOSE,
        "services": ["sleeper"],
        "health_services": ["sleeper"],
        "volumes": ["sleeper-data"],
    }
    payload.update(overrides)
    return SandboxSpec.model_validate(payload)


def ps_row(service: str, state: str = "running", health: str = "healthy") -> str:
    return json.dumps({"Service": service, "State": state, "Health": health})


# --------------------------------------------------------------------------- 定义


def test_load_sandbox_definition_resolves_relative_paths() -> None:
    loaded = load_sandbox_spec(SANDBOX_JSON)
    assert loaded.compose_file.is_absolute()
    assert loaded.compose_file == COMPOSE.resolve()
    assert loaded.project == "agenteval-sandbox-demo"


def test_snapshot_strategy_requires_a_name() -> None:
    with pytest.raises(ValueError, match="snapshot"):
        SandboxSpec.model_validate(
            {
                "id": "d",
                "project": "p",
                "compose_file": COMPOSE,
                "reset": "snapshot",
            }
        )


def test_definition_rejects_missing_project() -> None:
    with pytest.raises(ValueError):
        SandboxSpec.model_validate({"id": "d", "compose_file": COMPOSE})


def test_load_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_sandbox_spec(tmp_path / "nope.json")


# --------------------------------------------------------------------------- 命令构造


def test_up_uses_the_defined_project_and_services() -> None:
    runner = FakeRunner()
    DockerSandbox(spec(), runner=runner).up()
    assert runner.calls == [
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE),
            "-p",
            "agenteval-demo",
            "up",
            "-d",
            "sleeper",
        ]
    ]


def test_down_can_remove_volumes() -> None:
    runner = FakeRunner()
    DockerSandbox(spec(), runner=runner).down(remove_volumes=True)
    assert runner.calls[0][-2:] == ["down", "-v"]


def test_nonzero_exit_becomes_a_readable_error() -> None:
    runner = FakeRunner(responses=[CommandResult(1, "", "no such service", ["docker"])])
    with pytest.raises(SandboxError, match="no such service"):
        DockerSandbox(spec(), runner=runner).up()


def test_missing_docker_binary_is_reported() -> None:
    from agenteval.sandbox import SubprocessRunner

    with pytest.raises(SandboxError, match="command not found"):
        SubprocessRunner().run(["definitely-not-a-real-binary"], 5)


# --------------------------------------------------------------------------- 健康检查


def test_health_passes_when_every_service_is_running() -> None:
    runner = FakeRunner(responses=[CommandResult(0, ps_row("sleeper"), "", [])])
    ok, problems = DockerSandbox(spec(), runner=runner).health()
    assert ok is True
    assert problems == []


def test_health_reports_a_service_that_is_not_healthy() -> None:
    runner = FakeRunner(
        responses=[CommandResult(0, ps_row("sleeper", health="starting"), "", [])]
    )
    ok, problems = DockerSandbox(spec(), runner=runner).health()
    assert ok is False
    assert problems[0]["service"] == "sleeper"
    assert problems[0]["health"] == "starting"


def test_health_reports_a_missing_service() -> None:
    runner = FakeRunner(responses=[CommandResult(0, "", "", [])])
    ok, problems = DockerSandbox(spec(), runner=runner).health()
    assert ok is False
    assert problems[0]["state"] == "missing"


def test_wait_until_healthy_times_out() -> None:
    runner = FakeRunner(
        responses=[CommandResult(0, ps_row("sleeper", health="starting"), "", [])] * 5
    )
    sandbox = DockerSandbox(
        spec(health_timeout_s=0.01, poll_interval_s=0.001), runner=runner
    )
    with pytest.raises(SandboxError, match="not healthy"):
        sandbox.wait_until_healthy()


def test_health_tolerates_a_json_array_payload() -> None:
    runner = FakeRunner(
        responses=[CommandResult(0, json.dumps([json.loads(ps_row("sleeper"))]), "", [])]
    )
    ok, _ = DockerSandbox(spec(), runner=runner).health()
    assert ok is True


# --------------------------------------------------------------------------- 策略与版本


def test_recreate_reset_removes_volumes_then_starts() -> None:
    runner = FakeRunner(
        responses=[
            CommandResult(0, "", "", []),  # down -v
            CommandResult(0, "", "", []),  # up
            CommandResult(0, ps_row("sleeper"), "", []),  # health
        ]
    )
    DockerSandbox(spec(reset="recreate"), runner=runner).reset()
    assert "-v" in runner.calls[0]
    assert runner.calls[1][-3:] == ["up", "-d", "sleeper"]


def test_none_reset_only_checks_health() -> None:
    runner = FakeRunner(responses=[CommandResult(0, ps_row("sleeper"), "", [])])
    DockerSandbox(spec(reset="none"), runner=runner).reset()
    assert len(runner.calls) == 1
    assert "ps" in runner.calls[0]


def test_snapshot_reset_without_a_snapshot_fails_loudly(tmp_path: Path) -> None:
    sandbox = DockerSandbox(
        spec(reset="snapshot", snapshot="baseline", snapshot_dir=tmp_path / "snaps"),
        runner=FakeRunner(),
    )
    with pytest.raises(SandboxError, match="does not exist"):
        sandbox.reset()


def test_volumes_are_prefixed_with_the_project() -> None:
    sandbox = DockerSandbox(spec(), runner=FakeRunner())
    assert sandbox.volumes() == ["agenteval-demo_sleeper-data"]


def test_version_records_the_compose_hash() -> None:
    runner = FakeRunner(responses=[CommandResult(0, ps_row("sleeper"), "", [])])
    version = DockerSandbox(spec(), runner=runner).version()
    assert version["project"] == "agenteval-demo"
    assert len(version["compose_sha256"]) == 64


# --------------------------------------------------------------------------- 与评测集成


class FakeSandbox:
    def __init__(self, fail_before: str | None = None) -> None:
        self.ready = 0
        self.cases: list[str] = []
        self.closed = 0
        self.fail_before = fail_before

    def ensure_ready(self) -> None:
        self.ready += 1

    def before_case(self, case_id: str) -> None:
        self.cases.append(case_id)
        if self.fail_before is not None and case_id == self.fail_before:
            raise SandboxError("reset boom")

    def close(self) -> None:
        self.closed += 1

    def describe(self) -> dict[str, str]:
        return {"sandbox": "fake", "project": "fake"}


def test_task_runner_without_a_sandbox_is_unchanged() -> None:
    agent = _demo_agent()
    run = TaskRunner(agent=agent, environment=build_task_registry).run(
        load_tasks(TASKS), run_id="no-sandbox"
    )
    assert run.summary.passed == 2
    assert "sandbox" not in run.metadata


def test_task_runner_resets_before_every_case() -> None:
    sandbox = FakeSandbox()
    run = TaskRunner(
        agent=_demo_agent(), environment=build_task_registry, sandbox=sandbox  # type: ignore[arg-type]
    ).run(load_tasks(TASKS), run_id="with-sandbox")
    assert run.summary.passed == 2
    assert sandbox.ready == 1
    assert sandbox.cases == ["task-charge-and-settle", "task-refund-reduces-balance"]
    assert sandbox.closed == 1
    assert run.metadata["sandbox"]["project"] == "fake"


def test_a_failed_reset_only_errors_that_case() -> None:
    sandbox = FakeSandbox(fail_before="task-charge-and-settle")
    run = TaskRunner(
        agent=_demo_agent(), environment=build_task_registry, sandbox=sandbox  # type: ignore[arg-type]
    ).run(load_tasks(TASKS), run_id="reset-failure")
    assert run.summary.errored == 1
    assert run.summary.passed == 1
    assert "sandbox reset failed" in (run.verdicts[0].error or "")


def test_unready_sandbox_fails_the_whole_run() -> None:
    class UnreadySandbox(FakeSandbox):
        def ensure_ready(self) -> None:
            raise SandboxError("never came up")

    with pytest.raises(SandboxError, match="not ready"):
        TaskRunner(
            agent=_demo_agent(),  # type: ignore[arg-type]
            environment=build_task_registry,
            sandbox=UnreadySandbox(),  # type: ignore[arg-type]
        ).run(load_tasks(TASKS), run_id="unready")


def _demo_agent():
    def run(registry, task) -> None:
        ledger = registry.get("ledger_tool")
        if task.id == "task-charge-and-settle":
            ledger.invoke(op="charge", amount=30)
            ledger.invoke(op="settle")
        elif task.id == "task-refund-reduces-balance":
            ledger.invoke(op="charge", amount=100)
            ledger.invoke(op="refund", amount=30)
        else:
            raise RuntimeError(f"no plan for {task.id}")

    return run


# --------------------------------------------------------------------------- 真实 Docker


def _write_cli_sandbox(tmp_path: Path, project: str) -> Path:
    payload = {
        "sandbox": {
            "id": "cli-sandbox",
            "project": project,
            "compose_file": str(COMPOSE),
            "services": ["sleeper"],
            "health_services": ["sleeper"],
            "volumes": ["sleeper-data"],
            "reset": "recreate",
            "teardown": "down",
            "poll_interval_s": 1,
        }
    }
    path = tmp_path / "sandbox.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@DOCKER_MARK
@requires_docker
def test_real_sandbox_lifecycle(tmp_path: Path) -> None:
    sandbox_spec = load_sandbox_spec(SANDBOX_JSON).model_copy(
        update={
            "project": f"agenteval-sandbox-test-{os.getpid()}",
            "snapshot_dir": tmp_path / "snaps",
        }
    )
    sandbox = DockerSandbox(sandbox_spec)
    try:
        sandbox.up()
        sandbox.wait_until_healthy()
        ok, problems = sandbox.health()
        assert ok, problems
        manifest = sandbox.snapshot("baseline")
        assert manifest["volumes"] == [f"{sandbox_spec.project}_sleeper-data"]
        sandbox.reset()
        ok, problems = sandbox.health()
        assert ok, problems
    finally:
        sandbox.down(remove_volumes=True)


@DOCKER_MARK
@requires_docker
def test_cli_sandbox_health_and_reset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    definition = _write_cli_sandbox(tmp_path, f"agenteval-sandbox-cli-{os.getpid()}")
    try:
        up_code = main(["sandbox", "up", "--sandbox", str(definition), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert up_code == 0
        assert payload["project"].startswith("agenteval-sandbox-cli")

        health_code = main(["sandbox", "health", "--sandbox", str(definition), "--json"])
        health = json.loads(capsys.readouterr().out)
        assert health_code == 0
        assert health["healthy"] is True

        reset_code = main(["sandbox", "reset", "--sandbox", str(definition)])
        assert reset_code == 0
        capsys.readouterr()
    finally:
        main(["sandbox", "down", "--sandbox", str(definition), "--volumes"])
        capsys.readouterr()
