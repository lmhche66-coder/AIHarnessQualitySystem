from __future__ import annotations

import json
from pathlib import Path

from agenteval.cassette import (
    CASSETTES_DIRNAME,
    Cassette,
    CassetteStore,
    resolve_cassettes_dir,
)
from agenteval.tools import ToolResult


def record(store: CassetteStore, name: str, cassette: Cassette, target: str, args: dict) -> None:
    store.append(name, cassette.record(target, args, ToolResult(ok=True, value={"target": target})))


def test_append_then_load_round_trip(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    cassette = Cassette("demo")
    store.append("demo", cassette.record("t", {"a": 1}, ToolResult(ok=True, value={"x": 1})))
    loaded = store.load("demo")
    assert len(loaded.interactions) == 1
    interaction = loaded.interactions[0]
    assert interaction.target == "t"
    assert interaction.request == {"a": 1}
    assert interaction.response.value == {"x": 1}


def test_append_is_incremental(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    cassette = Cassette("demo")
    record(store, "demo", cassette, "a", {"x": 1})
    record(store, "demo", cassette, "b", {"x": 2})
    lines = store.path("demo").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert len(store.load("demo").interactions) == 2


def test_load_missing_cassette_returns_empty(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    assert store.load("absent").interactions == []
    assert store.exists("absent") is False


def test_reset_removes_cassette_and_meta(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    cassette = Cassette("demo")
    record(store, "demo", cassette, "a", {"x": 1})
    store.write_meta(cassette)
    assert store.exists("demo") is True
    store.reset("demo")
    assert store.exists("demo") is False
    assert store.meta_path("demo").is_file() is False


def test_write_meta_records_counts_and_targets(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    cassette = Cassette("demo")
    record(store, "demo", cassette, "beta", {"x": 1})
    record(store, "demo", cassette, "alpha", {"x": 2})
    meta = json.loads(store.write_meta(cassette).read_text(encoding="utf-8"))
    assert meta["interaction_count"] == 2
    assert meta["targets"] == ["alpha", "beta"]


def test_write_meta_preserves_created_at(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    cassette = Cassette("demo")
    record(store, "demo", cassette, "a", {"x": 1})
    first = json.loads(store.write_meta(cassette).read_text(encoding="utf-8"))
    second = json.loads(store.write_meta(cassette).read_text(encoding="utf-8"))
    assert first["created_at"] == second["created_at"]


def test_list_cassettes_sorted(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path / "cassettes")
    for name in ("b", "a"):
        cassette = Cassette(name)
        record(store, name, cassette, "t", {"x": 1})
    assert store.list_cassettes() == ["a", "b"]


def test_resolve_cassettes_dir_default_and_env_override(tmp_path: Path) -> None:
    assert resolve_cassettes_dir(base=tmp_path, env={}) == tmp_path / ".agenteval" / CASSETTES_DIRNAME
    override = tmp_path / "custom"
    assert (
        resolve_cassettes_dir(base=tmp_path, env={"AGENTEVAL_HOME": str(override)})
        == override / CASSETTES_DIRNAME
    )
