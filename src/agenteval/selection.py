"""工具选择打分。

BFCL 式的做法：把预测规整为「函数名 + 参数」的结构化调用列表，再与期望调用
逐项比对。平台只负责比对，不负责从自由文本里解析调用——那一步由接入方或
裁判层完成，打分本身必须确定且可复现。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import CaseMetrics, CheckOutcome, Run, Status, Verdict
from agenteval.runner import new_run_id
from agenteval.store import RunStore


class SelectionCall(BaseModel):
    """一次结构化工具调用。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class SelectionCase(BaseModel):
    """一条选择打分用例：请求、可用工具、期望调用与实际调用。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = ""
    available_tools: list[str] = Field(default_factory=list)
    expected: list[SelectionCall] = Field(default_factory=list)
    actual: list[SelectionCall] = Field(default_factory=list)


def load_selection_cases(path: Path) -> list[SelectionCase]:
    """载入选择用例，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "cases" not in payload:
            raise ValueError(f"selection file must contain a list or a 'cases' key: {path}")
        payload = payload["cases"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"selection file must contain at least one case: {path}")
    return [SelectionCase.model_validate(entry) for entry in payload]


def _name_outcome(case: SelectionCase) -> CheckOutcome:
    expected = Counter(call.name for call in case.expected)
    actual = Counter(call.name for call in case.actual)
    missing = sorted((expected - actual).elements())
    extra = sorted((actual - expected).elements())
    passed = not missing and not extra
    details: dict[str, Any] = {}
    if missing:
        details["missing"] = missing
    if extra:
        details["unexpected"] = extra
    if passed:
        message = None
    elif extra and not missing:
        message = f"unexpected function calls: {extra}"
    elif missing and not extra:
        message = f"missing function calls: {missing}"
    else:
        message = f"missing function calls: {missing}; unexpected function calls: {extra}"
    return CheckOutcome(
        name="selection.function_names",
        passed=passed,
        expected=sorted(expected.elements()),
        actual=sorted(actual.elements()),
        message=message,
    )


def _argument_outcome(call: SelectionCall, actual: Sequence[SelectionCall]) -> CheckOutcome:
    candidates = [item for item in actual if item.name == call.name]
    name = f"selection.arguments[{call.name}]"
    if not candidates:
        return CheckOutcome(
            name=name,
            passed=False,
            expected=call.arguments,
            actual=None,
            message=f"{call.name} was never called, so its arguments cannot be checked",
        )
    chosen = max(candidates, key=lambda item: _overlap(item.arguments, call.arguments))
    missing = sorted(key for key in call.arguments if key not in chosen.arguments)
    extra = sorted(key for key in chosen.arguments if key not in call.arguments)
    mismatched = {
        key: {"expected": call.arguments[key], "actual": chosen.arguments[key]}
        for key in call.arguments
        if key in chosen.arguments and chosen.arguments[key] != call.arguments[key]
    }
    passed = not missing and not extra and not mismatched
    problems: list[str] = []
    if missing:
        problems.append(f"missing arguments: {missing}")
    if extra:
        problems.append(f"unexpected arguments: {extra}")
    if mismatched:
        problems.append(f"mismatched arguments: {sorted(mismatched)}")
    return CheckOutcome(
        name=name,
        passed=passed,
        expected=call.arguments,
        actual=chosen.arguments,
        message=None if passed else "; ".join(problems),
    )


def _overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    return sum(1 for key, value in left.items() if key in right and right[key] == value)


def _parallel_outcome(case: SelectionCase) -> CheckOutcome:
    expected = Counter(call.name for call in case.expected)
    actual = Counter(call.name for call in case.actual)
    missing = sorted((expected - actual).elements())
    return CheckOutcome(
        name="selection.parallel_completeness",
        passed=not missing,
        expected=sorted(expected.elements()),
        actual=sorted(actual.elements()),
        message=None if not missing else f"parallel calls are incomplete, missing: {missing}",
    )


def _irrelevance_outcome(case: SelectionCase) -> CheckOutcome:
    called = [call.name for call in case.actual]
    return CheckOutcome(
        name="selection.irrelevance",
        passed=not called,
        expected="no tool call",
        actual=called,
        message=None if not called else f"tools were called for an irrelevant request: {called}",
    )


def evaluate_selection(case: SelectionCase) -> list[CheckOutcome]:
    """对一条选择用例执行全部比对。"""

    if not case.expected:
        return [_irrelevance_outcome(case)]
    outcomes = [_name_outcome(case)]
    for call in case.expected:
        outcomes.append(_argument_outcome(call, case.actual))
    if len(case.expected) > 1:
        outcomes.append(_parallel_outcome(case))
    return outcomes


@dataclass
class SelectionRunner:
    """执行选择打分并产出与其它各层一致的运行记录。"""

    store: RunStore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def run_case(self, case: SelectionCase) -> Verdict:
        started = time.perf_counter()
        checks = evaluate_selection(case)
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        status = Status.PASS if checks and all(check.passed for check in checks) else Status.FAIL
        verdict = Verdict(case_id=case.id, status=status, checks=checks)
        verdict.duration_ms = duration_ms
        verdict.metrics = CaseMetrics(calls=len(case.actual), duration_ms=duration_ms)
        return verdict

    def run(
        self,
        cases: Sequence[SelectionCase],
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        case_list = list(cases)
        merged = dict(self.metadata)
        merged.update(metadata or {})
        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=merged,
        )
        for case in case_list:
            run.verdicts.append(self.run_case(case))
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run
