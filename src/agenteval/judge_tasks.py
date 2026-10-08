"""结构化裁判任务（Judge Task）。

平台不调用任何模型：裁判由外部提供。本模块解决的是文章第 5 节的问题——把「某条
用例的某个指标到底达没达标」变成结构化的裁判任务，而不是轻信一个总分：

* 单一职责：一个任务只评一个指标。
* 先推理后判断：裁判返回的 JSON 必须带 ``reasoning``，平台把它留存为判定说明。
* 结构化输出：严格 JSON，按任务类型校验字段与取值，不合法就判 error，绝不静默通过。
* 负例引导：由调用方在 ``instruction`` 里给出通过/不通过对照，平台不生成 Prompt。

六种任务类型对应文章第 5 节：二元判定、单标签分类、多标签匹配、抽取比对、量表
评分、成对偏好。判定结果以既有 ``CheckOutcome`` 承载 ``metric`` 与 ``skipped``，
因此记分卡、门禁与控制台无需改动即可消费逐指标结论。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenteval.metrics import summarize_metrics
from agenteval.models import CheckOutcome, Run, Status, Trace, Verdict
from agenteval.modules import ModuleName, extract_signals
from agenteval.process import extract_tool_calls
from agenteval.profiles import RAG_DEPENDENT_METRICS
from agenteval.runner import new_run_id
from agenteval.store import RunStore


class JudgeTaskKind(str, Enum):
    """六种裁判任务类型。"""

    BINARY = "binary"
    CLASSIFICATION = "classification"
    MULTI_LABEL = "multi_label"
    EXTRACTION = "extraction"
    SCORE = "score"
    PREFERENCE = "preference"


class JudgeState(str, Enum):
    """一个指标判定后的状态。error 与 skipped 都不算通过也不算失败。"""

    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"
    ERROR = "error"


class JudgeTask(BaseModel):
    """面向单一指标的裁判任务。

    各类型用到不同的字段：分类与多标签用 ``labels`` 约束候选标签，抽取用 ``fields``
    声明要比对的字段，评分用 ``min_score``/``max_score``/``threshold`` 定义量表与阈值。
    ``labels``/``fields`` 是「声明时才约束」：留空表示不限制取值范围。
    """

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1)
    kind: JudgeTaskKind
    instruction: str = ""
    labels: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    min_score: float = 0.0
    max_score: float = 1.0
    threshold: float | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "JudgeTask":
        if self.min_score > self.max_score:
            raise ValueError("min_score must not exceed max_score")
        if self.threshold is not None and not (
            self.min_score <= self.threshold <= self.max_score
        ):
            raise ValueError("threshold must lie within [min_score, max_score]")
        return self

    @property
    def effective_threshold(self) -> float:
        return self.min_score if self.threshold is None else self.threshold


class JudgeCase(BaseModel):
    """一条待裁判的用例：输入、被测输出、参考，以及逐指标的期望。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    input: str = ""
    output: str = ""
    reference: str = ""
    metrics: list[str] = Field(default_factory=list)
    expected: dict[str, Any] = Field(default_factory=dict)
    # 期望的路由方向（如 skill_miss）；用于从轨迹判定上游路由是否误触发
    expected_route: str | None = None
    scene: str | None = None
    dataset_type: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class JudgeResult(BaseModel):
    """一个指标裁判后的结构化结论。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    metric: str
    kind: JudgeTaskKind
    state: JudgeState
    reasoning: str = ""
    expected: Any = None
    actual: Any = None
    error: str | None = None


Judge = Callable[[JudgeTask, JudgeCase], "str | Mapping[str, Any]"]


# 指标 → 默认任务类型。口径取自文章第 3 节的指标集与第 5 节的六类任务。
DEFAULT_TASK_KIND: dict[str, JudgeTaskKind] = {
    # 端到端
    "task_completion": JudgeTaskKind.BINARY,
    "multi_turn_completion": JudgeTaskKind.BINARY,
    "instruction_following": JudgeTaskKind.BINARY,
    "faithfulness": JudgeTaskKind.BINARY,
    "abnormal_input_handling": JudgeTaskKind.BINARY,
    "user_satisfaction": JudgeTaskKind.SCORE,
    # 感知
    "intent_accuracy": JudgeTaskKind.CLASSIFICATION,
    "intent_recall": JudgeTaskKind.MULTI_LABEL,
    "intent_precision": JudgeTaskKind.MULTI_LABEL,
    "multi_intent_recognition": JudgeTaskKind.MULTI_LABEL,
    "ambiguous_clarification": JudgeTaskKind.BINARY,
    "fallback_accuracy": JudgeTaskKind.CLASSIFICATION,
    # 规划
    "route_accuracy": JudgeTaskKind.CLASSIFICATION,
    "tool_decision_accuracy": JudgeTaskKind.MULTI_LABEL,
    "retrieval_decision_accuracy": JudgeTaskKind.CLASSIFICATION,
    "planning_path_score": JudgeTaskKind.SCORE,
    # 记忆
    "short_term_memory_retention": JudgeTaskKind.BINARY,
    "long_term_retrieval_precision": JudgeTaskKind.SCORE,
    "long_term_retrieval_recall": JudgeTaskKind.MULTI_LABEL,
    "memory_decay_curve": JudgeTaskKind.BINARY,
    # 工具
    "tool_load_success": JudgeTaskKind.BINARY,
    "tool_call_accuracy": JudgeTaskKind.MULTI_LABEL,
    "tool_call_success": JudgeTaskKind.BINARY,
    "param_mapping_accuracy": JudgeTaskKind.EXTRACTION,
}

_REQUIRED_KEYS: dict[JudgeTaskKind, tuple[str, ...]] = {
    JudgeTaskKind.BINARY: ("verdict",),
    JudgeTaskKind.CLASSIFICATION: ("label",),
    JudgeTaskKind.MULTI_LABEL: ("labels",),
    JudgeTaskKind.EXTRACTION: ("fields",),
    JudgeTaskKind.SCORE: ("score",),
    JudgeTaskKind.PREFERENCE: ("choice",),
}


def build_tasks(
    metrics: Iterable[str],
    overrides: Mapping[str, JudgeTaskKind | str] | None = None,
) -> list[JudgeTask]:
    """按指标集装配裁判任务，保持去重后的稳定顺序，未知指标直接报错。"""

    table = dict(DEFAULT_TASK_KIND)
    if overrides:
        for metric, kind in overrides.items():
            table[metric] = JudgeTaskKind(kind)
    tasks: list[JudgeTask] = []
    seen: set[str] = set()
    for metric in metrics:
        if metric in seen:
            continue
        seen.add(metric)
        if metric not in table:
            raise ValueError(
                f"no judge task kind registered for metric {metric!r}; "
                f"declare it via overrides"
            )
        tasks.append(JudgeTask(metric=metric, kind=table[metric]))
    return tasks


def declared_metrics(case: Any) -> list[str]:
    """取用例检查上声明的指标标识，保持稳定顺序。"""

    metrics: list[str] = []
    for check in getattr(case, "checks", None) or []:
        metric = getattr(check, "metric", None)
        if metric and metric not in metrics:
            metrics.append(metric)
    return metrics


def judge_case_from(case: Any, verdict: Any, trace: Trace | None = None) -> JudgeCase:
    """把一条已执行的用例折算成裁判用例（文章 §6.1 步骤 4d）。

    裁判看三样东西：用户输入、Agent 输出、以及轨迹里的中间数据。期望输出与逐指标
    期望来自用例声明；缺失时留空，由裁判自行判断。
    """

    expected_route = getattr(case, "expected_route", None)
    evidence = (
        evidence_from_trace(trace, expected_route=expected_route) if trace is not None else {}
    )
    declared = declared_metrics(case)
    expected = dict(getattr(case, "expected", None) or {})
    metrics = declared or list(expected)
    return JudgeCase(
        id=getattr(verdict, "case_id", getattr(case, "id", "")),
        input=getattr(case, "user_input", None)
        or getattr(case, "description", None)
        or "",
        output=getattr(verdict, "output", None) or "",
        reference=getattr(case, "expected_output", None) or "",
        metrics=metrics,
        expected=expected,
        expected_route=expected_route,
        scene=getattr(case, "scene", None),
        dataset_type=getattr(case, "dataset_type", None),
        evidence=evidence,
    )


def evidence_from_trace(trace: Trace, expected_route: str | None = None) -> dict[str, Any]:
    """把一次执行的轨迹折算成裁判可用的证据。

    文章 §6.3：Judge 不只看 Agent 的输出文本，还要看 Trace 里的中间数据——是否命中
    Skill、路由方向、调用了哪些工具、检索到哪些片段、各段耗时。这个函数把这些信号
    抽成 ``JudgeCase.evidence``，于是裁判层与模块信号层接上了。
    """

    signals = extract_signals(trace)
    perception = _last_signal_payload(signals, ModuleName.PERCEPTION)
    planning = _last_signal_payload(signals, ModuleName.PLANNING)
    memory = _last_signal_payload(signals, ModuleName.MEMORY)
    retrieval = _last_signal_payload(signals, ModuleName.RETRIEVAL)

    tool_calls = [
        {"target": call.target, "args": call.args, "ok": call.ok, "error_kind": call.error_kind}
        for call in extract_tool_calls(trace)
    ]
    durations = {
        signal.module.value: signal.duration_ms
        for signal in signals
        if signal.duration_ms is not None
    }

    # 多轮对话的用量是逐轮写入的，这里对整段对话求和
    usage: dict[str, Any] = {}
    for event in trace.events:
        if event.name != "model.usage":
            continue
        for key in ("model_calls", "input_tokens", "output_tokens"):
            value = event.payload.get(key)
            if value is not None:
                usage[key] = usage.get(key, 0) + value

    evidence: dict[str, Any] = {
        "intent": perception.get("intent"),
        "skill": perception.get("skill"),
        "skill_hit": perception.get("hit"),
        "route": planning.get("route"),
        "planned_tools": list(planning.get("tools") or []),
        "tools": [call["target"] for call in tool_calls],
        "tool_calls": tool_calls,
        "session_turns": memory.get("turns"),
        "injected": list(memory.get("injected") or []),
        "chunks": list(retrieval.get("chunks") or []),
        "retrieval_count": retrieval.get("count"),
        "usage": usage,
        "durations_ms": durations,
    }
    if expected_route is not None and planning.get("route") is not None:
        evidence["route_error"] = str(planning.get("route")) != str(expected_route)
    return evidence


def _last_signal_payload(signals: Sequence[Any], module: ModuleName) -> dict[str, Any]:
    """取某模块最近一条信号的 payload，供证据组装使用。"""

    payload: dict[str, Any] = {}
    for signal in signals:
        if signal.module is module:
            payload = dict(signal.payload)
    return payload


def load_cases(path: Path) -> list[JudgeCase]:
    """从 JSON 或 YAML 载入裁判用例列表。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = (
        yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    )
    if isinstance(payload, dict):
        if "cases" not in payload:
            raise ValueError(f"judge cases file must contain a list or a 'cases' key: {path}")
        payload = payload["cases"]
    if not isinstance(payload, list):
        raise ValueError(f"judge cases file must contain a list of cases: {path}")
    cases = [JudgeCase.model_validate(entry) for entry in payload]
    if not cases:
        raise ValueError(f"judge cases file contains no cases: {path}")
    return cases


def load_task_specs(path: Path) -> list[JudgeTask]:
    """从 JSON 或 YAML 载入任务规格，用于声明标签、字段与评分阈值。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = (
        yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    )
    if isinstance(payload, dict):
        if "tasks" not in payload:
            raise ValueError(f"task spec file must contain a list or a 'tasks' key: {path}")
        payload = payload["tasks"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"task spec file must contain a non-empty list of tasks: {path}")
    return [JudgeTask.model_validate(entry) for entry in payload]


def _as_payload(raw: str | Mapping[str, Any]) -> dict[str, Any]:
    """把裁判输出统一成字典；字符串必须是可解析的 JSON 对象。"""

    if isinstance(raw, Mapping):
        payload: Any = dict(raw)
    elif isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"judge output is not valid JSON: {exc}") from exc
    else:
        raise ValueError(
            f"judge output must be a JSON object or a JSON string, got {type(raw).__name__}"
        )
    if not isinstance(payload, dict):
        raise ValueError("judge output must be a JSON object")
    return payload


def parse_output(task: JudgeTask, raw: str | Mapping[str, Any]) -> tuple[str, Any]:
    """按任务类型校验裁判输出，返回 ``(reasoning, actual)``。

    非 JSON、缺字段、类型不符、标签越界、分数越界都会抛出可读的 ``ValueError``。
    """

    payload = _as_payload(raw)

    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("judge output is missing a non-empty 'reasoning' string")

    missing = [key for key in _REQUIRED_KEYS[task.kind] if key not in payload]
    if missing:
        raise ValueError(
            f"judge output for {task.kind.value} is missing field(s): {', '.join(missing)}"
        )

    if task.kind is JudgeTaskKind.BINARY:
        verdict = str(payload["verdict"]).strip().lower()
        if verdict not in {"pass", "fail"}:
            raise ValueError(f"binary verdict must be 'pass' or 'fail', got {payload['verdict']!r}")
        return reasoning, verdict

    if task.kind is JudgeTaskKind.CLASSIFICATION:
        label = str(payload["label"]).strip()
        if task.labels and label not in task.labels:
            raise ValueError(f"label {label!r} is outside the declared labels {task.labels}")
        return reasoning, label

    if task.kind is JudgeTaskKind.MULTI_LABEL:
        labels = payload["labels"]
        if not isinstance(labels, list) or not all(isinstance(item, str) for item in labels):
            raise ValueError("multi_label 'labels' must be a list of strings")
        cleaned = [item.strip() for item in labels]
        outside = [item for item in cleaned if task.labels and item not in task.labels]
        if outside:
            raise ValueError(f"labels {outside!r} are outside the declared labels {task.labels}")
        return reasoning, cleaned

    if task.kind is JudgeTaskKind.EXTRACTION:
        fields = payload["fields"]
        if not isinstance(fields, Mapping):
            raise ValueError("extraction 'fields' must be a JSON object")
        normalized = {str(key): value for key, value in fields.items()}
        unknown = [key for key in normalized if task.fields and key not in task.fields]
        if unknown:
            raise ValueError(
                f"extraction returned undeclared field(s): {', '.join(sorted(unknown))}"
            )
        return reasoning, normalized

    if task.kind is JudgeTaskKind.SCORE:
        score = payload["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("score must be a number")
        value = float(score)
        if not (task.min_score <= value <= task.max_score):
            raise ValueError(
                f"score {value} is outside the scale [{task.min_score}, {task.max_score}]"
            )
        return reasoning, value

    # preference
    choice = str(payload["choice"]).strip().lower()
    if choice not in {"a", "b", "tie"}:
        raise ValueError(f"preference choice must be 'a', 'b' or 'tie', got {payload['choice']!r}")
    return reasoning, choice


def _fold(task: JudgeTask, case: JudgeCase, actual: Any) -> bool:
    """按任务类型把结论与期望比较，得出通过与否。"""

    expected = case.expected.get(task.metric)

    if task.kind is JudgeTaskKind.BINARY:
        want = str(expected if expected is not None else "pass").strip().lower()
        return actual == want

    if task.kind is JudgeTaskKind.CLASSIFICATION:
        return _norm(expected) == _norm(actual)

    if task.kind is JudgeTaskKind.MULTI_LABEL:
        return _label_set(expected) == _label_set(actual)

    if task.kind is JudgeTaskKind.EXTRACTION:
        want = dict(expected or {})
        got = dict(actual or {})
        # 未声明字段时比对全部字段，避免空字段列表退化成「永远通过」
        keys = task.fields or sorted(set(want) | set(got))
        return all(_norm(got.get(key)) == _norm(want.get(key)) for key in keys)

    if task.kind is JudgeTaskKind.SCORE:
        return float(actual) >= task.effective_threshold

    # preference
    want = str(expected if expected is not None else "a").strip().lower()
    return actual == want


def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _label_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.strip()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(item).strip() for item in value}
    return {str(value).strip()}


def is_skipped(task: JudgeTask, case: JudgeCase) -> bool:
    """路由误触发时，依赖检索证据的指标跳过，避免被上游错误污染。"""

    if not bool(case.evidence.get("route_error")):
        return False
    return task.metric in RAG_DEPENDENT_METRICS


def evaluate(task: JudgeTask, case: JudgeCase, judge: Judge) -> JudgeResult:
    """执行一个裁判任务并折算结论；裁判异常或输出非法都判 error。"""

    if is_skipped(task, case):
        return JudgeResult(
            case_id=case.id,
            metric=task.metric,
            kind=task.kind,
            state=JudgeState.SKIPPED,
            reasoning="skipped: upstream route error removed the evidence this metric needs",
            expected=case.expected.get(task.metric),
        )

    expected = case.expected.get(task.metric)
    try:
        raw = judge(task, case)
        reasoning, actual = parse_output(task, raw)
    except Exception as exc:  # noqa: BLE001 - 单条任务失败不终止整轮
        return JudgeResult(
            case_id=case.id,
            metric=task.metric,
            kind=task.kind,
            state=JudgeState.ERROR,
            expected=expected,
            error=f"{type(exc).__name__}: {exc}",
        )

    passed = _fold(task, case, actual)
    return JudgeResult(
        case_id=case.id,
        metric=task.metric,
        kind=task.kind,
        state=JudgeState.PASS if passed else JudgeState.FAIL,
        reasoning=reasoning,
        expected=expected,
        actual=actual,
    )


def tasks_for_case(case: JudgeCase, tasks: Sequence[JudgeTask]) -> list[JudgeTask]:
    """取该用例声明的指标对应的任务；未声明则用全部任务。"""

    if not case.metrics:
        return list(tasks)
    wanted = set(case.metrics)
    return [task for task in tasks if task.metric in wanted]


def to_check(result: JudgeResult) -> CheckOutcome:
    """把结构化结论折算成既有断言，供记分卡与门禁消费。"""

    return CheckOutcome(
        name=result.metric,
        passed=result.state is JudgeState.PASS,
        expected=result.expected,
        actual=result.actual,
        message=result.error or result.reasoning or None,
        metric=result.metric,
        skipped=result.state is JudgeState.SKIPPED,
    )


def evaluate_case(case: JudgeCase, tasks: Sequence[JudgeTask], judge: Judge) -> Verdict:
    """对一条用例逐指标执行裁判，产出判定。"""

    verdict = Verdict(
        case_id=case.id,
        status=Status.PASS,
        scene=case.scene,
        dataset_type=case.dataset_type,
    )
    selected = tasks_for_case(case, tasks)
    if not selected:
        verdict.status = Status.ERROR
        verdict.error = "no judge task matched this case's declared metrics"
        return verdict

    for task in selected:
        result = evaluate(task, case, judge)
        verdict.checks.append(to_check(result))
        if result.state is JudgeState.ERROR:
            verdict.status = Status.ERROR
            if verdict.error is None:
                verdict.error = f"{result.metric}: {result.error}"

    if verdict.status is Status.ERROR:
        return verdict
    if any(not check.passed and not check.skipped for check in verdict.checks):
        verdict.status = Status.FAIL
    return verdict


def run_tasks(
    cases: Sequence[JudgeCase],
    tasks: Sequence[JudgeTask],
    judge: Judge,
    *,
    store: RunStore | None = None,
    run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Run:
    """执行一批裁判用例，汇总成本与耗时，并按需落盘。"""

    run = Run(
        run_id=run_id or new_run_id(),
        started_at=datetime.now(timezone.utc),
        metadata=dict(metadata or {}),
    )
    run.metadata.setdefault("judge_tasks", {"metrics": [task.metric for task in tasks]})
    for case in cases:
        start = time.perf_counter()
        verdict = evaluate_case(case, tasks, judge)
        verdict.duration_ms = round((time.perf_counter() - start) * 1000, 3)
        run.verdicts.append(verdict)
    run.metadata["metrics"] = summarize_metrics(run.verdicts).model_dump(mode="json")
    run.finished_at = datetime.now(timezone.utc)
    run.refresh_summary()
    if store is not None:
        store.save(run)
    return run


def format_run(run: Run) -> str:
    """人类可读的裁判任务报告。"""

    lines = [f"judge tasks: {run.run_id}", f"cases: {len(run.verdicts)}"]
    for verdict in run.verdicts:
        states = ", ".join(
            f"{check.metric}={_state_of(check)}{_reason_of(check)}" for check in verdict.checks
        )
        lines.append(f"  - {verdict.case_id}: {verdict.status.value}  {states}")
        if verdict.error:
            lines.append(f"      error: {verdict.error}")
    return "\n".join(lines)


def _state_of(check: CheckOutcome) -> str:
    if check.skipped:
        return "skipped"
    return "pass" if check.passed else "fail"


def _reason_of(check: CheckOutcome) -> str:
    return f" ({check.message})" if check.message else ""
