"""多轮对话。

平台掌握循环，agent 只负责一轮，用户由可替换的模拟器扮演。这样「先问再做」
「几轮收敛」「有没有收尾」这类跨轮断言才有地方落——一次性任务协议下平台在
agent 执行期间拿不到控制权。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agenteval.models import (
    CaseMetrics,
    CheckOutcome,
    DialogueCase,
    Run,
    Status,
    Trace,
    Verdict,
)
from agenteval.process import (
    ToolCall,
    TraceSession,
    extract_tool_calls,
    process_handler,
    wrap_registry_for_trace,
)
from agenteval.runner import new_run_id
from agenteval.store import RunStore
from agenteval.tools import ToolRegistry


class Role(str, Enum):
    USER = "user"
    AGENT = "agent"


class Turn(BaseModel):
    """一个对话回合。agent 回合可以携带该轮发生的工具调用。"""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str = ""
    calls: list[ToolCall] = Field(default_factory=list)


class Conversation(BaseModel):
    """一条对话用例的完整回合序列。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    turns: list[Turn] = Field(default_factory=list)

    def agent_turns(self) -> list[Turn]:
        return [turn for turn in self.turns if turn.role is Role.AGENT]


class TurnBasedAgent(Protocol):
    """逐轮协议：接收工具环境与当前对话，返回本轮回复。

    以可调用对象为主（``agent(registry, conversation)``）；带 ``respond`` 方法的
    对象同样被接受，方便已按方法风格封装的接入方。
    """

    def __call__(self, registry: ToolRegistry, conversation: Conversation) -> str:
        ...


class UserSimulator(Protocol):
    """用户模拟器：返回下一句用户消息；返回 ``None`` 表示对话结束。"""

    def respond(self, case: DialogueCase, conversation: Conversation) -> str | None:
        ...


class ScriptedSimulator:
    """确定性脚本模拟器：按剧本逐句回复，用尽即结束。"""

    def __init__(self, script: Sequence[str]) -> None:
        self.script = list(script)
        self._index = 0

    def respond(self, case: DialogueCase, conversation: Conversation) -> str | None:
        if self._index >= len(self.script):
            return None
        message = self.script[self._index]
        self._index += 1
        return message


_DIALOGUE_ADAPTER: TypeAdapter[list[DialogueCase]] = TypeAdapter(list[DialogueCase])


def load_dialogue_cases(path: Path) -> list[DialogueCase]:
    """载入对话用例集，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "cases" not in payload:
            raise ValueError(f"dialogue file must contain a list or a 'cases' key: {path}")
        payload = payload["cases"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"dialogue file must contain at least one case: {path}")
    return _DIALOGUE_ADAPTER.validate_python(payload)


def _check_clarification(spec: Any, agent_turns: Sequence[Turn]) -> CheckOutcome:
    if not spec.marker or not spec.before_tool:
        return CheckOutcome(
            name="dialogue.required_clarification.configured",
            passed=False,
            expected="non-empty marker and before_tool",
            actual={"marker": spec.marker, "before_tool": spec.before_tool},
            message="required_clarification check is not fully configured",
        )
    for index, turn in enumerate(agent_turns):
        if not any(call.target == spec.before_tool for call in turn.calls):
            continue
        asked_before = any(spec.marker in earlier.content for earlier in agent_turns[:index])
        return CheckOutcome(
            name="dialogue.required_clarification",
            passed=asked_before,
            expected=f"{spec.before_tool} is called only after asking about {spec.marker!r}",
            actual={"turn_index": index, "asked_before": asked_before},
            message=(
                None
                if asked_before
                else f"{spec.before_tool} was called in turn {index} without asking "
                f"about {spec.marker!r} first"
            ),
        )
    return CheckOutcome(
        name="dialogue.required_clarification",
        passed=False,
        expected=f"{spec.before_tool} is called at some point",
        actual=[call.target for turn in agent_turns for call in turn.calls],
        message=f"{spec.before_tool} was never called",
    )


def _check_termination(spec: Any, agent_turns: Sequence[Turn]) -> CheckOutcome:
    if not spec.marker:
        return CheckOutcome(
            name="dialogue.termination.configured",
            passed=False,
            expected="a non-empty marker",
            actual=spec.marker,
            message="termination check declares no marker",
        )
    last = agent_turns[-1].content if agent_turns else ""
    passed = spec.marker in last
    return CheckOutcome(
        name="dialogue.termination",
        passed=passed,
        expected=f"the last agent turn contains {spec.marker!r}",
        actual=last[:160],
        message=None if passed else "the conversation did not end with the expected marker",
    )


def evaluate_dialogue(
    case: DialogueCase,
    conversation: Conversation,
    trace: Trace,
    ended_naturally: bool,
) -> list[CheckOutcome]:
    """合并回合预算、对话级断言与轨迹类断言。"""

    agent_turns = conversation.agent_turns()
    outcomes = [
        CheckOutcome(
            name="dialogue.max_turns",
            passed=ended_naturally,
            expected=f"the simulator finishes within {case.max_turns} agent turns",
            actual=len(agent_turns),
            message=(
                None
                if ended_naturally
                else "the turn budget was exhausted before the simulator finished"
            ),
        )
    ]
    calls = extract_tool_calls(trace)
    for spec in case.checks:
        handler = process_handler(spec.kind)
        if handler is not None:
            outcomes.extend(handler(spec, calls, trace))
        elif spec.kind == "required_clarification":
            outcomes.append(_check_clarification(spec, agent_turns))
        elif spec.kind == "termination":
            outcomes.append(_check_termination(spec, agent_turns))
        else:
            outcomes.append(
                CheckOutcome(
                    name=f"{spec.kind}.unsupported",
                    passed=False,
                    expected="a supported dialogue check",
                    actual=spec.kind,
                    message="unsupported dialogue check",
                )
            )
    return outcomes


@dataclass
class DialogueRunner:
    """驱动对话循环并产出与其它各层一致的运行记录。"""

    agent: TurnBasedAgent
    environment: Callable[[], ToolRegistry]
    simulator: UserSimulator | None = None
    store: RunStore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def _respond(self, registry: ToolRegistry, conversation: Conversation) -> str:
        respond = getattr(self.agent, "respond", None)
        if callable(respond):
            return respond(registry, conversation)  # type: ignore[no-any-return]
        return self.agent(registry, conversation)

    def run_case(self, case: DialogueCase) -> tuple[Verdict, Trace, Conversation]:
        conversation = Conversation(id=case.id)
        trace = Trace(case_id=case.id)
        simulator = self.simulator or ScriptedSimulator(case.script)
        session = TraceSession()
        session.begin_case(trace)
        started = time.perf_counter()
        error: str | None = None
        ended_naturally = False
        try:
            registry = wrap_registry_for_trace(self.environment(), session)
            for _ in range(case.max_turns):
                message = simulator.respond(case, conversation)
                if message is None:
                    ended_naturally = True
                    break
                conversation.turns.append(Turn(role=Role.USER, content=message))
                before = len(trace.events)
                reply = self._respond(registry, conversation)
                turn_calls = extract_tool_calls(
                    Trace(case_id=case.id, events=list(trace.events[before:]))
                )
                conversation.turns.append(
                    Turn(role=Role.AGENT, content=reply or "", calls=turn_calls)
                )
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不终止整轮运行
            error = f"{type(exc).__name__}: {exc}"
        finally:
            session.end_case()

        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        if error is not None:
            verdict = Verdict(case_id=case.id, status=Status.ERROR, error=error)
            verdict.duration_ms = duration_ms
            return verdict, trace, conversation

        checks = evaluate_dialogue(case, conversation, trace, ended_naturally)
        status = Status.PASS if all(check.passed for check in checks) else Status.FAIL
        verdict = Verdict(case_id=case.id, status=status, checks=checks)
        verdict.duration_ms = duration_ms
        verdict.metrics = CaseMetrics(
            calls=len(extract_tool_calls(trace)),
            duration_ms=duration_ms,
        )
        return verdict, trace, conversation

    def run(
        self,
        cases: Sequence[DialogueCase],
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        case_list = list(cases)
        merged = dict(self.metadata)
        merged.update(metadata or {})
        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=merged,
        )
        dialogues: dict[str, Any] = {}
        for case in case_list:
            verdict, trace, conversation = self.run_case(case)
            run.verdicts.append(verdict)
            run.traces.append(trace)
            dialogues[case.id] = conversation.model_dump(mode="json")
        run.metadata["dialogues"] = dialogues
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run
