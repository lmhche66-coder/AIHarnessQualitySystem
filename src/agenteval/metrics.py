"""运行级指标汇总。

指标全部来自轨迹推导，不引入外部计时器与采样器，因此同一份轨迹重复求值会得到
同样的指标，指标本身也可以参与回归比对。
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import Verdict

PERCENTILE_METHOD = "nearest-rank"


class RunMetrics(BaseModel):
    """一次运行的指标汇总。"""

    model_config = ConfigDict(extra="forbid")

    cases: int = 0
    calls: int = 0
    retries: int = 0
    duration_p50_ms: float = 0.0
    duration_p95_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    usage_reported: bool = False
    budget_violations: list[str] = Field(default_factory=list)
    percentile_method: str = PERCENTILE_METHOD


def percentile(values: Sequence[float], fraction: float) -> float:
    """最近秩法：取排序后第 ``round(f * (n - 1))`` 个值。

    分位数没有唯一定义，这里固定口径并在报告中写明，避免读者按别的定义理解。
    """

    if not values:
        return 0.0
    ordered = sorted(values)
    index = round(fraction * (len(ordered) - 1))
    return ordered[min(max(index, 0), len(ordered) - 1)]


def summarize_metrics(verdicts: Sequence[Verdict]) -> RunMetrics:
    durations = [verdict.metrics.duration_ms for verdict in verdicts]
    return RunMetrics(
        cases=len(verdicts),
        calls=sum(verdict.metrics.calls for verdict in verdicts),
        retries=sum(verdict.metrics.retries for verdict in verdicts),
        duration_p50_ms=percentile(durations, 0.5),
        duration_p95_ms=percentile(durations, 0.95),
        input_tokens=sum(verdict.metrics.input_tokens for verdict in verdicts),
        output_tokens=sum(verdict.metrics.output_tokens for verdict in verdicts),
        model_calls=sum(verdict.metrics.model_calls for verdict in verdicts),
        usage_reported=any(verdict.metrics.usage_reported for verdict in verdicts),
        budget_violations=sorted(
            {
                f"{verdict.case_id}:{check.name}"
                for verdict in verdicts
                for check in verdict.failed_checks
                if check.name.startswith("budget.")
            }
        ),
    )
