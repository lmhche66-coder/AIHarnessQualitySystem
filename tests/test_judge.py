from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agenteval.judge import (
    JudgeDecision,
    JudgeItem,
    JudgeThresholds,
    calibrate,
    format_report,
    load_gold_set,
    wilson_interval,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GOLD = EXAMPLES / "judge_gold.json"


def make_item(
    item_id: str,
    response_a: str,
    response_b: str,
    expected: str,
) -> JudgeItem:
    return JudgeItem.model_validate(
        {
            "id": item_id,
            "prompt": "p",
            "response_a": response_a,
            "response_b": response_b,
            "expected": expected,
        }
    )


def always_a(prompt: str, response_a: str, response_b: str) -> str:
    return "a"


def prefers_longer(prompt: str, response_a: str, response_b: str) -> str:
    return "a" if len(response_a) > len(response_b) else "b"


def test_load_gold_set_from_example() -> None:
    items = load_gold_set(GOLD)
    expectations = [item.expected.value for item in items]
    assert len(items) == 8
    assert expectations.count("a") == 4
    assert expectations.count("b") == 3
    assert expectations.count("tie") == 1


def test_gold_set_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "gold.json"
    path.write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="no items"):
        load_gold_set(path)


def test_gold_set_rejects_missing_key(tmp_path: Path) -> None:
    path = tmp_path / "gold.json"
    path.write_text('{"samples": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="'items' key"):
        load_gold_set(path)


def test_gold_set_rejects_invalid_expected(tmp_path: Path) -> None:
    path = tmp_path / "gold.json"
    path.write_text(
        '{"items": [{"id": "x", "prompt": "p", "expected": "maybe"}]}', encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_gold_set(path)


def test_calibrate_rejects_empty_items() -> None:
    with pytest.raises(ValueError, match="nothing to calibrate"):
        calibrate(always_a, [])


def test_judge_returning_invalid_decision_is_rejected() -> None:
    def bad_judge(prompt: str, response_a: str, response_b: str) -> str:
        return "maybe"

    with pytest.raises(ValueError, match="invalid decision for item x"):
        calibrate(bad_judge, [make_item("x", "aa", "b", "a")])


def test_agreement_and_disagreements() -> None:
    items = [
        make_item("a-wins", "aa", "b", "a"),
        make_item("b-wins", "b", "aa", "b"),
    ]
    report = calibrate(always_a, items, JudgeThresholds(min_agreement=0.0))
    assert report.agreement == 0.5
    assert [item.item_id for item in report.disagreements] == ["b-wins"]


def test_wilson_interval_widens_for_small_samples() -> None:
    small_low, small_high = wilson_interval(8, 8)
    large_low, large_high = wilson_interval(80, 80)
    assert (small_high - small_low) > (large_high - large_low)


def test_wilson_interval_stays_within_bounds() -> None:
    low, high = wilson_interval(0, 5)
    assert low >= 0.0 and high <= 1.0
    assert high > 0.0


def test_wilson_interval_of_nothing_is_zero() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_position_bias_is_detected() -> None:
    items = [make_item(f"i{index}", "aa", "b", "a") for index in range(4)]
    report = calibrate(always_a, items, JudgeThresholds(min_agreement=0.0))
    assert report.position_flip_rate == 1.0
    assert any("position flip rate" in reason for reason in report.reasons)


def test_unbiased_judge_has_no_flips() -> None:
    items = [make_item(f"i{index}", "aa", "b", "a") for index in range(4)]
    report = calibrate(prefers_longer, items, JudgeThresholds(min_agreement=0.0))
    assert report.position_flips == 0


def test_length_bias_is_detected() -> None:
    items = [
        make_item("long-is-a", "aaa", "b", "a"),
        make_item("long-is-b", "b", "aaa", "a"),
    ]
    report = calibrate(prefers_longer, items, JudgeThresholds(min_agreement=0.0))
    assert report.length_bias == 1.0
    assert any("longer-response win rate" in reason for reason in report.reasons)


def test_equal_length_pairs_are_not_counted_as_longer_wins() -> None:
    items = [make_item("same", "aa", "bb", "tie")]
    report = calibrate(always_a, items, JudgeThresholds(min_agreement=0.0))
    assert report.decisive == 1
    assert report.longer_wins == 0


def test_gate_verdict_lists_every_unmet_threshold() -> None:
    items = [make_item(f"i{index}", "aa", "b", "b") for index in range(4)]
    report = calibrate(always_a, items)
    assert report.usable_for_gate is False
    # 这个裁判同时踩中三条：一致率过低、位置偏置满格、且总选更长的那个
    assert len(report.reasons) == 3
    assert any("agreement" in reason for reason in report.reasons)
    assert any("position flip rate" in reason for reason in report.reasons)
    assert any("longer-response win rate" in reason for reason in report.reasons)


def test_report_contains_metrics_and_thresholds() -> None:
    report = calibrate(always_a, [make_item("i0", "aa", "b", "a")])
    text = format_report(report)
    assert "agreement:" in text
    assert "95% CI" in text
    assert "position flips:" in text
    assert "length bias:" in text
    assert "thresholds:" in text


def test_decision_accepts_plain_strings_case_insensitively() -> None:
    def string_judge(prompt: str, response_a: str, response_b: str) -> str:
        return "A"

    report = calibrate(string_judge, [make_item("i0", "aa", "b", "a")], JudgeThresholds(min_agreement=0.0))
    assert report.agreement == 1.0
    assert isinstance(JudgeDecision.A, JudgeDecision)
