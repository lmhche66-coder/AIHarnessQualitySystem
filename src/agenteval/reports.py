"""评测结论的结构化持久化。

门禁、失败归因与裁判校准只给出结论，不产生新的运行记录。把结论单独落盘，既
避免与运行记录重复，也让它们可以被回看与展示。结论引用运行标识，不复制判定
明细——判定明细的唯一来源仍是运行记录。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agenteval.store import resolve_home

REPORTS_DIRNAME = "reports"
REPORT_SUFFIX = ".json"

KIND_GATE = "gate"
KIND_TRIAGE = "triage"
KIND_JUDGE = "judge"
KIND_REPORT = "report"


class ConclusionRecord(BaseModel):
    """一条结论记录：统一外壳加各自类型的报告体。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str | None = None
    passed: bool | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


def new_conclusion_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{kind}-{stamp}-{uuid4().hex[:6]}"


def resolve_reports_dir(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析结论目录，即 ``<home>/reports``。"""

    return resolve_home(base=base, env=env) / REPORTS_DIRNAME


class ConclusionStore:
    """每条结论一个文件；按标识读取是最常见的用法。"""

    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = Path(reports_dir)

    @classmethod
    def default(
        cls,
        base: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "ConclusionStore":
        return cls(resolve_reports_dir(base=base, env=env))

    def path(self, report_id: str) -> Path:
        return self.reports_dir / f"{report_id}{REPORT_SUFFIX}"

    def save(self, record: ConclusionRecord) -> Path:
        path = self.path(record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, report_id: str) -> ConclusionRecord:
        path = self.path(report_id)
        if not path.is_file():
            raise FileNotFoundError(f"conclusion not found: {report_id}")
        return ConclusionRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_records(self) -> list[ConclusionRecord]:
        if not self.reports_dir.is_dir():
            return []
        records: list[ConclusionRecord] = []
        for path in self.reports_dir.iterdir():
            if not path.is_file() or not path.name.endswith(REPORT_SUFFIX):
                continue
            try:
                records.append(
                    ConclusionRecord.model_validate_json(path.read_text(encoding="utf-8"))
                )
            except ValueError:
                continue
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records


def write_conclusion(
    store: ConclusionStore,
    *,
    kind: str,
    title: str,
    summary: Mapping[str, Any],
    payload: Mapping[str, Any],
    run_id: str | None = None,
    passed: bool | None = None,
) -> ConclusionRecord:
    record = ConclusionRecord(
        id=new_conclusion_id(kind),
        kind=kind,
        title=title,
        run_id=run_id,
        passed=passed,
        summary=dict(summary),
        payload=dict(payload),
    )
    store.save(record)
    return record
