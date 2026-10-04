"""质量门禁：基线、阈值与回归判定。

门禁只读取既有的运行记录，不引入新的运行格式，因此对用例与运行器零影响。
结论通过进程退出码表达，可直接作为 CI 步骤的成败。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import Run, Status
from agenteval.store import resolve_home

BASELINES_DIRNAME = "baselines"
BASELINE_SUFFIX = ".json"
DEFAULT_BASELINE_NAME = "default"


class Baseline(BaseModel):
    """一份用例状态快照，用于后续回归比对。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    run_id: str
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verdicts: dict[str, Status] = Field(default_factory=dict)

    @classmethod
    def from_run(cls, run: Run, name: str = DEFAULT_BASELINE_NAME) -> "Baseline":
        return cls(
            name=name,
            run_id=run.run_id,
            verdicts={verdict.case_id: verdict.status for verdict in run.verdicts},
        )


class GateThresholds(BaseModel):
    """门禁阈值。"""

    model_config = ConfigDict(extra="forbid")

    min_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    max_errors: int = Field(default=0, ge=0)


class GateResult(BaseModel):
    """门禁判定结果，同时也是机器可读报告。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    run_id: str
    baseline_run_id: str | None = None
    baseline_name: str | None = None
    total: int = 0
    passed_count: int = 0
    failed: int = 0
    errored: int = 0
    pass_rate: float = 0.0
    thresholds: GateThresholds = Field(default_factory=GateThresholds)
    regressions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    new_cases: list[str] = Field(default_factory=list)
    allow_regressions: bool = False
    reasons: list[str] = Field(default_factory=list)


def evaluate(
    run: Run,
    *,
    baseline: Baseline | None = None,
    thresholds: GateThresholds | None = None,
    allow_regressions: bool = False,
) -> GateResult:
    """依据阈值与基线给出门禁结论。

    计数直接从 verdicts 重算，不信任运行记录里的汇总字段，避免读取到陈旧数据。
    """

    limits = thresholds or GateThresholds()
    counts = {status: 0 for status in Status}
    for verdict in run.verdicts:
        counts[verdict.status] += 1
    total = len(run.verdicts)
    pass_rate = (counts[Status.PASS] / total) if total else 0.0

    regressions, missing, new_cases = _compare_with_baseline(run, baseline)

    reasons: list[str] = []
    if total == 0:
        reasons.append("run contains no verdicts")
    if pass_rate < limits.min_pass_rate:
        reasons.append(
            f"pass rate {pass_rate:.4f} is below the minimum {limits.min_pass_rate:.4f}"
        )
    if counts[Status.ERROR] > limits.max_errors:
        reasons.append(
            f"error count {counts[Status.ERROR]} exceeds the maximum {limits.max_errors}"
        )
    if not allow_regressions:
        if regressions:
            reasons.append(
                f"{len(regressions)} case(s) regressed against baseline: {', '.join(regressions)}"
            )
        if missing:
            reasons.append(
                f"{len(missing)} baseline case(s) missing from the run: {', '.join(missing)}"
            )

    return GateResult(
        passed=not reasons,
        run_id=run.run_id,
        baseline_run_id=baseline.run_id if baseline is not None else None,
        baseline_name=baseline.name if baseline is not None else None,
        total=total,
        passed_count=counts[Status.PASS],
        failed=counts[Status.FAIL],
        errored=counts[Status.ERROR],
        pass_rate=pass_rate,
        thresholds=limits,
        regressions=regressions,
        missing=missing,
        new_cases=new_cases,
        allow_regressions=allow_regressions,
        reasons=reasons,
    )


def _compare_with_baseline(
    run: Run,
    baseline: Baseline | None,
) -> tuple[list[str], list[str], list[str]]:
    if baseline is None:
        return [], [], []
    current: Mapping[str, Status] = {verdict.case_id: verdict.status for verdict in run.verdicts}
    regressions: list[str] = []
    missing: list[str] = []
    for case_id, status in baseline.verdicts.items():
        if case_id not in current:
            missing.append(case_id)
        elif status is Status.PASS and current[case_id] is not Status.PASS:
            regressions.append(case_id)
    new_cases = sorted(set(current) - set(baseline.verdicts))
    return sorted(regressions), sorted(missing), new_cases


def format_report(result: GateResult) -> str:
    """人类可读报告，逐条列出不通过原因。"""

    lines = [f"gate: {'PASS' if result.passed else 'FAIL'}", f"run: {result.run_id}"]
    if result.baseline_run_id is not None:
        lines.append(f"baseline: {result.baseline_name} (run {result.baseline_run_id})")
    else:
        lines.append("baseline: none (thresholds only)")
    lines.append(
        f"cases: {result.total}  pass: {result.passed_count}  "
        f"fail: {result.failed}  error: {result.errored}  "
        f"pass_rate: {result.pass_rate:.4f}"
    )
    lines.append(
        f"thresholds: min_pass_rate={result.thresholds.min_pass_rate:.4f}  "
        f"max_errors={result.thresholds.max_errors}"
    )
    if result.regressions:
        lines.append(f"regressions ({len(result.regressions)}): {', '.join(result.regressions)}")
    if result.missing:
        lines.append(f"missing ({len(result.missing)}): {', '.join(result.missing)}")
    if result.new_cases:
        lines.append(f"new cases ({len(result.new_cases)}): {', '.join(result.new_cases)}")
    if result.reasons:
        lines.append("reasons:")
        lines.extend(f"  - {reason}" for reason in result.reasons)
    return "\n".join(lines)


def resolve_baselines_dir(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析基线目录，即 ``<home>/baselines``。"""

    return resolve_home(base=base, env=env) / BASELINES_DIRNAME


class BaselineStore:
    """基于文件系统的基线存储。"""

    def __init__(self, baselines_dir: Path) -> None:
        self.baselines_dir = Path(baselines_dir)

    @classmethod
    def default(
        cls,
        base: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "BaselineStore":
        return cls(resolve_baselines_dir(base=base, env=env))

    def path(self, name: str = DEFAULT_BASELINE_NAME) -> Path:
        return self.baselines_dir / f"{name}{BASELINE_SUFFIX}"

    def save(self, baseline: Baseline) -> Path:
        path = self.path(baseline.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, name: str = DEFAULT_BASELINE_NAME) -> Baseline | None:
        path = self.path(name)
        if not path.is_file():
            return None
        return Baseline.model_validate_json(path.read_text(encoding="utf-8"))

    def list_baselines(self) -> list[str]:
        if not self.baselines_dir.is_dir():
            return []
        return sorted(
            path.name[: -len(BASELINE_SUFFIX)]
            for path in self.baselines_dir.iterdir()
            if path.is_file() and path.name.endswith(BASELINE_SUFFIX)
        )
