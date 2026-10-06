from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agenteval.bridge import (
    PROTOCOL_VERSION,
    AgentSpec,
    BridgeAgent,
    BridgeError,
    BridgeRequest,
    SubprocessTransport,
    check_availability,
    load_agent_registry,
)
from agenteval.cli import main
from agenteval.dialogue import DialogueRunner, load_dialogue_cases
from agenteval.fakes import build_demo_registry, build_task_registry
from agenteval.models import Status
from agenteval.models import TaskCase
from agenteval.redteam import RedTeamRunner, load_probes
from agenteval.selection import SelectionRunner, load_selection_cases
from agenteval.tasks import TaskRunner, load_tasks

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
AGENTS = EXAMPLES / "agents.json"
LEDGER = EXAMPLES / "agents" / "ledger_agent.py"
ROGUE = EXAMPLES / "agents" / "rogue_agent.py"
HTTP_SERVER = EXAMPLES / "agents" / "http_support_agent.py"
TASKS = EXAMPLES / "tasks.json"
DIALOGUE_CASES = EXAMPLES / "dialogue_cases.json"
SELECTION_CASES = EXAMPLES / "selection_cases.json"
PROBES = EXAMPLES / "redteam_probes.json"
PYTHON = sys.executable


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("the example HTTP agent did not start in time")


@pytest.fixture(scope="module")
def http_endpoint() -> str:
    port = _free_port()
    env = {**os.environ, "AGENTEVAL_BRIDGE_PORT": str(port)}
    process = subprocess.Popen(
        [PYTHON, str(HTTP_SERVER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    _wait_for_port(port)
    endpoint = f"http://127.0.0.1:{port}/eval"
    yield endpoint
    process.terminate()
    process.wait(timeout=10)


def spec(**overrides: object) -> AgentSpec:
    payload: dict[str, object] = {
        "id": "ledger-agent",
        "version": "1.0.0",
        "transport": "subprocess",
        "command": PYTHON,
        "args": [str(LEDGER)],
        "capabilities": ["task"],
    }
    payload.update(overrides)
    return AgentSpec.model_validate(payload)


def request(capability: str = "task") -> BridgeRequest:
    return BridgeRequest(capability=capability, case={"id": "c1"})  # type: ignore[arg-type]


def _script(payload: dict[str, object]) -> list[str]:
    return ["-c", f"import json; print(json.dumps({payload!r}))"]


# --------------------------------------------------------------------------- 注册表


def test_load_agent_registry() -> None:
    registry = load_agent_registry(AGENTS)
    assert registry.ids() == ["ledger-agent", "rogue-agent", "support-agent"]
    assert registry.get("ledger-agent").version == "1.2.0"
    assert registry.get("support-agent").transport == "http"


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps(
            {
                "agents": [
                    {"id": "a", "version": "1", "transport": "subprocess", "command": "python"},
                    {"id": "a", "version": "2", "transport": "subprocess", "command": "python"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_agent_registry(path)


def test_registry_rejects_entry_without_transport_details(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    path.write_text(
        json.dumps({"agents": [{"id": "a", "version": "1", "transport": "http"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="url"):
        load_agent_registry(path)


def test_unknown_app_id_lists_available_ids() -> None:
    registry = load_agent_registry(AGENTS)
    with pytest.raises(BridgeError, match="registered agents"):
        registry.get("nope")


def test_availability_check_for_subprocess_and_http() -> None:
    ok, _ = check_availability(spec())
    assert ok is True
    missing = spec(command="definitely-not-a-real-bridge-binary")
    ok, message = check_availability(missing)
    assert ok is False
    assert "not found" in message
    unreachable = spec(
        transport="http", url="http://127.0.0.1:1/eval", command=None, args=[]
    )
    ok, message = check_availability(unreachable)
    assert ok is False
    assert "cannot reach" in message


# --------------------------------------------------------------------------- 传输


def test_subprocess_transport_round_trip() -> None:
    transport = SubprocessTransport(
        command=PYTHON,
        args=_script({"protocol": PROTOCOL_VERSION, "output": "hi", "tool_calls": []}),
    )
    response = transport.call(request(), 15)
    assert response.output == "hi"


def test_subprocess_transport_reports_nonzero_exit() -> None:
    transport = SubprocessTransport(command=PYTHON, args=["-c", "import sys; sys.exit(3)"])
    with pytest.raises(BridgeError, match="exited with code 3"):
        transport.call(request(), 15)


def test_subprocess_transport_times_out() -> None:
    transport = SubprocessTransport(
        command=PYTHON, args=["-c", "import time; time.sleep(30)"]
    )
    with pytest.raises(BridgeError, match="did not respond"):
        transport.call(request(), 0.5)


def test_unsupported_protocol_version_is_rejected() -> None:
    transport = SubprocessTransport(
        command=PYTHON, args=_script({"protocol": "agenteval.bridge/99", "output": "hi"})
    )
    with pytest.raises(BridgeError, match="protocol"):
        transport.call(request(), 15)


def test_agent_reported_error_is_rejected() -> None:
    transport = SubprocessTransport(
        command=PYTHON, args=_script({"protocol": PROTOCOL_VERSION, "error": "boom"})
    )
    with pytest.raises(BridgeError, match="reported an error"):
        transport.call(request(), 15)


# --------------------------------------------------------------------------- 能力与工具模式


def test_undeclared_capability_is_rejected() -> None:
    bridge = BridgeAgent(spec(capabilities=["task"]))
    with pytest.raises(BridgeError, match="does not declare capability"):
        bridge.invoke("selection", {"id": "c1"})


def test_task_accepts_the_agent_tool_mode() -> None:
    bridge = BridgeAgent(spec(tool_mode="agent"))
    assert callable(bridge.for_task())


def test_task_in_agent_mode_records_agent_executed_calls() -> None:
    agent = spec(
        args=_script(
            {
                "protocol": PROTOCOL_VERSION,
                "output": "done",
                "tool_calls": [{"name": "ledger_tool", "arguments": {"op": "settle"}}],
            }
        ),
        tool_mode="agent",
    )
    task = TaskCase.model_validate(
        {
            "id": "agent-mode-task",
            "kind": "task",
            "description": "settle the ledger",
            "checks": [
                {"kind": "tool_sequence", "expected": ["ledger_tool"], "mode": "subsequence"}
            ],
        }
    )
    run = TaskRunner(agent=BridgeAgent(agent).for_task(), environment=build_task_registry).run(
        [task], run_id="agent-mode"
    )
    assert run.summary.passed == 1
    calls = [
        event.payload.get("target")
        for event in run.traces[0].events
        if event.name == "tool_call"
    ]
    assert calls == ["ledger_tool"]


def test_loop_exceeding_max_steps_fails() -> None:
    looping = spec(
        args=_script(
            {
                "protocol": PROTOCOL_VERSION,
                "tool_calls": [{"name": "ledger_tool", "arguments": {"op": "settle"}}],
            }
        ),
        max_steps=3,
    )
    bridge = BridgeAgent(looping)
    task = load_tasks(TASKS)[0]
    with pytest.raises(BridgeError, match="max_steps"):
        bridge.for_task()(build_task_registry(), task)


# --------------------------------------------------------------------------- 四类能力


def test_task_runs_over_a_subprocess_bridge() -> None:
    bridge = BridgeAgent(spec())
    run = TaskRunner(agent=bridge.for_task(), environment=build_task_registry).run(
        load_tasks(TASKS), run_id="bridge-task"
    )
    assert run.summary.passed == 2
    bridge.apply_usage(run)
    metrics = run.metadata["metrics"]
    assert metrics["input_tokens"] == 420
    assert metrics["output_tokens"] == 85


def test_dialogue_runs_over_an_http_bridge(http_endpoint: str) -> None:
    spec_ = spec(
        id="support-agent",
        transport="http",
        url=http_endpoint,
        command=None,
        args=[],
        capabilities=["dialogue"],
    )
    bridge = BridgeAgent(spec_)
    run = DialogueRunner(
        agent=bridge.for_dialogue(), environment=build_demo_registry
    ).run(load_dialogue_cases(DIALOGUE_CASES), run_id="bridge-dialogue")
    assert run.summary.passed == 1


def test_selection_runs_over_an_http_bridge(http_endpoint: str) -> None:
    spec_ = spec(
        id="support-agent",
        transport="http",
        url=http_endpoint,
        command=None,
        args=[],
        capabilities=["selection"],
    )
    bridge = BridgeAgent(spec_)
    cases = load_selection_cases(SELECTION_CASES)
    scored = [
        case.model_copy(update={"actual": bridge.for_selection()(case)}) for case in cases
    ]
    run = SelectionRunner().run(scored, run_id="bridge-selection")
    assert run.summary.passed == 3


def test_selection_bridge_detects_a_wrong_tool() -> None:
    spec_ = spec(
        id="rogue-agent", args=[str(ROGUE)], capabilities=["selection"], tool_mode="agent"
    )
    bridge = BridgeAgent(spec_)
    cases = load_selection_cases(SELECTION_CASES)
    scored = [case.model_copy(update={"actual": bridge.for_selection()(case)}) for case in cases]
    run = SelectionRunner().run(scored, run_id="bridge-rogue")
    assert run.summary.failed >= 1
    assert run.verdicts[0].status is Status.FAIL


def test_redteam_runs_over_an_http_bridge(http_endpoint: str) -> None:
    spec_ = spec(
        id="support-agent",
        transport="http",
        url=http_endpoint,
        command=None,
        args=[],
        capabilities=["redteam"],
    )
    bridge = BridgeAgent(spec_)
    run = RedTeamRunner(target=bridge.for_redteam()).run(
        load_probes(PROBES), run_id="bridge-redteam"
    )
    assert run.summary.total > 0
    assert run.summary.passed == run.summary.total


# --------------------------------------------------------------------------- CLI


def test_cli_runs_a_task_over_the_registry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "--agents",
            str(AGENTS),
            "task",
            "run",
            "--tasks",
            str(TASKS),
            "--registry",
            "agenteval.fakes:build_task_registry",
            "--agent",
            "@ledger-agent",
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "resolved: 2" in output


def test_cli_binds_the_app_version_in_the_run_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    code = main(
        [
            "--home",
            str(home),
            "--agents",
            str(AGENTS),
            "task",
            "run",
            "--tasks",
            str(TASKS),
            "--registry",
            "agenteval.fakes:build_task_registry",
            "--agent",
            "@ledger-agent",
            "--json",
        ]
    )
    assert code == 0
    capsys.readouterr()
    from agenteval.store import RunStore

    store = RunStore(home / "runs")
    run_id = store.latest_run_id()
    assert run_id
    run = store.load(run_id)
    assert run.metadata["app"] == "ledger-agent"
    assert run.metadata["app_version"] == "1.2.0"


def test_cli_fails_a_rogue_selection_agent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "--agents",
            str(AGENTS),
            "selection",
            "run",
            "--cases",
            str(SELECTION_CASES),
            "--agent",
            "@rogue-agent",
        ]
    )
    output = capsys.readouterr().out
    assert code == 1
    assert "selection.function_names" in output


def test_cli_rejects_unknown_app_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "--agents",
            str(AGENTS),
            "task",
            "run",
            "--tasks",
            str(TASKS),
            "--registry",
            "agenteval.fakes:build_task_registry",
            "--agent",
            "@nope",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "registered agents" in captured.err
