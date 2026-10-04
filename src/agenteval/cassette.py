"""工具交互的录制与回放。

回放必须是 hermetic 的：只要回放有可能悄悄触达真实工具，「可复现」就是假的。
因此 replay 模式在 cassette 缺失时一律返回结构化错误，不做任何回退调用。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import Trace
from agenteval.store import resolve_home
from agenteval.tools import Tool, ToolErrorKind, ToolRegistry, ToolResult

CASSETTES_DIRNAME = "cassettes"
CASSETTE_SUFFIX = ".jsonl"
META_SUFFIX = ".meta.json"
REDACTION_PLACEHOLDER = "***"

DEFAULT_SENSITIVE_FIELDS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)


class CassetteMode(str, Enum):
    """录制与回放模式。"""

    RECORD = "record"
    REPLAY = "replay"
    AUTO = "auto"


class Interaction(BaseModel):
    """一次被录制的工具交互。"""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    target: str
    request: dict[str, Any] = Field(default_factory=dict)
    response: ToolResult
    sequence: int = 0
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CassetteMetadata(BaseModel):
    """cassette 的元信息，便于人工排查与后续工具消费。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    created_at: datetime
    updated_at: datetime
    interaction_count: int
    targets: list[str] = Field(default_factory=list)


def fingerprint(target: str, args: Mapping[str, Any]) -> str:
    """基于规范化 JSON 生成稳定指纹，与参数键的书写顺序无关。"""

    payload = json.dumps(
        {"target": target, "args": args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def redact(
    args: Mapping[str, Any],
    sensitive: Iterable[str] = DEFAULT_SENSITIVE_FIELDS,
) -> dict[str, Any]:
    """对声明的顶层敏感字段做脱敏。"""

    declared = {name.lower() for name in sensitive}
    return {
        key: REDACTION_PLACEHOLDER if key.lower() in declared else value
        for key, value in args.items()
    }


def resolve_cassettes_dir(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析 cassette 目录，即 ``<home>/cassettes``。"""

    return resolve_home(base=base, env=env) / CASSETTES_DIRNAME


class Cassette:
    """内存中的 cassette，按指纹维护有序响应队列。"""

    def __init__(
        self,
        name: str,
        interactions: Sequence[Interaction] = (),
        sensitive: Iterable[str] | None = None,
    ) -> None:
        self.name = name
        self.sensitive = frozenset(sensitive) if sensitive is not None else DEFAULT_SENSITIVE_FIELDS
        self._interactions: list[Interaction] = []
        self._by_key: dict[str, list[Interaction]] = {}
        self._cursors: dict[str, int] = {}
        for interaction in interactions:
            self._index(interaction)

    def _index(self, interaction: Interaction) -> None:
        self._interactions.append(interaction)
        self._by_key.setdefault(interaction.fingerprint, []).append(interaction)

    @property
    def interactions(self) -> list[Interaction]:
        return list(self._interactions)

    def record(self, target: str, args: Mapping[str, Any], response: ToolResult) -> Interaction:
        """记录一次真实调用。指纹基于原始参数，落盘内容经过脱敏。"""

        key = fingerprint(target, args)
        bucket = self._by_key.setdefault(key, [])
        interaction = Interaction(
            fingerprint=key,
            target=target,
            request=redact(args, self.sensitive),
            response=response,
            sequence=len(bucket),
        )
        self._index(interaction)
        self._cursors[key] = self._cursors.get(key, 0) + 1
        return interaction

    def lookup(self, target: str, args: Mapping[str, Any]) -> Interaction | None:
        """按序取出一条已录制交互；耗尽或不存在时返回 ``None``。"""

        key = fingerprint(target, args)
        bucket = self._by_key.get(key, [])
        index = self._cursors.get(key, 0)
        if index >= len(bucket):
            return None
        self._cursors[key] = index + 1
        return bucket[index]

    def unused(self) -> list[Interaction]:
        """返回尚未被消费的交互。"""

        return [
            interaction
            for interaction in self._interactions
            if interaction.sequence >= self._cursors.get(interaction.fingerprint, 0)
        ]

    def reset_cursors(self) -> None:
        self._cursors.clear()


class CassetteStore:
    """基于文件系统的 cassette 存储。"""

    def __init__(self, cassettes_dir: Path) -> None:
        self.cassettes_dir = Path(cassettes_dir)

    @classmethod
    def default(
        cls,
        base: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "CassetteStore":
        return cls(resolve_cassettes_dir(base=base, env=env))

    def path(self, name: str) -> Path:
        return self.cassettes_dir / f"{name}{CASSETTE_SUFFIX}"

    def meta_path(self, name: str) -> Path:
        return self.cassettes_dir / f"{name}{META_SUFFIX}"

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def load(self, name: str, sensitive: Iterable[str] | None = None) -> Cassette:
        """读取 cassette；文件不存在时返回空 cassette。"""

        path = self.path(name)
        interactions: list[Interaction] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    interactions.append(Interaction.model_validate_json(line))
        return Cassette(name=name, interactions=interactions, sensitive=sensitive)

    def append(self, name: str, interaction: Interaction) -> Path:
        """追加一条交互，保证录制过程中途失败也不会丢失已录制内容。"""

        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(interaction.model_dump_json())
            handle.write("\n")
        return path

    def reset(self, name: str) -> None:
        """清空指定 cassette，用于重新录制。"""

        for path in (self.path(name), self.meta_path(name)):
            if path.is_file():
                path.unlink()

    def write_meta(self, cassette: Cassette) -> Path:
        path = self.meta_path(cassette.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        created_at = _existing_created_at(path) or now
        meta = CassetteMetadata(
            name=cassette.name,
            created_at=created_at,
            updated_at=now,
            interaction_count=len(cassette.interactions),
            targets=sorted({interaction.target for interaction in cassette.interactions}),
        )
        path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        return path

    def list_cassettes(self) -> list[str]:
        if not self.cassettes_dir.is_dir():
            return []
        return sorted(
            path.name[: -len(CASSETTE_SUFFIX)]
            for path in self.cassettes_dir.iterdir()
            if path.is_file() and path.name.endswith(CASSETTE_SUFFIX)
        )


def _existing_created_at(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    try:
        return CassetteMetadata.model_validate_json(path.read_text(encoding="utf-8")).created_at
    except ValueError:
        return None


@dataclass
class CassetteSession:
    """一次运行内的 cassette 生命周期与轨迹钩子。"""

    cassette: Cassette
    mode: CassetteMode
    store: CassetteStore | None = None
    strict: bool = False
    _trace: Trace | None = field(default=None, repr=False)

    def begin_case(self, trace: Trace) -> None:
        self._trace = trace

    def end_case(self) -> None:
        self._trace = None

    def emit(self, name: str, payload: dict[str, Any] | None = None, error: str | None = None) -> None:
        if self._trace is not None:
            self._trace.record(name, payload=payload or {}, error=error)

    def finish(self) -> dict[str, Any]:
        """结束本次运行：写出元信息并汇总未使用交互。"""

        unused = self.cassette.unused()
        if self.store is not None and self.mode in {CassetteMode.RECORD, CassetteMode.AUTO}:
            self.store.write_meta(self.cassette)
        return {
            "name": self.cassette.name,
            "mode": self.mode.value,
            "strict": self.strict,
            "interactions": len(self.cassette.interactions),
            "unused": [interaction.fingerprint for interaction in unused],
        }


class CassetteTool:
    """把工具包装成录制或回放行为，对上层完全透明。"""

    def __init__(self, tool: Tool, session: CassetteSession) -> None:
        self._tool = tool
        self._session = session
        self.name = tool.name
        self.input_schema = tool.input_schema

    def invoke(self, **kwargs: Any) -> ToolResult:
        session = self._session
        if session.mode is CassetteMode.RECORD:
            return self._record(kwargs)

        key = fingerprint(self.name, kwargs)
        interaction = session.cassette.lookup(self.name, kwargs)
        if interaction is not None:
            session.emit("cassette_hit", {"target": self.name, "fingerprint": interaction.fingerprint})
            return interaction.response

        if session.mode is CassetteMode.AUTO:
            return self._record(kwargs)

        message = f"cassette miss for {self.name} (fingerprint {key})"
        session.emit("cassette_miss", {"target": self.name, "fingerprint": key}, error=message)
        return ToolResult(
            ok=False,
            error_kind=ToolErrorKind.CASSETTE_MISS,
            error_message=message,
        )

    def _record(self, kwargs: dict[str, Any]) -> ToolResult:
        session = self._session
        result = self._tool.invoke(**kwargs)
        interaction = session.cassette.record(self.name, kwargs, result)
        if session.store is not None:
            session.store.append(session.cassette.name, interaction)
        session.emit(
            "cassette_record",
            {"target": self.name, "fingerprint": interaction.fingerprint},
        )
        return result


def wrap_registry(registry: ToolRegistry, session: CassetteSession) -> ToolRegistry:
    """按会话模式包装注册表中的全部工具。"""

    wrapped = ToolRegistry()
    for name in registry.names():
        wrapped.register(CassetteTool(registry.get(name), session))
    return wrapped
