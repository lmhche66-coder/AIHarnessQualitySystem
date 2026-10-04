"""契约用例的运行器与用例文件载入。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml

from agenteval.cassette import CassetteMode, CassetteSession
from agenteval.contracts import check_case
from agenteval.models import Case, CheckOutcome, Run, Status, Trace, Verdict
from agenteval.store import RunStore
from agenteval.tools import ToolRegistry


def new_run_id() -> str:
    """生成按时间排序且唯一的运行标识。"""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


# 这些契约的结论取决于真实调用耗时，而 cassette 只记录请求与响应。
NON_REPLAYABLE_CHECKS = frozenset({"timeout"})


def _non_replayable_reason(case: Case, session: CassetteSession | None) -> str | None:
    """耗时相关契约在非录制模式下无法忠实重放，宁可直接拒绝也不给出错误结论。"""

    if session is None or session.mode is CassetteMode.RECORD:
        return None
    check_kind = getattr(case.check, "kind", None)
    if check_kind in NON_REPLAYABLE_CHECKS:
        return (
            f"check '{check_kind}' depends on real call latency and cannot be replayed from "
            f"cassette '{session.cassette.name}'; run it without --cassette or in record mode"
        )
    return None


def load_cases(path: Path) -> list[Case]:
    """从 JSON 或 YAML 文件载入用例列表。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        payload: Any = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("cases", [])
    if not isinstance(payload, list):
        raise ValueError(f"cases file must contain a list or a 'cases' key: {path}")
    return [Case.model_validate(item) for item in payload]


@dataclass
class ContractRunner:
    """执行契约用例并产出运行记录。"""

    registry: ToolRegistry
    store: RunStore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cassette_session: CassetteSession | None = None

    def run_case(self, case: Case) -> tuple[Verdict, Trace]:
        """执行单条用例，返回判定与轨迹。"""

        trace = Trace(case_id=case.id)
        session = self.cassette_session
        if session is not None:
            session.begin_case(trace)
        start = time.perf_counter()
        try:
            verdict = self._evaluate(case, trace)
        finally:
            if session is not None:
                session.end_case()
        verdict.duration_ms = round((time.perf_counter() - start) * 1000, 3)
        return verdict, trace

    def _evaluate(self, case: Case, trace: Trace) -> Verdict:
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
            return Verdict(case_id=case.id, status=Status.ERROR, error=str(exc))
        try:
            return check_case(case, tool, trace)
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不终止整轮运行
            return Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )

    def run(
        self,
        cases: Iterable[Case],
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        """执行一批用例，计算汇总，并按需落盘。"""

        merged_metadata = dict(self.metadata)
        merged_metadata.update(metadata or {})
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
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run
