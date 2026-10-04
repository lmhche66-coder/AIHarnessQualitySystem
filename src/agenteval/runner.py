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

from agenteval.contracts import check_case
from agenteval.models import Case, Run, Status, Trace, Verdict
from agenteval.store import RunStore
from agenteval.tools import ToolRegistry


def new_run_id() -> str:
    """生成按时间排序且唯一的运行标识。"""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


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

    def run_case(self, case: Case) -> tuple[Verdict, Trace]:
        """执行单条用例，返回判定与轨迹。"""

        trace = Trace(case_id=case.id)
        start = time.perf_counter()
        try:
            tool = self.registry.get(case.target)
        except KeyError as exc:
            return Verdict(case_id=case.id, status=Status.ERROR, error=str(exc)), trace
        try:
            verdict = check_case(case, tool, trace)
        except Exception as exc:  # noqa: BLE001 - 单条用例失败不终止整轮运行
            verdict = Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )
        verdict.duration_ms = round((time.perf_counter() - start) * 1000, 3)
        return verdict, trace

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
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run
