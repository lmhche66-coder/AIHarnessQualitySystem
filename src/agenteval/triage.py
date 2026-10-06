"""失败样本回流。

把一次真实失败变成可反复执行的回归资产：归因、保存证据轨迹、生成候选用例，
并在交付前自证候选用例确实复现了原失败。

关键约束：期望一律沿用原用例。失败轨迹记录的是「agent 做了什么」，不是
「agent 该做什么」；从它反推期望会产出一条必然通过的假用例。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import (
    AnyCase,
    CheckOutcome,
    ProcessCase,
    Run,
    Status,
    TaskCase,
    Trace,
    Verdict,
)
from agenteval.process import check_process_case, process_handler
from agenteval.trace_store import TraceStore

STATE_DEPENDENT_PREFIXES = ("final_state",)
DEFAULT_TRACE_PREFIX = "repro"
REPRO_SUFFIX = "@repro"


class FailureDetail(BaseModel):
    """单条失败的归因结果。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: str
    failed_checks: list[CheckOutcome] = Field(default_factory=list)
    error: str | None = None
    reflowable: bool = False
    reason: str | None = None
    trace_name: str | None = None
    candidate_id: str | None = None
    verified: bool = False
    verification: str | None = None


class TriageReport(BaseModel):
    """一次归因的汇总，同时也是机器可读报告。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    total_failures: int = 0
    reflowable: int = 0
    verified: int = 0
    details: list[FailureDetail] = Field(default_factory=list)


def trace_checks(case: AnyCase) -> list[Any]:
    """取出原用例中依赖轨迹的判据。终态判据需要环境，不能只靠轨迹评估。"""

    checks = getattr(case, "checks", None) or []
    return [spec for spec in checks if process_handler(spec.kind) is not None]


def classify(verdict: Verdict, case: AnyCase, has_trace: bool) -> tuple[bool, str | None]:
    """判断该失败能否仅凭轨迹复现。"""

    if verdict.status is Status.PASS:
        return False, "case passed; nothing to reflow"
    if verdict.status is not Status.FAIL:
        return False, "verdict is an error, not a failure; it has no reproducible judgement"
    if not isinstance(case, (ProcessCase, TaskCase)):
        return False, "only process and task cases carry a reusable trajectory"
    if not has_trace:
        return False, "run has no trajectory for this case"
    if not verdict.failed_checks:
        return False, "verdict records no failing check to reproduce"
    state_dependent = [
        check.name
        for check in verdict.failed_checks
        if check.name.startswith(STATE_DEPENDENT_PREFIXES)
    ]
    if state_dependent:
        return False, (
            "failure depends on environment state and cannot be reproduced from a trajectory alone: "
            + ", ".join(state_dependent)
        )
    if not trace_checks(case):
        return False, "case declares no trajectory-based check to carry over"
    return True, None


def build_candidate(
    case: AnyCase,
    source_run: str,
    trace_name: str,
    suffix: str = "",
) -> ProcessCase:
    """生成绑定证据轨迹的候选用例，判据沿用原用例中依赖轨迹的部分。"""

    return ProcessCase(
        id=f"{case.id}{REPRO_SUFFIX}{suffix}",
        description=f"Regression reproduction of {case.id} from run {source_run}",
        trace=trace_name,
        checks=list(trace_checks(case)),
    )


def verify_candidate(
    candidate: ProcessCase,
    original: Verdict,
    trace: Trace,
) -> tuple[bool, str | None]:
    """对证据轨迹重新求值，核对失败判据是否与原失败一致。"""

    replay = check_process_case(candidate, trace)
    if replay.status is not Status.FAIL:
        return False, f"candidate verdict is {replay.status.value}, expected fail"
    expected = {check.name for check in original.failed_checks}
    actual = {check.name for check in replay.failed_checks}
    if actual != expected:
        missing = sorted(expected - actual) or ["none"]
        unexpected = sorted(actual - expected) or ["none"]
        return False, (
            f"failing checks differ (missing: {', '.join(missing)}; "
            f"unexpected: {', '.join(unexpected)})"
        )
    return True, None


def triage_run(
    run: Run,
    cases: Sequence[AnyCase],
    trace_store: TraceStore,
    trace_prefix: str = DEFAULT_TRACE_PREFIX,
) -> tuple[TriageReport, list[ProcessCase]]:
    """归因一次运行，并产出已验证的候选用例。"""

    cases_by_id: Mapping[str, AnyCase] = {case.id: case for case in cases}
    traces_by_case = {trace.case_id: trace for trace in run.traces}
    report = TriageReport(run_id=run.run_id)
    candidates: list[ProcessCase] = []

    for verdict in run.verdicts:
        if verdict.status is Status.PASS:
            continue
        detail = FailureDetail(
            case_id=verdict.case_id,
            status=verdict.status.value,
            failed_checks=list(verdict.failed_checks),
            error=verdict.error,
        )
        case = cases_by_id.get(verdict.case_id)
        trace = traces_by_case.get(verdict.case_id)
        if case is None:
            detail.reason = "original case definition not found in the provided cases file"
            report.details.append(detail)
            continue

        reflowable, reason = classify(verdict, case, trace is not None)
        detail.reflowable = reflowable
        detail.reason = reason
        if not reflowable or trace is None:
            report.details.append(detail)
            continue

        trace_name = f"{trace_prefix}-{_slug(verdict.case_id)}"
        trace_store.save(trace_name, trace, source=f"run:{run.run_id}/{verdict.case_id}")
        detail.trace_name = trace_name

        candidate = build_candidate(case, run.run_id, trace_name)
        detail.candidate_id = candidate.id
        verified, verification = verify_candidate(candidate, verdict, trace)
        detail.verified = verified
        detail.verification = verification
        if verified:
            candidates.append(candidate)
        report.details.append(detail)

    report.total_failures = len(report.details)
    report.reflowable = sum(1 for detail in report.details if detail.reflowable)
    report.verified = sum(1 for detail in report.details if detail.verified)
    return report, candidates


def write_candidates(path: Path, candidates: Sequence[ProcessCase]) -> Path:
    """把候选用例写成可直接运行的用例文件。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cases": [candidate.model_dump(mode="json") for candidate in candidates]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def format_report(report: TriageReport) -> str:
    """人类可读报告。"""

    lines = [
        f"run: {report.run_id}",
        f"failures: {report.total_failures}  reflowable: {report.reflowable}  "
        f"verified: {report.verified}",
    ]
    if not report.details:
        lines.append("  no failures")
        return "\n".join(lines)
    for detail in report.details:
        lines.append(f"  [{detail.status}] {detail.case_id}")
        for check in detail.failed_checks:
            lines.append(
                f"         - {check.name}: expected={check.expected!r} actual={check.actual!r}"
            )
            if check.message:
                lines.append(f"           {check.message}")
        if detail.error:
            lines.append(f"         - error: {detail.error}")
        if detail.reflowable:
            lines.append(
                f"         reflow: verified={detail.verified} trace={detail.trace_name} "
                f"candidate={detail.candidate_id}"
            )
        else:
            lines.append(f"         reflow: no ({detail.reason})")
        if detail.verification:
            lines.append(f"         verification: {detail.verification}")
    return "\n".join(lines)


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
