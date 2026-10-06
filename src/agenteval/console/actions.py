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
from agenteval.gate import (
    DEFAULT_BASELINE_NAME,
    BaselineStore,
    GateThresholds,
    evaluate,
)
from agenteval.gold import GoldStore, parse_gold_payload
from agenteval.judge import JudgeDecision, JudgeItem, calibrate
from agenteval.loading import LoadError, load_factory, load_registry
from agenteval.models import ProcessCase
from agenteval.reports import (
    KIND_GATE,
    KIND_JUDGE,
    ConclusionRecord,
    ConclusionStore,
    write_conclusion,
)
from agenteval.runner import ContractRunner, load_cases
from agenteval.store import RunStore
from agenteval.tasks import TaskRunner, load_tasks
from agenteval.tools import ToolRegistry
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

    def trigger_tasks(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """触发一次端到端任务集运行，返回解决数与通过率。"""

        tasks_file = Path(str(_required(body, "tasks_file")))
        registry_ref = str(_required(body, "registry"))
        agent_ref = str(_required(body, "agent"))
        if not tasks_file.is_file():
            raise OperationError(f"tasks file not found: {tasks_file}", status=404)
        try:
            registry_factory = load_factory(registry_ref, "registry")
            agent_factory = load_factory(agent_ref, "agent")
        except LoadError as exc:
            raise OperationError(str(exc)) from exc
        try:
            tasks = load_tasks(tasks_file)
        except ValueError as exc:
            raise OperationError(str(exc)) from exc

        def environment() -> ToolRegistry:
            registry = registry_factory()
            if not isinstance(registry, ToolRegistry):
                raise TypeError("registry factory must return a ToolRegistry instance")
            return registry

        def agent(registry: ToolRegistry, task: Any) -> None:
            runner = agent_factory()
            if not callable(runner):
                raise TypeError("agent factory must return a callable agent")
            runner(registry, task)

        run = TaskRunner(agent=agent, environment=environment, store=self.runs).run(
            tasks,
            metadata={
                "tasks_file": str(tasks_file),
                "registry": registry_ref,
                "agent": agent_ref,
                "triggered_by": "console",
            },
        )
        report = run.metadata.get("task_report") or {}
        return {
            "run_id": run.run_id,
            "total": report.get("total", 0),
            "resolved": report.get("resolved", 0),
            "resolved_rate": report.get("resolved_rate", 0.0),
        }


class GateOperations:
    """从界面触发门禁判定，并把结论写进既有结论目录。"""

    def __init__(
        self,
        runs: RunStore,
        baselines: BaselineStore,
        reports: ConclusionStore,
    ) -> None:
        self.runs = runs
        self.baselines = baselines
        self.reports = reports

    def run(self, body: Mapping[str, Any]) -> dict[str, Any]:
        run_id = str(_required(body, "run_id")).strip()
        try:
            run = self.runs.load(run_id)
        except FileNotFoundError as exc:
            raise OperationError(str(exc), status=404) from exc

        baseline = None
        name = body.get("baseline")
        if name:
            baseline = self.baselines.load(str(name))
            if baseline is None:
                raise OperationError(f"baseline not found: {name}", status=404)

        try:
            thresholds = GateThresholds(
                min_pass_rate=float(body.get("min_pass_rate", 1.0)),
                max_errors=int(body.get("max_errors", 0)),
            )
        except (TypeError, ValueError) as exc:
            raise OperationError(f"invalid gate threshold: {exc}") from exc

        result = evaluate(
            run,
            baseline=baseline,
            thresholds=thresholds,
            allow_regressions=bool(body.get("allow_regressions", False)),
        )
        record = write_conclusion(
            self.reports,
            kind=KIND_GATE,
            title=f"gate {run_id}",
            passed=result.passed,
            run_id=run_id,
            summary={
                "run_id": run_id,
                "pass_rate": result.pass_rate,
                "failed": result.failed,
                "errored": result.errored,
                "regressions": result.regressions,
                "missing": result.missing,
            },
            payload=result.model_dump(mode="json"),
        )
        return {"conclusion_id": record.id, **result.model_dump(mode="json")}


class JudgeOperations:
    """从界面触发裁判校准；未标注样本被跳过而不是拒绝。"""

    def __init__(self, gold: GoldStore, reports: ConclusionStore) -> None:
        self.gold = gold
        self.reports = reports

    def run(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(_required(body, "gold")).strip()
        judge_ref = str(_required(body, "judge")).strip()
        try:
            gold_set = self.gold.load(name)
        except FileNotFoundError as exc:
            raise OperationError(str(exc), status=404) from exc

        labeled = [item for item in gold_set.items if item.expected is not None]
        if not labeled:
            raise OperationError(
                f"gold set '{name}' has no labeled items yet; label at least one first"
            )
        try:
            judge = load_factory(judge_ref, "judge")()
        except LoadError as exc:
            raise OperationError(str(exc)) from exc
        if not callable(judge):
            raise OperationError("judge factory must return a callable judge")

        items = [
            JudgeItem(
                id=item.id,
                prompt=item.prompt,
                response_a=item.response_a,
                response_b=item.response_b,
                expected=item.expected,
            )
            for item in labeled
        ]
        report = calibrate(judge, items)
        record = write_conclusion(
            self.reports,
            kind=KIND_JUDGE,
            title=f"judge calibration ({name})",
            passed=report.usable_for_gate,
            summary={
                "items": report.items,
                "skipped": len(gold_set.items) - len(labeled),
                "agreement": report.agreement,
                "ci_low": report.agreement_ci_low,
                "ci_high": report.agreement_ci_high,
                "position_flip_rate": report.position_flip_rate,
                "length_bias": report.length_bias,
            },
            payload=report.model_dump(mode="json"),
        )
        return {
            "conclusion_id": record.id,
            "skipped": len(gold_set.items) - len(labeled),
            **report.model_dump(mode="json"),
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
    gate: GateOperations
    judge: JudgeOperations
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
            gate=GateOperations(store, BaselineStore(home / "baselines"), ConclusionStore(home / "reports")),
            judge=JudgeOperations(GoldStore(home / "gold"), ConclusionStore(home / "reports")),
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
