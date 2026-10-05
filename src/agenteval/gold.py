"""金标集的存取与逐条标注。

人工标注是裁判校准的前置。这里把标注过程当成一等操作：条目允许暂时没有人工
偏好，标注完成后即可直接作为校准输入。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.judge import JudgeDecision
from agenteval.store import resolve_home

GOLD_DIRNAME = "gold"
GOLD_SUFFIX = ".json"


class GoldItem(BaseModel):
    """一条金标样本；``expected`` 为空表示尚未标注。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    response_a: str = ""
    response_b: str = ""
    expected: JudgeDecision | None = None


class GoldSet(BaseModel):
    """一份金标集。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    items: list[GoldItem] = Field(default_factory=list)

    def labeled(self) -> int:
        return sum(1 for item in self.items if item.expected is not None)


@dataclass(frozen=True)
class GoldSummary:
    """列表视图需要的进度信息。"""

    name: str
    items: int
    labeled: int


def resolve_gold_dir(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析金标目录，即 ``<home>/gold``。"""

    return resolve_home(base=base, env=env) / GOLD_DIRNAME


class GoldStore:
    """基于文件系统的金标集存储。"""

    def __init__(self, gold_dir: Path) -> None:
        self.gold_dir = Path(gold_dir)

    @classmethod
    def default(
        cls,
        base: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "GoldStore":
        return cls(resolve_gold_dir(base=base, env=env))

    def path(self, name: str) -> Path:
        return self.gold_dir / f"{name}{GOLD_SUFFIX}"

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def save(self, gold_set: GoldSet) -> Path:
        path = self.path(gold_set.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(gold_set.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, name: str) -> GoldSet:
        path = self.path(name)
        if not path.is_file():
            raise FileNotFoundError(f"gold set not found: {name}")
        payload = self.path(name).read_text(encoding="utf-8")
        parsed = GoldSet.model_validate_json(payload)
        return parsed.model_copy(update={"name": name})

    def summaries(self) -> list[GoldSummary]:
        if not self.gold_dir.is_dir():
            return []
        summaries: list[GoldSummary] = []
        for path in sorted(self.gold_dir.iterdir()):
            if not path.is_file() or not path.name.endswith(GOLD_SUFFIX):
                continue
            try:
                gold_set = GoldSet.model_validate_json(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            summaries.append(
                GoldSummary(
                    name=path.name[: -len(GOLD_SUFFIX)],
                    items=len(gold_set.items),
                    labeled=gold_set.labeled(),
                )
            )
        return summaries

    def label(self, name: str, item_id: str, expected: JudgeDecision) -> GoldSet:
        """记录一条人工偏好；条目不存在时报错而不是追加一条新的。"""

        gold_set = self.load(name)
        for item in gold_set.items:
            if item.id == item_id:
                item.expected = expected
                self.save(gold_set)
                return gold_set
        raise KeyError(f"gold set '{name}' has no item: {item_id}")


def parse_gold_payload(name: str, items: Any) -> GoldSet:
    """把请求体中的条目转成金标集，并在不合法时指明第几条。"""

    if not isinstance(items, list) or not items:
        raise ValueError("gold set must contain at least one item")
    parsed: list[GoldItem] = []
    for index, entry in enumerate(items):
        if not isinstance(entry, Mapping):
            raise ValueError(f"gold item #{index} is not an object")
        try:
            parsed.append(GoldItem.model_validate(entry))
        except ValueError as exc:
            raise ValueError(f"gold item #{index} is invalid: {exc}") from exc
    return GoldSet(name=name, items=parsed)
