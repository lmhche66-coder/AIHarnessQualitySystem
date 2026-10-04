"""命名轨迹的持久化。

轨迹一旦落盘就成了一份固定资产：对它的求值不再调用任何工具，因此同一个结论
可以在不同机器、不同时间重复得到。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import Run, Trace, TraceEvent
from agenteval.store import resolve_home

TRACES_DIRNAME = "traces"
TRACE_SUFFIX = ".jsonl"
META_SUFFIX = ".meta.json"


class TraceMetadata(BaseModel):
    """轨迹的来源与规模信息，便于判断它是否陈旧。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    case_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_count: int = 0
    attempts: int = 0
    side_effects: list[str] = Field(default_factory=list)
    source: str | None = None


def resolve_traces_dir(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析轨迹目录，即 ``<home>/traces``。"""

    return resolve_home(base=base, env=env) / TRACES_DIRNAME


class TraceStore:
    """基于文件系统的命名轨迹存储。"""

    def __init__(self, traces_dir: Path) -> None:
        self.traces_dir = Path(traces_dir)

    @classmethod
    def default(
        cls,
        base: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "TraceStore":
        return cls(resolve_traces_dir(base=base, env=env))

    def path(self, name: str) -> Path:
        return self.traces_dir / f"{name}{TRACE_SUFFIX}"

    def meta_path(self, name: str) -> Path:
        return self.traces_dir / f"{name}{META_SUFFIX}"

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def save(self, name: str, trace: Trace, source: str | None = None) -> Path:
        """保存一条轨迹。事件逐行写入，其余字段写入元信息。"""

        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for event in trace.events:
                handle.write(event.model_dump_json())
                handle.write("\n")
        meta = TraceMetadata(
            name=name,
            case_id=trace.case_id,
            event_count=len(trace.events),
            attempts=trace.attempts,
            side_effects=list(trace.side_effects),
            source=source,
        )
        self.meta_path(name).write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        return path

    def metadata(self, name: str) -> TraceMetadata | None:
        path = self.meta_path(name)
        if not path.is_file():
            return None
        return TraceMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def load(self, name: str) -> Trace:
        """按名称读回轨迹；不存在时抛出 ``FileNotFoundError``。"""

        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"trace not found: {name}")
        events = [
            TraceEvent.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        meta = self.metadata(name)
        return Trace(
            case_id=meta.case_id if meta is not None else name,
            events=events,
            attempts=meta.attempts if meta is not None else 0,
            side_effects=list(meta.side_effects) if meta is not None else [],
        )

    def list_traces(self) -> list[str]:
        if not self.traces_dir.is_dir():
            return []
        return sorted(
            path.name[: -len(TRACE_SUFFIX)]
            for path in self.traces_dir.iterdir()
            if path.is_file() and path.name.endswith(TRACE_SUFFIX)
        )


def extract_trace(run: Run, case_id: str | None = None) -> Trace:
    """从一次运行中取出指定用例的轨迹。

    未指定用例时必须能唯一确定，否则报错要求指明，而不是任意挑一条。
    """

    if case_id is not None:
        for trace in run.traces:
            if trace.case_id == case_id:
                return trace
        raise KeyError(f"run {run.run_id} has no trace for case: {case_id}")
    if not run.traces:
        raise KeyError(f"run {run.run_id} contains no traces")
    if len(run.traces) == 1:
        return run.traces[0]
    available = ", ".join(sorted(trace.case_id for trace in run.traces))
    raise KeyError(
        f"run {run.run_id} contains {len(run.traces)} traces; "
        f"specify --case with one of: {available}"
    )
