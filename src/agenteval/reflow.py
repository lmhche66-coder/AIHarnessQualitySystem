"""增量失败回流。

把「线上与 CI 持续产生的失败」变成会一直跑下去的回归资产：扫描已有的全部运行，
只处理此前没见过的失败模式，把通过自证的候选用例累积进数据集，并用账本记住
水位。判定语义完全复用 ``triage``——期望一律沿用原用例，绝不从失败轨迹反推。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import AnyCase, Run, Status
from agenteval.runner import load_cases
from agenteval.store import RunStore
from agenteval.tasks import load_tasks
from agenteval.trace_store import TraceStore
from agenteval.triage import (
    DEFAULT_TRACE_PREFIX,
    build_candidate,
    classify,
    verify_candidate,
)

REFLOW_DIRNAME = "reflow"
LEDGER_FILENAME = "ledger.json"
DATASET_FILENAME = "regression_cases.json"


class ReflowLedger(BaseModel):
    """已处理失败指纹的水位。"""

    model_config = ConfigDict(extra="forbid")

    processed: dict[str, str] = Field(default_factory=dict)


class ReflowEntry(BaseModel):
    """单条失败样本的处理结果。"""

    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    run_id: str
    case_id: str
    reflowable: bool = False
    verified: bool = False
    candidate_id: str | None = None
    trace_name: str | None = None
    reason: str | None = None
    failed_checks: list[str] = Field(default_factory=list)


class ReflowReport(BaseModel):
    """一次增量回流的汇总，同时是机器可读报告。"""

    model_config = ConfigDict(extra="forbid")

    scanned_runs: int = 0
    scanned_failures: int = 0
    new_failures: int = 0
    duplicates: int = 0
    non_reflowable: int = 0
    emitted: int = 0
    dataset_size: int = 0
    dataset_path: str = ""
    ledger_path: str = ""
    entries: list[ReflowEntry] = Field(default_factory=list)


def fingerprint_for(case_id: str, verdict: Any) -> str:
    """失败指纹：用例 + 失败判据的期望与实际。

    只用用例标识会漏掉「同一用例换了失败原因」；把判据的期望与实际纳入，
    既做精确去重，又能让不同失败模式各自成为独立样本。
    """

    parts = [case_id]
    for check in sorted(verdict.failed_checks, key=lambda item: item.name):
        parts.append(f"{check.name}|{check.expected!r}|{check.actual!r}")
    if verdict.error:
        parts.append(f"error|{verdict.error}")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def default_paths(home: Path) -> tuple[Path, Path]:
    """返回 (数据集路径, 账本路径)。"""

    base = Path(home)
    return base / DATASET_FILENAME, base / LEDGER_FILENAME


def reflow(
    store: RunStore,
    trace_store: TraceStore,
    dataset_path: Path,
    ledger_path: Path,
    trace_prefix: str = DEFAULT_TRACE_PREFIX,
) -> ReflowReport:
    """扫描全部运行，增量地把新失败回流成回归用例。"""

    ledger = _load_ledger(ledger_path)
    dataset = _load_dataset(dataset_path)
    known_ids = {candidate.id for candidate in dataset}
    report = ReflowReport(
        dataset_path=str(dataset_path),
        ledger_path=str(ledger_path),
    )
    now = datetime.now(timezone.utc).isoformat()

    for run_id in store.list_runs():
        try:
            run = store.load(run_id)
        except Exception:  # noqa: BLE001 - 坏运行记录不阻塞回流
            continue
        report.scanned_runs += 1
        cases = _cases_for_run(run)
        cases_by_id: dict[str, AnyCase] = {case.id: case for case in cases}
        traces_by_case = {trace.case_id: trace for trace in run.traces}

        for verdict in run.verdicts:
            if verdict.status is Status.PASS:
                continue
            report.scanned_failures += 1
            fingerprint = fingerprint_for(verdict.case_id, verdict)
            if fingerprint in ledger.processed:
                report.duplicates += 1
                continue
            report.new_failures += 1
            entry = ReflowEntry(
                fingerprint=fingerprint,
                run_id=run_id,
                case_id=verdict.case_id,
                failed_checks=[check.name for check in verdict.failed_checks],
            )
            ledger.processed[fingerprint] = now

            case = cases_by_id.get(verdict.case_id)
            trace = traces_by_case.get(verdict.case_id)
            if case is None:
                entry.reason = "original case definition not found in the cases file"
                report.non_reflowable += 1
                report.entries.append(entry)
                continue

            reflowable, reason = classify(verdict, case, trace is not None)
            entry.reflowable = reflowable
            entry.reason = reason
            if not reflowable or trace is None:
                report.non_reflowable += 1
                report.entries.append(entry)
                continue

            trace_name = f"{trace_prefix}-{_slug(verdict.case_id)}-{fingerprint[:8]}"
            trace_store.save(trace_name, trace, source=f"run:{run_id}/{verdict.case_id}")
            candidate = build_candidate(case, run_id, trace_name, suffix=f"-{fingerprint[:8]}")
            entry.trace_name = trace_name
            entry.candidate_id = candidate.id
            verified, verification = verify_candidate(candidate, verdict, trace)
            entry.verified = verified
            if not verified:
                entry.reason = f"candidate did not reproduce the failure: {verification}"
            elif candidate.id not in known_ids:
                dataset.append(candidate)
                known_ids.add(candidate.id)
                report.emitted += 1
            report.entries.append(entry)

    _write_dataset(dataset_path, dataset)
    _write_ledger(ledger_path, ledger)
    report.dataset_size = len(dataset)
    return report


def format_report(report: ReflowReport) -> str:
    """人类可读报告。"""

    lines = [
        f"runs scanned: {report.scanned_runs}",
        f"failures: {report.scanned_failures}  new: {report.new_failures}  "
        f"duplicates: {report.duplicates}  not reflowable: {report.non_reflowable}",
        f"emitted regression cases: {report.emitted}  dataset size: {report.dataset_size}",
        f"dataset: {report.dataset_path}",
        f"ledger: {report.ledger_path}",
    ]
    for entry in report.entries:
        lines.append(f"  [new] {entry.case_id}  ({entry.run_id})  fp={entry.fingerprint[:8]}")
        if entry.reflowable:
            lines.append(
                f"         candidate={entry.candidate_id} trace={entry.trace_name} "
                f"verified={entry.verified}"
            )
        if entry.reason:
            lines.append(f"         reason: {entry.reason}")
    return "\n".join(lines)


def _cases_for_run(run: Run) -> list[AnyCase]:
    """从运行元数据里找回原始用例定义。"""

    metadata = run.metadata or {}
    cases_file = metadata.get("cases_file")
    if cases_file:
        path = Path(str(cases_file))
        if path.exists():
            return list(load_cases(path))
    tasks_file = metadata.get("tasks_file")
    if tasks_file:
        path = Path(str(tasks_file))
        if path.exists():
            return list(load_tasks(path))
    return []


def _load_ledger(path: Path) -> ReflowLedger:
    path = Path(path)
    if not path.exists():
        return ReflowLedger()
    try:
        return ReflowLedger.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return ReflowLedger()


def _write_ledger(path: Path, ledger: ReflowLedger) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ledger.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_dataset(path: Path) -> list[Any]:
    from agenteval.models import ProcessCase

    path = Path(path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entries = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return []
    return [ProcessCase.model_validate(entry) for entry in entries]


def _write_dataset(path: Path, cases: list[Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cases": [case.model_dump(mode="json") for case in cases]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
