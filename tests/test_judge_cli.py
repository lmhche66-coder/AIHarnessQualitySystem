from __future__ import annotations

import json
from pathlib import Path

import pytest

from agenteval.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GOLD = EXAMPLES / "judge_gold.json"
JUDGES = str(EXAMPLES / "demo_judges.py")


def calibrate_with(
    factory: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict]:
    code = main(
        [
            "judge",
            "calibrate",
            "--gold",
            str(GOLD),
            "--judge",
            f"{JUDGES}:{factory}",
            "--json",
        ]
    )
    return code, json.loads(capsys.readouterr().out)


def test_unbiased_judge_is_usable_for_gate(capsys: pytest.CaptureFixture[str]) -> None:
    code, report = calibrate_with("build_keyword_judge", capsys)
    assert code == 0
    assert report["usable_for_gate"] is True
    assert report["agreement"] == 1.0
    assert report["position_flip_rate"] == 0.0
    assert report["reasons"] == []


def test_position_biased_judge_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    code, report = calibrate_with("build_position_biased_judge", capsys)
    assert code == 1
    assert report["usable_for_gate"] is False
    assert report["position_flip_rate"] == 1.0
    assert any("position flip rate" in reason for reason in report["reasons"])


def test_length_biased_judge_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    code, report = calibrate_with("build_length_biased_judge", capsys)
    assert code == 1
    assert report["length_bias"] == 1.0
    assert any("longer-response win rate" in reason for reason in report["reasons"])


def test_text_report_lists_metrics(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "judge",
            "calibrate",
            "--gold",
            str(GOLD),
            "--judge",
            f"{JUDGES}:build_keyword_judge",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "judge: usable for gate" in output
    assert "95% CI" in output


def test_missing_gold_set_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "judge",
            "calibrate",
            "--gold",
            str(tmp_path / "absent.json"),
            "--judge",
            f"{JUDGES}:build_keyword_judge",
        ]
    )
    assert code == 2
    assert "gold set not found" in capsys.readouterr().err
