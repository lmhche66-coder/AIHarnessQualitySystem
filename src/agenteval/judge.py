"""裁判校准。

平台不调用任何模型：裁判由外部提供，平台负责量化它与人工标注的一致程度、给出
置信区间、检出位置与长度偏置，并据此判定它能否参与质量门禁。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

Z_95 = 1.96
CI_METHOD = "wilson"


class JudgeDecision(str, Enum):
    """成对偏好判定。"""

    A = "a"
    B = "b"
    TIE = "tie"


Judge = Callable[[str, str, str], JudgeDecision | str]

_SWAPPED = {
    JudgeDecision.A: JudgeDecision.B,
    JudgeDecision.B: JudgeDecision.A,
    JudgeDecision.TIE: JudgeDecision.TIE,
}


class JudgeItem(BaseModel):
    """一条带人工标注的成对样本。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    response_a: str = ""
    response_b: str = ""
    expected: JudgeDecision


class JudgeThresholds(BaseModel):
    """裁判能否进入门禁的阈值。"""

    model_config = ConfigDict(extra="forbid")

    min_agreement: float = Field(default=0.8, ge=0.0, le=1.0)
    max_position_flip_rate: float = Field(default=0.2, ge=0.0, le=1.0)
    max_length_bias: float = Field(default=0.8, ge=0.0, le=1.0)


class Disagreement(BaseModel):
    """裁判与人工标注不一致的样本。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    expected: JudgeDecision
    actual: JudgeDecision


class JudgeReport(BaseModel):
    """校准结果，同时也是机器可读报告。"""

    model_config = ConfigDict(extra="forbid")

    items: int = 0
    agreement: float = 0.0
    agreement_ci_low: float = 0.0
    agreement_ci_high: float = 0.0
    position_flips: int = 0
    position_flip_rate: float = 0.0
    decisive: int = 0
    longer_wins: int = 0
    length_bias: float = 0.0
    thresholds: JudgeThresholds = Field(default_factory=JudgeThresholds)
    usable_for_gate: bool = False
    reasons: list[str] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    ci_method: str = CI_METHOD


def load_gold_set(path: Path) -> list[JudgeItem]:
    """载入成对金标集，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "items" not in payload:
            raise ValueError(f"gold set must contain a list or an 'items' key: {path}")
        payload = payload["items"]
    if not isinstance(payload, list):
        raise ValueError(f"gold set must contain a list of items: {path}")
    items = [JudgeItem.model_validate(entry) for entry in payload]
    if not items:
        raise ValueError(f"gold set contains no items: {path}")
    return items


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """一致率的 Wilson 置信区间。

    小样本下正态近似会给出越界或过窄的区间，Wilson 区间没有这个问题。
    """

    if total <= 0:
        return 0.0, 0.0
    phat = successes / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total))
    return max(0.0, centre - spread / denominator), min(1.0, centre + spread / denominator)


def calibrate(
    judge: Judge,
    items: Sequence[JudgeItem],
    thresholds: JudgeThresholds | None = None,
) -> JudgeReport:
    """量化裁判与人工标注的一致程度与偏置。"""

    item_list = list(items)
    if not item_list:
        raise ValueError("gold set contains no items; nothing to calibrate")
    limits = thresholds or JudgeThresholds()

    agreement_count = 0
    disagreements: list[Disagreement] = []
    flips = 0
    decisive = 0
    longer_wins = 0

    for item in item_list:
        forward = _normalize(judge(item.prompt, item.response_a, item.response_b), item.id)
        if forward is item.expected:
            agreement_count += 1
        else:
            disagreements.append(
                Disagreement(item_id=item.id, expected=item.expected, actual=forward)
            )

        backward = _normalize(judge(item.prompt, item.response_b, item.response_a), item.id)
        if _SWAPPED[backward] is not forward:
            flips += 1

        if forward is not JudgeDecision.TIE:
            decisive += 1
            longer = _longer_side(item)
            if longer is not None and forward is longer:
                longer_wins += 1

    total = len(item_list)
    agreement = agreement_count / total
    ci_low, ci_high = wilson_interval(agreement_count, total)
    flip_rate = flips / total
    length_bias = (longer_wins / decisive) if decisive else 0.0

    reasons: list[str] = []
    if agreement < limits.min_agreement:
        reasons.append(
            f"agreement {agreement:.4f} is below the minimum {limits.min_agreement:.4f}"
        )
    if flip_rate > limits.max_position_flip_rate:
        reasons.append(
            f"position flip rate {flip_rate:.4f} exceeds the maximum "
            f"{limits.max_position_flip_rate:.4f}"
        )
    if length_bias > limits.max_length_bias:
        reasons.append(
            f"longer-response win rate {length_bias:.4f} exceeds the maximum "
            f"{limits.max_length_bias:.4f}"
        )

    return JudgeReport(
        items=total,
        agreement=agreement,
        agreement_ci_low=ci_low,
        agreement_ci_high=ci_high,
        position_flips=flips,
        position_flip_rate=flip_rate,
        decisive=decisive,
        longer_wins=longer_wins,
        length_bias=length_bias,
        thresholds=limits,
        usable_for_gate=not reasons,
        reasons=reasons,
        disagreements=disagreements,
    )


def format_report(report: JudgeReport) -> str:
    """人类可读报告。"""

    verdict = "usable for gate" if report.usable_for_gate else "reference only"
    lines = [
        f"judge: {verdict}",
        f"items: {report.items}  agreement: {report.agreement:.4f} "
        f"(95% CI {report.agreement_ci_low:.4f}-{report.agreement_ci_high:.4f}, "
        f"{report.ci_method})",
        f"position flips: {report.position_flips}/{report.items} "
        f"({report.position_flip_rate:.4f})",
        f"length bias: {report.longer_wins}/{report.decisive} decisive "
        f"({report.length_bias:.4f})",
        f"thresholds: min_agreement={report.thresholds.min_agreement:.2f}  "
        f"max_position_flip_rate={report.thresholds.max_position_flip_rate:.2f}  "
        f"max_length_bias={report.thresholds.max_length_bias:.2f}",
    ]
    if report.reasons:
        lines.append("reasons:")
        lines.extend(f"  - {reason}" for reason in report.reasons)
    if report.disagreements:
        lines.append(f"disagreements ({len(report.disagreements)}):")
        lines.extend(
            f"  - {item.item_id}: expected {item.expected.value}, "
            f"judge said {item.actual.value}"
            for item in report.disagreements
        )
    return "\n".join(lines)


def _normalize(decision: JudgeDecision | str, item_id: str) -> JudgeDecision:
    if isinstance(decision, JudgeDecision):
        return decision
    try:
        return JudgeDecision(str(decision).strip().lower())
    except ValueError as exc:
        raise ValueError(
            f"judge returned an invalid decision for item {item_id}: {decision!r}"
        ) from exc


def _longer_side(item: JudgeItem) -> JudgeDecision | None:
    length_a = len(item.response_a)
    length_b = len(item.response_b)
    if length_a == length_b:
        return None
    return JudgeDecision.A if length_a > length_b else JudgeDecision.B
