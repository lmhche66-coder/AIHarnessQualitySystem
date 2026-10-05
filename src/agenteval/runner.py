"""契约用例的运行器与用例文件载入。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml
from pydantic import TypeAdapter

from agenteval.cassette import CassetteMode, CassetteSession
from agenteval.contracts import check_case
from agenteval.models import (
    AnyCase,
    CheckOutcome,
    ProcessCase,
    Run,
    Status,
    TaskCase,
    Trace,
    Verdict,
)
from agenteval.metrics import summarize_metrics
from agenteval.process import check_process_case, measure_calls, record_tool_call
from agenteval.store import RunStore
from agenteval.tools import ToolErrorKind, ToolRegistry, ToolResult

_ANY_CASE_ADAPTER: TypeAdapter[AnyCase] = TypeAdapter(AnyCase)


def _key_error_text(exc: KeyError) -> str:
    """``str(KeyError)`` 会给消息加上引号，这里取回原始文本。"""

    return str(exc.args[0]) if exc.args else str(exc)


def new_run_id() -> str:
    """生成按时间排序且唯一的运行标识。"""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


# 这些契约的结论取决于真实调用耗时，而 cassette 只记录请求与响应。
NON_REPLAYABLE_CHECKS = frozenset({"timeout"})


def _non_replayable_reason(case: AnyCase, session: CassetteSession | None) -> str | None:
    """耗时相关契约在非录制模式下无法忠实重放，宁可直接拒绝也不给出错误结论。"""

    if session is None or session.mode is CassetteMode.RECORD:
        return None
    check_kind = getattr(getattr(case, "check", None), "kind", None)
    if check_kind in NON_REPLAYABLE_CHECKS:
        return (
            f"check '{check_kind}' depends on real call latency and cannot be replayed from "
            f"cassette '{session.cassette.name}'; run it without --cassette or in record mode"
        )
    return None


def load_cases(path: Path) -> list[AnyCase]:
    """从 JSON 或 YAML 文件载入用例列表。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        payload: Any = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    if isinstance(payload, dict):
        if "cases" not in payload:
            found = ", ".join(sorted(payload)) or "none"
            raise ValueError(
                f"cases file must contain a list or a 'cases' key: {path} (found keys: {found})"
            )
        payload = payload["cases"]
    if not isinstance(payload, list):
        raise ValueError(f"cases file must contain a list or a 'cases' key: {path}")
    return [_ANY_CASE_ADAPTER.validate_python(item) for item in payload]


@dataclass
class ContractRunner:
    """执行契约用例并产出运行记录。"""

    registry: ToolRegistry
    store: RunStore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cassette_session: CassetteSession | None = None
    trace_override: Trace | None = None
    trace_name: str | None = None
    trace_resolver: Callable[[str], Trace] | None = None
    _used_traces: list[str] = field(default_factory=list, repr=False)

    def run_case(self, case: AnyCase) -> tuple[Verdict, Trace]:
        """执行单条用例，返回判定与轨迹。"""

        trace = Trace(case_id=case.id)
        session = self.cassette_session
        if session is not None:
            session.begin_case(trace)
        start = time.perf_counter()
        try:
            override, failure = self._resolve_trace_override(case)
            if failure is not None:
                verdict = Verdict(case_id=case.id, status=Status.ERROR, error=failure)
            else:
                if override is not None:
                    trace = override
                verdict = self._evaluate(case, trace, override)
        finally:
            if session is not None:
                session.end_case()
        verdict.duration_ms = round((time.perf_counter() - start) * 1000, 3)
        verdict.metrics = measure_calls(trace)
        return verdict, trace

    def _resolve_trace_override(self, case: AnyCase) -> tuple[Trace | None, str | None]:
        """解析该用例要引用的已保存轨迹，返回 ``(轨迹, 错误说明)``。

        用例级引用优先于运行级引用，因为一次失败回流的候选各自绑定不同证据。
        """

        if not isinstance(case, ProcessCase):
            return None, None
        if case.trace:
            if self.trace_resolver is None:
                return None, (
                    f"case references stored trace '{case.trace}' but no trace store is configured"
                )
            try:
                resolved = self.trace_resolver(case.trace)
            except FileNotFoundError as exc:
                return None, str(exc)
            self._used_traces.append(case.trace)
            return resolved, None
        return self.trace_override, None

    def _evaluate(self, case: AnyCase, trace: Trace, override: Trace | None) -> Verdict:
        if isinstance(case, TaskCase):
            return Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error="task cases must be run with 'agenteval task run', not 'agenteval run'",
            )
        if isinstance(case, ProcessCase):
            return self._evaluate_process(case, trace, override)
        blocked = _non_replayable_reason(case, self.cassette_session)
        if blocked is not None:
            trace.record(
                "cassette_unreplayable_check",
                payload={"check_kind": case.check.kind},
                error=blocked,
            )
            return Verdict(case_id=case.id, status=Status.ERROR, error=blocked)
        try:
            tool = self.registry.get(case.target)
        except KeyError as exc:
            return Verdict(case_id=case.id, status=Status.ERROR, error=_key_error_text(exc))
        try:
            return check_case(case, tool, trace)
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不终止整轮运行
            return Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _evaluate_process(self, case: ProcessCase, trace: Trace, override: Trace | None) -> Verdict:
        if override is not None:
            if case.steps:
                return Verdict(
                    case_id=case.id,
                    status=Status.ERROR,
                    error=(
                        "process case declares steps but a stored trace was supplied; "
                        "use exactly one trace source"
                    ),
                )
            try:
                return check_process_case(case, trace)
            except Exception as exc:  # noqa: BLE001 - 单条用例失败不终止整轮运行
                return Verdict(
                    case_id=case.id,
                    status=Status.ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                )
        if not case.steps:
            return Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error=(
                    "process case declares no steps and no stored trace was supplied; "
                    "add steps or run with --trace"
                ),
            )
        try:
            self._run_process_steps(case, trace)
            return check_process_case(case, trace)
        except KeyError as exc:
            return Verdict(case_id=case.id, status=Status.ERROR, error=_key_error_text(exc))
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不终止整轮运行
            return Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run_process_steps(self, case: ProcessCase, trace: Trace) -> None:
        """按声明顺序执行步骤，并把每次调用记入轨迹。

        步骤由运行器驱动，因此过程用例无需外部 agent 即可端到端运行；需要记录
        真实 agent 轨迹时改用 ``wrap_registry_for_trace`` 包装工具。
        """

        for step in case.steps:
            tool = self.registry.get(step.target)
            try:
                result = tool.invoke(**step.input)
            except Exception as exc:  # noqa: BLE001 - 调用失败也要记入轨迹
                result = ToolResult(
                    ok=False,
                    error_kind=ToolErrorKind.UPSTREAM,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            record_tool_call(trace, step.target, step.input, result)

    def run(
        self,
        cases: Iterable[AnyCase],
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        """执行一批用例，计算汇总，并按需落盘。"""

        merged_metadata = dict(self.metadata)
        merged_metadata.update(metadata or {})
        if self.trace_name is not None:
            merged_metadata.setdefault("trace_name", self.trace_name)
        if self._used_traces:
            merged_metadata["trace_names"] = sorted(set(self._used_traces))
        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=merged_metadata,
        )
        for case in cases:
            verdict, trace = self.run_case(case)
            run.verdicts.append(verdict)
            run.traces.append(trace)
        if self.cassette_session is not None:
            report = self.cassette_session.finish()
            run.metadata["cassette"] = report
            unused = report.get("unused") or []
            if self.cassette_session.strict and unused:
                run.verdicts.append(
                    Verdict(
                        case_id="cassette:unused-interactions",
                        status=Status.FAIL,
                        checks=[
                            CheckOutcome(
                                name="cassette.strict_no_unused",
                                passed=False,
                                expected="no unused interactions",
                                actual=len(unused),
                                message=(
                                    "cassette has unused interactions; "
                                    "fewer calls were made than recorded"
                                ),
                            )
                        ],
                    )
                )
        run.metadata["metrics"] = summarize_metrics(run.verdicts).model_dump(mode="json")
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run
