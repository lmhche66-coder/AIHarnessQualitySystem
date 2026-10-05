"""控制台的四项操作能力。

路由层只做校验与序列化，业务编排放在这里：同一段逻辑既能被界面调用，也能被
将来的命令行复用，规则不会分叉。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agenteval.audit_import import (
    AuditImportError,
    build_trace,
    fidelity_report,
    parse_csv_records,
    parse_json_records,
)
from agenteval.gate import BaselineStore
from agenteval.gold import GoldStore, parse_gold_payload
from agenteval.judge import JudgeDecision
from agenteval.loading import LoadError, load_registry
from agenteval.models import ProcessCase
from agenteval.reports import ConclusionStore
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore
from agenteval.tasks import load_tasks
from agenteval.trace_store import TraceStore
from agenteval.triage import triage_run, write_candidates

CASES_DIRNAME = "cases"
CASES_SUFFIX = ".json"


class OperationError(Exception):
    """操作失败，携带可直接返回给调用方的状态码与原因。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _required(body: Mapping[str, Any], field: str) -> Any:
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise OperationError(f"{field} is required")
    return value


# --------------------------------------------------------------------------- 金标


class GoldOperations:
    def __init__(self, store: GoldStore) -> None:
        self.store = store

    def list_sets(self) -> list[dict[str, Any]]:
        return [
            {"name": summary.name, "items": summary.items, "labeled": summary.labeled}
            for summary in self.store.summaries()
        ]

    def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(_required(body, "name")).strip()
        try:
            gold_set = parse_gold_payload(name, body.get("items"))
        except ValueError as exc:
            raise OperationError(str(exc)) from exc
        self.store.save(gold_set)
        return {"name": name, "items": len(gold_set.items), "labeled": gold_set.labeled()}

    def detail(self, name: str) -> dict[str, Any]:
        try:
            gold_set = self.store.load(name)
        except FileNotFoundError as exc:
            raise OperationError(str(exc), status=404) from exc
        return gold_set.model_dump(mode="json")

    def label(self, name: str, body: Mapping[str, Any]) -> dict[str, Any]:
        item_id = str(_required(body, "item_id")).strip()
        raw = _required(body, "expected")
        try:
            expected = JudgeDecision(str(raw).strip().lower())
        except ValueError as exc:
            raise OperationError(
                f"expected must be one of a, b, tie: {raw!r}"
            ) from exc
        try:
            gold_set = self.store.label(name, item_id, expected)
        except FileNotFoundError as exc:
            raise OperationError(str(exc), status=404) from exc
        except KeyError as exc:
            raise OperationError(exc.args[0] if exc.args else str(exc), status=404) from exc
        return {"name": gold_set.name, "labeled": gold_set.labeled(), "items": len(gold_set.items)}


# --------------------------------------------------------------------------- 轨迹


class TraceOperations:
    def __init__(self, store: TraceStore) -> None:
        self.store = store

    def upload(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(_required(body, "name")).strip()
        content = str(_required(body, "content"))
        case_id = str(body.get("case_id") or name).strip() or name
        source_format = str(body.get("format") or "json").strip().lower()
        try:
            if source_format == "csv":
                records = parse_csv_records(content)
            else:
                records = parse_json_records(json.loads(content))
        except json.JSONDecodeError as exc:
            raise OperationError(f"content is not valid JSON: {exc}") from exc
        except AuditImportError as exc:
            raise OperationError(str(exc)) from exc

        trace = build_trace(records, case_id)
        self.store.save(name, trace, source="console-upload")
        return {
            "name": name,
            "case_id": case_id,
            "records": len(records),
            "events": len(trace.events),
            "limitations": fidelity_report(records),
        }


# --------------------------------------------------------------------------- 回流


def _load_original_cases(path: Path) -> list[Any]:
    try:
        return list(load_cases(path))
    except ValueError:
        return list(load_tasks(path))


class ReflowOperations:
    def __init__(self, runs: RunStore, traces: TraceStore, cases_dir: Path) -> None:
        self.runs = runs
        self.traces = traces
        self.cases_dir = Path(cases_dir)

    def analyze(self, body: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(_required(body, "run_id")).strip()
        try:
            run = self.runs.load(run_id)
        except FileNotFoundError as exc:
            raise OperationError(str(exc), status=404) from exc
        cases_file = body.get("cases_file") or run.metadata.get("cases_file")
        if not cases_file:
            raise OperationError(
                "run does not record its cases file; provide cases_file explicitly"
            )
        path = Path(str(cases_file))
        if not path.is_file():
            raise OperationError(f"cases file not found: {path}", status=404)
        cases = _load_original_cases(path)
        report, candidates = triage_run(run, cases, self.traces)
        return {
            "report": report.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        }

    def write(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(_required(body, "name")).strip()
        selected = body.get("cases") or []
        if not isinstance(selected, list):
            raise OperationError("cases must be a list")
        if not selected:
            return {"name": name, "written": 0, "path": None}
        path = self.cases_dir / f"{name}{CASES_SUFFIX}"
        try:
            parsed = [ProcessCase.model_validate(item) for item in selected]
        except ValueError as exc:
            raise OperationError(f"candidate case is invalid: {exc}") from exc
        write_candidates(path, parsed)
        return {"name": name, "written": len(parsed), "path": str(path)}


# --------------------------------------------------------------------------- 运行


class RunOperations:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    def trigger(self, body: Mapping[str, Any]) -> dict[str, Any]:
        cases_file = Path(str(_required(body, "cases_file")))
        registry_ref = str(_required(body, "registry"))
        if not cases_file.is_file():
            raise OperationError(f"cases file not found: {cases_file}", status=404)
        try:
            registry = load_registry(registry_ref)
        except LoadError as exc:
            raise OperationError(str(exc)) from exc
        try:
            cases = _load_original_cases(cases_file)
        except ValueError as exc:
            raise OperationError(str(exc)) from exc

        runner = ContractRunner(registry=registry, store=self.runs)
        run = runner.run(
            cases,
            metadata={
                "cases_file": str(cases_file),
                "registry": registry_ref,
                "triggered_by": "console",
            },
        )
        return {
            "run_id": run.run_id,
            "total": run.summary.total,
            "passed": run.summary.passed,
            "failed": run.summary.failed,
            "errored": run.summary.errored,
        }


@dataclass
class ConsoleServices:
    """控制台一次请求需要访问的全部存储与操作编排。"""

    runs: RunStore
    reports: ConclusionStore
    baselines: BaselineStore
    gold: GoldOperations
    traces: TraceOperations
    reflow: ReflowOperations
    runner: RunOperations
    allow_actions: bool
    cases_dir: Path

    @classmethod
    def for_home(cls, store: RunStore, allow_actions: bool) -> "ConsoleServices":
        home = store.runs_dir.parent
        trace_store = TraceStore(home / "traces")
        cases_dir = home / CASES_DIRNAME
        return cls(
            runs=store,
            reports=ConclusionStore(home / "reports"),
            baselines=BaselineStore(home / "baselines"),
            gold=GoldOperations(GoldStore(home / "gold")),
            traces=TraceOperations(trace_store),
            reflow=ReflowOperations(store, trace_store, cases_dir),
            runner=RunOperations(store),
            allow_actions=allow_actions,
            cases_dir=cases_dir,
        )

    def list_cases_files(self) -> list[str]:
        if not self.cases_dir.is_dir():
            return []
        return sorted(
            path.name[: -len(CASES_SUFFIX)]
            for path in self.cases_dir.iterdir()
            if path.is_file() and path.name.endswith(CASES_SUFFIX)
        )
