"""三维记分卡：把一次运行折算成「质量 × 成本 × 性能」的诊断报告。

文章的关键主张是：一条用例是否通过与某一项指标是否达标分开看——通过与否只看该
数据集（或其评测范围）的主指标，其余指标只做诊断；当上游错误（如路由误触发）导致
某个下游指标没有执行机会时，该指标标记为跳过，既不计入分子也不计入分母。本模块
把这两条规则落到既有运行记录上，因此对运行器零侵入，也能直接读取历史运行。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from agenteval.metrics import percentile, summarize_metrics
from agenteval.models import Run, Status, Verdict
from agenteval.modules import ModuleName, extract_signals, module_of_metric
from agenteval.process import extract_tool_calls
from agenteval.profiles import (
    METRICS,
    DatasetType,
    EvalProfile,
    EvalScope,
    MetricDimension,
    MetricSpec,
    primary_metric_for,
    profile_for_scope,
)

EVAL_MODE_REAL = "e2e_real"
EVAL_MODE_MOCK = "e2e_mock"
UNKNOWN_SCENE = "unknown"


@dataclass
class _Counts:
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errored: int = 0

    def rate(self) -> float:
        considered = self.total - self.skipped - self.errored
        return self.passed / considered if considered > 0 else 0.0


class MetricStat(BaseModel):
    """单项指标在本次运行中的通过情况。"""

    model_config = ConfigDict(extra="forbid")

    metric: str
    dimension: MetricDimension
    description: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errored: int = 0
    pass_rate: float = 0.0


class SceneStat(BaseModel):
    """按业务场景聚合的主指标通过率，用于快速定位薄弱环节。"""

    model_config = ConfigDict(extra="forbid")

    scene: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errored: int = 0
    pass_rate: float = 0.0


class ModuleStat(BaseModel):
    """按核心模块聚合的指标通过率，用于回答「哪个模块出了问题」。"""

    model_config = ConfigDict(extra="forbid")

    module: ModuleName
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errored: int = 0
    pass_rate: float = 0.0


class CaseStat(BaseModel):
    """单条用例的判定明细，是定位具体问题的入口。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    scene: str | None = None
    dataset_type: str | None = None
    status: Status
    primary_metric: str | None = None
    # pass / fail / skipped / error；skipped 表示主指标因上游错误没有执行机会
    primary_state: str = "fail"
    metrics: dict[str, str] = Field(default_factory=dict)


class CostSummary(BaseModel):
    """成本维度：调用次数与 Token 消耗，口径与运行级指标一致。"""

    model_config = ConfigDict(extra="forbid")

    tool_calls: int = 0
    retries: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    usage_reported: bool = False


class PerformanceSummary(BaseModel):
    """性能维度：单用例耗时的 p50 与 p95，以及总耗时。"""

    model_config = ConfigDict(extra="forbid")

    total_duration_ms: float = 0.0
    duration_p50_ms: float = 0.0
    duration_p95_ms: float = 0.0


class LatencyStat(BaseModel):
    """一项延迟指标的分布：文章 §6.3 的模块级延迟与端到端延迟。"""

    model_config = ConfigDict(extra="forbid")

    metric: str
    samples: int = 0
    mean_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0


class Scorecard(BaseModel):
    """一次运行的完整诊断报告，同时也是机器可读结果。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    scope: EvalScope
    eval_mode: str = EVAL_MODE_REAL
    datasets: list[DatasetType] = Field(default_factory=list)
    primary_metric: str | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    # 原始判定通过率：直接按 Verdict.status 统计，保留既有口径
    pass_rate: float = 0.0
    # 主指标通过率：只看主指标，且把跳过与执行错误排除在分母之外，是报告的头条口径
    primary_pass_rate: float = 0.0
    quality: list[MetricStat] = Field(default_factory=list)
    cost: CostSummary = Field(default_factory=CostSummary)
    performance: PerformanceSummary = Field(default_factory=PerformanceSummary)
    latencies: list[LatencyStat] = Field(default_factory=list)
    modules: list[ModuleStat] = Field(default_factory=list)
    scenes: list[SceneStat] = Field(default_factory=list)
    cases: list[CaseStat] = Field(default_factory=list)


def resolve_scope(run: Run, scope: EvalScope | str | None = None) -> EvalScope:
    """解析评测范围：显式参数优先，其次运行元数据，最后回落到端到端。"""

    if scope is not None:
        return EvalScope(scope)
    declared = run.metadata.get("scope") if isinstance(run.metadata, dict) else None
    if isinstance(declared, str):
        try:
            return EvalScope(declared)
        except ValueError:
            pass
    return EvalScope.END_TO_END


def _eval_mode(run: Run) -> str:
    """解析评测模式：运行元数据里声明的优先，其次从 cassette 推导。

    评测执行引擎会把 ``eval_mode`` 写进元数据；历史运行没有该字段时，回落到
    「重放即 Mock，其余视为真实链路」的既有口径。
    """

    metadata = run.metadata if isinstance(run.metadata, dict) else {}
    declared = metadata.get("eval_mode")
    if isinstance(declared, str) and declared:
        return declared
    cassette = metadata.get("cassette")
    if isinstance(cassette, dict) and cassette.get("mode") == "replay":
        return EVAL_MODE_MOCK
    return EVAL_MODE_REAL


def _metric_spec(metric: str) -> MetricSpec:
    return METRICS.get(
        metric,
        MetricSpec(name=metric, dimension=MetricDimension.QUALITY, description="未登记指标"),
    )


def _primary_state(verdict: Verdict, primary: str | None) -> tuple[str, str | None]:
    """返回 ``(状态, 实际命中的指标标识)``。

    判定顺序：执行错误优先；其次看主指标对应的断言；主指标缺失时回落到整体判定，
    这样未标注指标的既有运行也能给出结论。
    """

    if verdict.status is Status.ERROR:
        return "error", primary
    if primary is not None:
        for check in verdict.checks:
            if (check.metric or check.name) == primary:
                if check.skipped:
                    return "skipped", primary
                return ("pass" if check.passed else "fail"), primary
    return ("pass" if verdict.status is Status.PASS else "fail"), primary


def build_scorecard(
    run: Run,
    scope: EvalScope | str | None = None,
    profile: EvalProfile | None = None,
) -> Scorecard:
    """从一次运行记录构建三维记分卡。"""

    resolved = profile or profile_for_scope(resolve_scope(run, scope))
    primary = resolved.primary_metric or primary_metric_for(resolved.scope)

    metric_counts: dict[str, _Counts] = {}
    scene_counts: dict[str, _Counts] = {}
    cases: list[CaseStat] = []
    totals = _Counts()

    for verdict in run.verdicts:
        totals.total += 1
        if verdict.status is Status.PASS:
            totals.passed += 1
        elif verdict.status is Status.ERROR:
            totals.errored += 1
        else:
            totals.failed += 1

        state, _ = _primary_state(verdict, primary)
        totals_for_primary = scene_counts.setdefault(verdict.scene or UNKNOWN_SCENE, _Counts())
        _bump_primary(totals_for_primary, state)

        metrics: dict[str, str] = {}
        for check in verdict.checks:
            metric = check.metric or check.name
            counts = metric_counts.setdefault(metric, _Counts())
            counts.total += 1
            if check.skipped:
                counts.skipped += 1
                metrics[metric] = "skipped"
            elif check.passed:
                counts.passed += 1
                metrics.setdefault(metric, "pass")
            else:
                counts.failed += 1
                metrics[metric] = "fail"

        cases.append(
            CaseStat(
                case_id=verdict.case_id,
                scene=verdict.scene,
                dataset_type=verdict.dataset_type,
                status=verdict.status,
                primary_metric=primary,
                primary_state=state,
                metrics=metrics,
            )
        )

    quality: list[MetricStat] = []
    for metric in _ordered_metrics(resolved, metric_counts):
        counts = metric_counts[metric]
        spec = _metric_spec(metric)
        if spec.dimension is not MetricDimension.QUALITY:
            # 成本与性能指标来自运行埋点，不参与按断言的通过率统计
            continue
        quality.append(
            MetricStat(
                metric=metric,
                dimension=spec.dimension,
                description=spec.description,
                total=counts.total,
                passed=counts.passed,
                failed=counts.failed,
                skipped=counts.skipped,
                errored=counts.errored,
                pass_rate=counts.rate(),
            )
        )

    run_metrics = summarize_metrics(run.verdicts)
    stored = run.metadata.get("metrics") if isinstance(run.metadata, dict) else None
    if isinstance(stored, dict):
        # 运行记录里的汇总在 agent 用量并入后刷新过，优先采信它
        cost = CostSummary(
            tool_calls=int(stored.get("calls", run_metrics.calls) or 0),
            retries=int(stored.get("retries", run_metrics.retries) or 0),
            input_tokens=int(stored.get("input_tokens", run_metrics.input_tokens) or 0),
            output_tokens=int(stored.get("output_tokens", run_metrics.output_tokens) or 0),
            model_calls=int(stored.get("model_calls", run_metrics.model_calls) or 0),
            usage_reported=bool(stored.get("usage_reported", run_metrics.usage_reported)),
        )
    else:
        cost = CostSummary(
            tool_calls=run_metrics.calls,
            retries=run_metrics.retries,
            input_tokens=run_metrics.input_tokens,
            output_tokens=run_metrics.output_tokens,
            model_calls=run_metrics.model_calls,
            usage_reported=run_metrics.usage_reported,
        )
    # 单用例耗时以判定上的墙钟耗时为准，工具调用跨度只在缺省时兜底
    durations = [verdict.duration_ms or verdict.metrics.duration_ms for verdict in run.verdicts]
    primary_counts = _Counts()
    for counts in scene_counts.values():
        primary_counts.total += counts.total
        primary_counts.passed += counts.passed
        primary_counts.failed += counts.failed
        primary_counts.skipped += counts.skipped
        primary_counts.errored += counts.errored

    scenes = [
        SceneStat(
            scene=scene,
            total=counts.total,
            passed=counts.passed,
            failed=counts.failed,
            skipped=counts.skipped,
            errored=counts.errored,
            pass_rate=counts.rate(),
        )
        for scene, counts in sorted(
            scene_counts.items(), key=lambda item: item[1].rate(), reverse=True
        )
    ]

    modules: list[ModuleStat] = []
    for module in ModuleName:
        module_counts = _Counts()
        for metric, counts in metric_counts.items():
            if module_of_metric(metric) is not module:
                continue
            module_counts.total += counts.total
            module_counts.passed += counts.passed
            module_counts.failed += counts.failed
            module_counts.skipped += counts.skipped
            module_counts.errored += counts.errored
        if module_counts.total == 0:
            continue
        modules.append(
            ModuleStat(
                module=module,
                total=module_counts.total,
                passed=module_counts.passed,
                failed=module_counts.failed,
                skipped=module_counts.skipped,
                errored=module_counts.errored,
                pass_rate=module_counts.rate(),
            )
        )

    return Scorecard(
        run_id=run.run_id,
        scope=resolved.scope,
        eval_mode=_eval_mode(run),
        datasets=resolved.datasets,
        primary_metric=primary,
        total=totals.total,
        passed=totals.passed,
        failed=totals.failed,
        errored=totals.errored,
        skipped=primary_counts.skipped,
        pass_rate=totals.rate(),
        primary_pass_rate=primary_counts.rate(),
        quality=quality,
        cost=cost,
        performance=PerformanceSummary(
            total_duration_ms=round(sum(durations), 3),
            duration_p50_ms=percentile(durations, 0.5),
            duration_p95_ms=percentile(durations, 0.95),
        ),
        latencies=_latency_stats(run, durations),
        modules=modules,
        scenes=scenes,
        cases=cases,
    )


# 模块信号 → 延迟指标。文章 §6.3 要求把各节点耗时落到指标上。
MODULE_LATENCY_METRIC: dict[ModuleName, str] = {
    ModuleName.PERCEPTION: "intent_latency",
    ModuleName.PLANNING: "planning_latency",
    ModuleName.MEMORY: "memory_injection_latency",
    ModuleName.RETRIEVAL: "retrieval_latency",
    ModuleName.TOOL: "tool_latency",
}

_LATENCY_ORDER = [
    "e2e_latency",
    "intent_latency",
    "planning_latency",
    "memory_injection_latency",
    "retrieval_latency",
    "tool_latency",
]


def _latency_stats(run: Run, durations: Sequence[float]) -> list[LatencyStat]:
    """从轨迹的模块信号与判定耗时聚合延迟分布。

    端到端延迟取判定上的墙钟耗时；模块级延迟取对应模块最近信号的 ``duration_ms``。
    没有数据的指标不出现在结果里——不臆造零值。
    """

    buckets: dict[str, list[float]] = {}
    for value in durations:
        if value:
            buckets.setdefault("e2e_latency", []).append(float(value))
    for trace in run.traces:
        for signal in extract_signals(trace):
            if signal.duration_ms is None:
                continue
            metric = MODULE_LATENCY_METRIC.get(signal.module)
            if metric is not None:
                buckets.setdefault(metric, []).append(float(signal.duration_ms))
        # 工具调用耗时由平台侧自动测量，agent 无需上报
        for call in extract_tool_calls(trace):
            if call.duration_ms is not None:
                buckets.setdefault("tool_latency", []).append(float(call.duration_ms))

    stats: list[LatencyStat] = []
    for metric in _LATENCY_ORDER:
        values = buckets.get(metric)
        if not values:
            continue
        stats.append(
            LatencyStat(
                metric=metric,
                samples=len(values),
                mean_ms=round(sum(values) / len(values), 3),
                p50_ms=percentile(values, 0.5),
                p95_ms=percentile(values, 0.95),
            )
        )
    return stats


def _bump_primary(counts: _Counts, state: str) -> None:
    counts.total += 1
    if state == "pass":
        counts.passed += 1
    elif state == "skipped":
        counts.skipped += 1
    elif state == "error":
        counts.errored += 1
    else:
        counts.failed += 1


def _ordered_metrics(profile: EvalProfile, counts: dict[str, _Counts]) -> list[str]:
    """先按画像声明的顺序，再补齐运行中出现但未登记的指标。"""

    ordered = [metric for metric in profile.metrics if metric in counts]
    extra = sorted(metric for metric in counts if metric not in set(profile.metrics))
    return ordered + extra


def format_scorecard(scorecard: Scorecard) -> str:
    """人类可读报告，按质量、成本、性能三栏输出。"""

    lines = [
        f"scorecard: {scorecard.run_id}",
        f"scope: {scorecard.scope.value}  eval_mode: {scorecard.eval_mode}",
        f"datasets: {', '.join(dataset.value for dataset in scorecard.datasets) or 'none'}",
        f"primary metric: {scorecard.primary_metric or 'none'}",
        (
            f"cases: {scorecard.total}  pass: {scorecard.passed}  "
            f"fail: {scorecard.failed}  error: {scorecard.errored}  "
            f"skipped: {scorecard.skipped}"
        ),
        (
            f"pass_rate: {scorecard.pass_rate:.4f}  "
            f"primary_pass_rate: {scorecard.primary_pass_rate:.4f} "
            f"(分母排除跳过与错误)"
        ),
        "quality:",
    ]
    if scorecard.quality:
        for stat in scorecard.quality:
            lines.append(
                f"  - {stat.metric}: {stat.pass_rate:.4f} "
                f"(pass {stat.passed}/{stat.total - stat.skipped - stat.errored}, "
                f"skipped {stat.skipped})"
            )
    else:
        lines.append("  - (no quality metrics recorded)")

    cost = scorecard.cost
    usage = "reported" if cost.usage_reported else "not reported"
    lines.append("cost:")
    lines.append(
        f"  - tool_calls: {cost.tool_calls}  model_calls: {cost.model_calls}  "
        f"retries: {cost.retries}  "
        f"tokens: {cost.input_tokens}/{cost.output_tokens}  usage: {usage}"
    )

    perf = scorecard.performance
    lines.append("performance:")
    lines.append(
        f"  - p50: {perf.duration_p50_ms}ms  p95: {perf.duration_p95_ms}ms  "
        f"total: {perf.total_duration_ms}ms"
    )
    if scorecard.latencies:
        lines.append("latencies:")
        for stat in scorecard.latencies:
            lines.append(
                f"  - {stat.metric}: n={stat.samples} mean={stat.mean_ms}ms "
                f"p50={stat.p50_ms}ms p95={stat.p95_ms}ms"
            )

    if scorecard.modules:
        lines.append("modules:")
        for module in scorecard.modules:
            lines.append(
                f"  - {module.module.value}: {module.pass_rate:.4f} "
                f"(pass {module.passed}/{module.total - module.skipped - module.errored}, "
                f"skipped {module.skipped})"
            )

    if scorecard.scenes:
        lines.append("scenes:")
        for scene in scorecard.scenes:
            lines.append(
                f"  - {scene.scene}: {scene.pass_rate:.4f} "
                f"(pass {scene.passed}/{scene.total - scene.skipped - scene.errored}, "
                f"skipped {scene.skipped})"
            )
    return "\n".join(lines)
