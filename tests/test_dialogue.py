from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.dialogue import (
    Conversation,
    DialogueRunner,
    Role,
    ScriptedSimulator,
    Turn,
    load_dialogue_cases,
)
from agenteval.fakes import build_demo_registry
from agenteval.models import DialogueCase, Status
from agenteval.store import RunStore
from agenteval.tools import ToolRegistry

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CASES = EXAMPLES / "dialogue_cases.json"
ASKING_AGENT = f"{EXAMPLES / 'demo_dialogue_agent.py'}:build_agent"
HASTY_AGENT = f"{EXAMPLES / 'demo_dialogue_agent.py'}:build_hasty_agent"
DEMO_REGISTRY = "agenteval.fakes:build_demo_registry"


def case(**overrides: object) -> DialogueCase:
    payload: dict[str, object] = {
        "id": "d1",
        "script": ["你好"],
        "checks": [{"kind": "termination", "marker": "已确认"}],
    }
    payload.update(overrides)
    return DialogueCase.model_validate(payload)


def runner(agent: object, **kwargs: object) -> DialogueRunner:
    return DialogueRunner(
        agent=agent,  # type: ignore[arg-type]
        environment=kwargs.pop("environment", build_demo_registry),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- 模型


def test_dialogue_checks_carry_declared_metric() -> None:
    """对话检查声明的指标应写到判定上，供记分卡按指标归口。"""

    from agenteval.dialogue import DialogueRunner, evaluate_dialogue
    from agenteval.models import DialogueCase, Trace

    case = DialogueCase(
        id="d",
        script=["你好"],
        checks=[{"kind": "termination", "marker": "已确认", "metric": "multi_turn_completion"}],
    )
    conversation = __import__("agenteval.dialogue", fromlist=["Conversation"]).Conversation(id="d")
    outcomes = evaluate_dialogue(case, conversation, Trace(case_id="d"), ended_naturally=True)
    by_name = {outcome.name: outcome for outcome in outcomes}
    assert by_name["dialogue.max_turns"].metric == "multi_turn_completion"
    assert by_name["dialogue.termination"].metric == "multi_turn_completion"


def test_dialogue_case_requires_script_and_checks() -> None:
    with pytest.raises(ValueError, match="scripted user message"):
        DialogueCase.model_validate({"id": "d", "checks": [{"kind": "termination"}]})
    with pytest.raises(ValueError, match="at least one check"):
        DialogueCase.model_validate({"id": "d", "script": ["hi"]})


def test_dialogue_case_rejects_unknown_check() -> None:
    with pytest.raises(ValueError):
        DialogueCase.model_validate(
            {"id": "d", "script": ["hi"], "checks": [{"kind": "nope"}]}
        )


def test_existing_case_kinds_still_parse(tmp_path: Path) -> None:
    from agenteval.runner import load_cases

    cases = load_cases(EXAMPLES / "replayable_cases.json")
    assert cases and not isinstance(cases[0], DialogueCase)


def test_load_example_dialogue_cases() -> None:
    cases = load_dialogue_cases(CASES)
    assert [item.id for item in cases] == ["diagnose-after-asking"]
    assert cases[0].max_turns == 4


# --------------------------------------------------------------------------- 模拟器


def test_scripted_simulator_returns_messages_then_ends() -> None:
    simulator = ScriptedSimulator(["一", "二"])
    conversation = Conversation(id="d1")
    assert simulator.respond(case(), conversation) == "一"
    assert simulator.respond(case(), conversation) == "二"
    assert simulator.respond(case(), conversation) is None


# --------------------------------------------------------------------------- 循环


def test_turn_calls_are_attributed_to_their_turn() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        if len(conversation.agent_turns()) == 0:
            return "先说说时间。"
        registry.get("echo_tool").invoke(message="hi")
        return "已确认。"

    verdict, trace, conversation = runner(
        agent,
        environment=build_demo_registry,
    ).run_case(case(script=["一", "二"], checks=[{"kind": "termination", "marker": "已确认"}]))
    assert verdict.status is Status.PASS
    agent_turns = conversation.agent_turns()
    assert agent_turns[0].calls == []
    assert [call.target for call in agent_turns[1].calls] == ["echo_tool"]
    assert len(conversation.turns) == 4


def test_turn_budget_is_exhausted_when_script_outlasts_it() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        return "还在说"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(script=["一", "二", "三"], max_turns=2, checks=[{"kind": "termination", "marker": "已确认"}])
    )
    assert verdict.status is Status.FAIL
    assert "dialogue.max_turns" in [check.name for check in verdict.failed_checks]


def test_natural_end_within_budget_passes_the_budget_check() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        return "已确认：好的"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(script=["一"], max_turns=3, checks=[{"kind": "termination", "marker": "已确认"}])
    )
    assert verdict.status is Status.PASS


def test_agent_exception_is_an_error() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        raise RuntimeError("agent exploded")

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(case())
    assert verdict.status is Status.ERROR
    assert "agent exploded" in (verdict.error or "")


# --------------------------------------------------------------------------- 断言


def test_required_clarification_passes_when_asked_first() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        if not conversation.agent_turns():
            return "请提供时间范围。"
        registry.get("echo_tool").invoke(message="hi")
        return "已确认。"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(
            script=["一", "二"],
            checks=[
                {"kind": "required_clarification", "marker": "时间", "before_tool": "echo_tool"},
                {"kind": "termination", "marker": "已确认"},
            ],
        )
    )
    assert verdict.status is Status.PASS


def test_required_clarification_fails_when_acting_first() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        registry.get("echo_tool").invoke(message="hi")
        return "已确认。"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(
            checks=[
                {"kind": "required_clarification", "marker": "时间", "before_tool": "echo_tool"},
                {"kind": "termination", "marker": "已确认"},
            ]
        )
    )
    assert verdict.status is Status.FAIL
    failed = [check.name for check in verdict.failed_checks]
    assert failed == ["dialogue.required_clarification"]
    assert "without asking" in (verdict.failed_checks[0].message or "")


def test_required_clarification_fails_when_tool_never_called() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        return "已确认。"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(
            checks=[
                {"kind": "required_clarification", "marker": "时间", "before_tool": "echo_tool"}
            ]
        )
    )
    assert verdict.status is Status.FAIL
    assert "never called" in (verdict.failed_checks[0].message or "")


def test_termination_fails_without_the_marker() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        return "还在处理"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(checks=[{"kind": "termination", "marker": "已确认"}])
    )
    assert verdict.status is Status.FAIL
    assert verdict.failed_checks[0].name == "dialogue.termination"


def test_process_checks_apply_to_dialogue_traces() -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        registry.get("echo_tool").invoke(message="hi")
        return "已确认。"

    verdict, _, _ = runner(agent, environment=build_demo_registry).run_case(
        case(
            checks=[
                {"kind": "no_extra_calls", "allowed": ["payout_tool"]},
                {"kind": "termination", "marker": "已确认"},
            ]
        )
    )
    assert verdict.status is Status.FAIL
    assert "no_extra_calls" in [check.name for check in verdict.failed_checks]


# --------------------------------------------------------------------------- 运行与 CLI


def test_run_record_contains_dialogues(tmp_path: Path) -> None:
    def agent(registry: ToolRegistry, conversation: Conversation) -> str:
        return "已确认。"

    store = RunStore(tmp_path / "runs")
    run = runner(agent, environment=build_demo_registry, store=store).run(
        [case()], run_id="dialogue-run"
    )
    assert run.summary.passed == 1
    assert "d1" in run.metadata["dialogues"]
    restored = store.load("dialogue-run")
    assert restored.metadata["dialogues"]["d1"]["turns"][1]["role"] == "agent"


def test_cli_runs_the_asking_agent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "dialogue",
            "run",
            "--cases",
            str(CASES),
            "--agent",
            ASKING_AGENT,
            "--registry",
            DEMO_REGISTRY,
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "cases: 1  pass: 1" in output


def test_cli_fails_the_hasty_agent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "dialogue",
            "run",
            "--cases",
            str(CASES),
            "--agent",
            HASTY_AGENT,
            "--registry",
            DEMO_REGISTRY,
        ]
    )
    output = capsys.readouterr().out
    assert code == 1
    assert "dialogue.required_clarification" in output


def test_cli_can_emit_dialogues_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--home",
            str(tmp_path / "home"),
            "dialogue",
            "run",
            "--cases",
            str(CASES),
            "--agent",
            ASKING_AGENT,
            "--registry",
            DEMO_REGISTRY,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(payload["diagnose-after-asking"]["turns"]) == 4


def test_run_command_rejects_dialogue_cases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--home", str(tmp_path / "home"), "run", "--cases", str(CASES), "--demo"]
    )
    assert code == 1
    assert "agenteval dialogue run" in capsys.readouterr().out
