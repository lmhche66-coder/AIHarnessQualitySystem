"""端到端任务的执行。

任务只描述「要达成什么」，agent 通过统一协议接入；每个任务开始时用工厂重建一份
干净环境，因此任务之间不会有状态残留。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from agenteval.models import (
    CheckOutcome,
    FinalStateCheck,
    Run,
    Status,
    TaskCase,
    Trace,
    Verdict,
)
from agenteval.metrics import summarize_metrics
from agenteval.process import (
    TraceSession,
    extract_tool_calls,
    measure_calls,
    process_handler,
    wrap_registry_for_trace,
)
from agenteval.runner import new_run_id
from agenteval.sandbox import SandboxError, SandboxSession
from agenteval.store import RunStore
from agenteval.tools import ToolRegistry

AgentRunner = Callable[[ToolRegistry, TaskCase], None]
EnvironmentFactory = Callable[[], ToolRegistry]

_TASK_LIST_ADAPTER: TypeAdapter[list[TaskCase]] = TypeAdapter(list[TaskCase])

ATTEMPT_SEPARATOR = "#"


def attempt_case_id(task_id: str, attempt: int, attempts: int) -> str:
    """单次尝试保持既有标识，多次尝试才追加序号。

    既有基线与回归资产都以判定标识为键，默认一次时标识不能变。
    """

    if attempts <= 1:
        return task_id
    return f"{task_id}{ATTEMPT_SEPARATOR}{attempt}"


def load_tasks(path: Path) -> list[TaskCase]:
    """从 JSON 或 YAML 文件载入任务列表。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "tasks" not in payload:
            found = ", ".join(sorted(payload)) or "none"
            raise ValueError(
                f"tasks file must contain a list or a 'tasks' key: {path} (found keys: {found})"
            )
        payload = payload["tasks"]
    if not isinstance(payload, list):
        raise ValueError(f"tasks file must contain a list or a 'tasks' key: {path}")
    return _TASK_LIST_ADAPTER.validate_python(payload)


def check_final_state(spec: FinalStateCheck, registry: ToolRegistry) -> CheckOutcome:
    """读取环境中的最终状态并与期望值比较。

    未注册工具、工具没有状态读取能力、字段不存在，都是任务配置问题：判失败并
    说明原因，而不是抛异常。
    """

    if not spec.tool or not spec.field:
        return CheckOutcome(
            name="final_state.configured",
            passed=False,
            expected="non-empty tool and field",
            actual={"tool": spec.tool, "field": spec.field},
            message="final state check is not fully configured",
        )
    if spec.tool not in registry:
        return CheckOutcome(
            name="final_state",
            passed=False,
            expected=spec.expected,
            actual=None,
            message=f"tool not registered: {spec.tool}",
        )
    tool = registry.get(spec.tool)
    reader = getattr(tool, "state", None)
    if not callable(reader):
        return CheckOutcome(
            name="final_state",
            passed=False,
            expected=spec.expected,
            actual=None,
            message=f"tool does not expose state(): {spec.tool}",
        )
    try:
        state = reader()
    except Exception as exc:  # noqa: BLE001 - 读取状态失败也要转成判据结果
        return CheckOutcome(
            name="final_state",
            passed=False,
            expected=spec.expected,
            actual=None,
            message=f"reading state failed: {type(exc).__name__}: {exc}",
        )
    if not isinstance(state, dict) or spec.field not in state:
        return CheckOutcome(
            name="final_state",
            passed=False,
            expected=spec.expected,
            actual=state,
            message=f"state has no field: {spec.field}",
        )
    actual = state[spec.field]
    passed = actual == spec.expected
    return CheckOutcome(
        name="final_state",
        passed=passed,
        expected=spec.expected,
        actual=actual,
        message=None if passed else f"final state mismatch on {spec.tool}.{spec.field}",
    )


def evaluate_task_checks(
    task: TaskCase,
    registry: ToolRegistry,
    trace: Trace,
) -> list[CheckOutcome]:
    """合并过程断言与终态断言。过程断言复用轨迹层的处理器。"""

    calls = extract_tool_calls(trace)
    outcomes: list[CheckOutcome] = []
    for spec in task.checks:
        handler = process_handler(spec.kind)
        if handler is not None:
            outcomes.extend(handler(spec, calls, trace))
        elif isinstance(spec, FinalStateCheck):
            outcomes.append(check_final_state(spec, registry))
        else:
            outcomes.append(
                CheckOutcome(
                    name=f"{spec.kind}.unsupported",
                    passed=False,
                    expected="a supported task check",
                    actual=spec.kind,
                    message="unsupported task check",
                )
            )
    return outcomes


def _task_statuses(task: TaskCase, by_id: Mapping[str, Verdict]) -> list[Status]:
    return [
        by_id[key].status
        for attempt in range(1, task.attempts + 1)
        if (key := attempt_case_id(task.id, attempt, task.attempts)) in by_id
    ]


def summarize_tasks(tasks: Sequence[TaskCase], verdicts: Sequence[Verdict]) -> dict[str, Any]:
    """统计通过率，并分别列出未解决与出错的任务。

    任务声明多次尝试时，只要任一次通过即视为已解决。
    """

    by_id = {verdict.case_id: verdict for verdict in verdicts}
    resolved: list[str] = []
    unresolved: list[str] = []
    errored: list[str] = []
    for task in tasks:
        statuses = _task_statuses(task, by_id)
        if not statuses or all(status is Status.ERROR for status in statuses):
            errored.append(task.id)
        elif any(status is Status.PASS for status in statuses):
            resolved.append(task.id)
        else:
            unresolved.append(task.id)
    total = len(tasks)
    return {
        "total": total,
        "resolved": len(resolved),
        "resolved_rate": (len(resolved) / total) if total else 0.0,
        "resolved_tasks": resolved,
        "unresolved_tasks": unresolved,
        "errored_tasks": errored,
    }


def summarize_attempts(
    tasks: Sequence[TaskCase], verdicts: Sequence[Verdict]
) -> dict[str, Any]:
    """按尝试聚合，区分「做不成」与「不稳定」。"""

    by_id = {verdict.case_id: verdict for verdict in verdicts}
    details: list[dict[str, Any]] = []
    resolved = 0
    first_pass = 0
    for task in tasks:
        statuses = _task_statuses(task, by_id)
        task_resolved = any(status is Status.PASS for status in statuses)
        resolved += task_resolved
        first_pass += bool(statuses) and statuses[0] is Status.PASS
        details.append(
            {
                "task_id": task.id,
                "attempts": task.attempts,
                "passed": sum(1 for status in statuses if status is Status.PASS),
                "resolved": task_resolved,
                "pass_at_1": bool(statuses) and statuses[0] is Status.PASS,
            }
        )
    total = len(tasks)
    return {
        "tasks": total,
        "attempts": sum(task.attempts for task in tasks),
        "resolved": resolved,
        "pass_at_k": (resolved / total) if total else 0.0,
        "pass_at_1": (first_pass / total) if total else 0.0,
        "details": details,
    }


@dataclass
class TaskRunner:
    """执行端到端任务。"""

    agent: AgentRunner
    environment: EnvironmentFactory
    store: RunStore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sandbox: SandboxSession | None = None

    def run_task(self, task: TaskCase, attempt: int = 1) -> tuple[Verdict, Trace]:
        """在干净环境里跑一次任务，返回判定与轨迹。

        每次尝试都重建环境与 agent，避免上一次尝试的残留影响本次。
        """

        case_id = attempt_case_id(task.id, attempt, task.attempts)
        trace = Trace(case_id=case_id)
        start = time.perf_counter()
        if self.sandbox is not None:
            try:
                self.sandbox.before_case(case_id)
            except Exception as exc:  # noqa: BLE001 - 重置失败只影响该用例
                return self._error(
                    case_id,
                    f"sandbox reset failed: {type(exc).__name__}: {exc}",
                    trace,
                    start,
                )
        try:
            registry = self.environment()
        except Exception as exc:  # noqa: BLE001 - 环境失败不终止整轮运行
            return self._error(
                case_id,
                f"environment setup failed: {type(exc).__name__}: {exc}",
                trace,
                start,
            )

        session = TraceSession()
        session.begin_case(trace)
        try:
            self.agent(wrap_registry_for_trace(registry, session), task)
        except Exception as exc:  # noqa: BLE001 - agent 异常不终止整轮运行
            session.end_case()
            return self._error(
                case_id, f"agent raised: {type(exc).__name__}: {exc}", trace, start
            )
        session.end_case()

        if not task.checks:
            checks = [
                CheckOutcome(
                    name="task.checks_configured",
                    passed=False,
                    expected="at least one success check",
                    actual=0,
                    message="task declares no success checks",
                )
            ]
        else:
            checks = evaluate_task_checks(task, registry, trace)
        status = Status.PASS if all(check.passed for check in checks) else Status.FAIL
        verdict = Verdict(case_id=case_id, status=status, checks=checks)
        verdict.duration_ms = round((time.perf_counter() - start) * 1000, 3)
        verdict.metrics = measure_calls(trace)
        return verdict, trace

    def run(
        self,
        tasks: Iterable[TaskCase],
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        task_list = list(tasks)
        merged_metadata = dict(self.metadata)
        merged_metadata.update(metadata or {})
        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=merged_metadata,
        )
        if self.sandbox is not None:
            try:
                self.sandbox.ensure_ready()
            except Exception as exc:  # noqa: BLE001 - 环境不可用则整轮失败
                raise SandboxError(
                    f"sandbox is not ready: {type(exc).__name__}: {exc}"
                ) from exc
            run.metadata["sandbox"] = self.sandbox.describe()
        for task in task_list:
            for attempt in range(1, task.attempts + 1):
                verdict, trace = self.run_task(task, attempt)
                run.verdicts.append(verdict)
                run.traces.append(trace)
        if self.sandbox is not None:
            self.sandbox.close()
        run.metadata["task_report"] = summarize_tasks(task_list, run.verdicts)
        run.metadata["attempt_report"] = summarize_attempts(task_list, run.verdicts)
        run.metadata["metrics"] = summarize_metrics(run.verdicts).model_dump(mode="json")
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run

    @staticmethod
    def _error(
        case_id: str, message: str, trace: Trace, start: float
    ) -> tuple[Verdict, Trace]:
        verdict = Verdict(case_id=case_id, status=Status.ERROR, error=message)
        verdict.duration_ms = round((time.perf_counter() - start) * 1000, 3)
        return verdict, trace
