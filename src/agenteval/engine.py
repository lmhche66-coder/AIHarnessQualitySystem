"""评测执行引擎：从评测范围到报告。

文章第 6 节把一次评测拆成固定链路：提交评测请求（评测范围 + 评测模式）→ 自动装配
数据集 → 并发执行用例（单条超时、失败重试）→ 采集轨迹与指标 → 结构化评分 → 生成
报告。本模块负责其中「范围 → 数据集 → 用例」的装配与执行编排：

* 评测范围画像决定装配哪些数据集（``profiles.SCOPE_DATASETS``）；
* 数据集注册表决定每个数据集从哪些用例文件加载；
* 执行交给 ``ContractRunner``，并发、单条超时与失败重试都在那里实现；
* 报告复用三维记分卡（``scorecard.build_scorecard``）。

判定与评分仍由外部提供，平台不调用任何模型。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agenteval.dialogue import DialogueRunner
from agenteval.judge_tasks import (
    JudgeCase,
    JudgeTask,
    evaluate as evaluate_judge_task,
    judge_case_from,
    to_check as judge_to_check,
)
from agenteval.models import AnyCase, DialogueCase, Run, Status, TaskCase
from agenteval.runner import execute_cases
from agenteval.dialogue import load_dialogue_cases
from agenteval.tasks import (
    TaskRunner,
    attempt_case_id,
    load_tasks,
    summarize_attempts,
    summarize_tasks,
)
from agenteval.profiles import (
    EvalScope,
    DatasetType,
    profile_for_scope,
)
from agenteval.metrics import summarize_metrics
from agenteval.runner import ContractRunner, load_cases, new_run_id


class EvalMode(str, Enum):
    """两种互补的评测模式：真实链路与注入 Mock 返回值。"""

    REAL = "e2e_real"
    MOCK = "e2e_mock"


class EvalPlan(BaseModel):
    """一次评测请求解析后的执行计划：装配了哪些数据集、用例、指标与执行参数。"""

    model_config = ConfigDict(extra="forbid")

    scope: EvalScope
    eval_mode: EvalMode = EvalMode.REAL
    datasets: list[DatasetType] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    primary_metric: str | None = None
    sources: dict[DatasetType, list[str]] = Field(default_factory=dict)
    missing: list[DatasetType] = Field(default_factory=list)
    concurrency: int = 1
    timeout_s: float | None = None
    retries: int = 0
    retry_interval_s: float = 0.0


class DatasetRegistry:
    """数据集 → 用例文件的注册表；相对路径按注册表文件所在目录解析。"""

    def __init__(self, sources: Mapping[DatasetType | str, Sequence[Path | str]], base: Path | None = None):
        self._base = Path(base) if base is not None else None
        self._sources: dict[DatasetType, list[Path]] = {}
        for key, files in sources.items():
            dataset = DatasetType(key)
            resolved = [self._resolve(Path(file)) for file in files]
            self._sources[dataset] = resolved

    def _resolve(self, path: Path) -> Path:
        if path.is_absolute() or self._base is None:
            return path
        return self._base / path

    @classmethod
    def load(cls, path: Path) -> "DatasetRegistry":
        """从 JSON 或 YAML 载入数据集注册表。"""

        path = Path(path)
        text = path.read_text(encoding="utf-8")
        payload: Any = (
            yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
        )
        if isinstance(payload, dict) and "datasets" in payload:
            payload = payload["datasets"]
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError(
                f"dataset registry must be a non-empty mapping of dataset -> files: {path}"
            )
        return cls(payload, base=path.parent)

    def files_for(self, dataset: DatasetType | str) -> list[Path]:
        return list(self._sources.get(DatasetType(dataset), []))

    @property
    def datasets(self) -> list[DatasetType]:
        return list(self._sources)


def plan_evaluation(
    scope: EvalScope | str,
    registry: DatasetRegistry,
    *,
    eval_mode: EvalMode | str = EvalMode.REAL,
    concurrency: int = 1,
    timeout_s: float | None = None,
    retries: int = 0,
    retry_interval_s: float = 0.0,
) -> EvalPlan:
    """按评测范围装配数据集与用例文件，产出执行计划。"""

    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if retries < 0:
        raise ValueError("retries must not be negative")
    if timeout_s is not None and timeout_s <= 0:
        raise ValueError("timeout_s must be positive when provided")
    if retry_interval_s < 0:
        raise ValueError("retry_interval_s must not be negative")

    profile = profile_for_scope(scope)
    sources: dict[DatasetType, list[str]] = {}
    missing: list[DatasetType] = []
    for dataset in profile.datasets:
        files = [str(path) for path in registry.files_for(dataset) if path.is_file()]
        if files:
            sources[dataset] = files
        else:
            missing.append(dataset)
    if not sources:
        wanted = ", ".join(dataset.value for dataset in profile.datasets)
        raise ValueError(f"no case files registered for any dataset in scope: {wanted}")

    return EvalPlan(
        scope=profile.scope,
        eval_mode=EvalMode(eval_mode),
        datasets=profile.datasets,
        metrics=profile.metrics,
        primary_metric=profile.primary_metric,
        sources=sources,
        missing=missing,
        concurrency=concurrency,
        timeout_s=timeout_s,
        retries=retries,
        retry_interval_s=retry_interval_s,
    )


def load_any_cases(path: Path) -> list[AnyCase]:
    """按文件内容选择加载器：通用 cases、tasks 或 dialogue 三种写法都接受。"""

    try:
        return list(load_cases(path))
    except ValueError as generic_error:
        for loader in (load_tasks, load_dialogue_cases):
            try:
                return list(loader(path))
            except (ValueError, KeyError):
                continue
        raise generic_error


def assemble_cases(plan: EvalPlan) -> list[AnyCase]:
    """按计划加载全部用例，并把数据集归属补写到用例上。"""

    cases: list[AnyCase] = []
    for dataset, files in plan.sources.items():
        for file in files:
            for case in load_any_cases(Path(file)):
                if not getattr(case, "dataset_type", None):
                    case.dataset_type = dataset.value
                cases.append(case)
    if not cases:
        raise ValueError("the evaluation plan assembled no cases")
    return cases


@dataclass
class AgentEvalRunner:
    """按文章 §6.1 的链路驱动评测用例：交给 Agent，采轨迹，再交裁判。

    文章的执行链路只有一条——评测用例（用户输入 + 期望输出 + Mock 数据）交给 Agent
    跑「感知 → 规划 → 记忆 → 工具 → 生成」，平台采集 EvalTrace。因此这里只跑
    **agent 用例**：单轮任务（`TaskCase`）与多轮对话（`DialogueCase`）。

    平台还支持「平台直接调工具、不经 agent」的契约与过程用例，那类用例既没有 agent
    链路，也没有对应的轨迹，属于另一层能力（`agenteval run`），本引擎明确拒收。
    """

    tasks: TaskRunner | None = None
    dialogues: DialogueRunner | None = None
    store: RunStore | None = None
    # 文章 §6.1 步骤 4d：把 Agent 输出与 EvalTrace 一起交给裁判
    judge: Any | None = None
    judge_tasks: list[JudgeTask] | None = None

    def run(
        self,
        cases: Iterable[AnyCase],
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        *,
        concurrency: int = 1,
        timeout_s: float | None = None,
        retries: int = 0,
        retry_interval_s: float = 0.0,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Run:
        case_list = list(cases)
        others = [
            case for case in case_list if not isinstance(case, (TaskCase, DialogueCase))
        ]
        if others:
            raise ValueError(
                f"the evaluation engine drives the agent chain, so it only runs "
                f"task/dialogue cases; found {len(others)} contract/process case(s). "
                f"Run those with 'agenteval run'."
            )
        task_cases = [case for case in case_list if isinstance(case, TaskCase)]
        dialogue_cases = [case for case in case_list if isinstance(case, DialogueCase)]
        if task_cases and self.tasks is None:
            raise ValueError("task cases need an agent; pass --agent/--agents")
        if dialogue_cases and self.dialogues is None:
            raise ValueError("dialogue cases need a turn-based agent; pass --agent/--agents")

        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=dict(metadata or {}),
        )
        engine_meta: dict[str, Any] = {
            "concurrency": concurrency,
            "timeout_s": timeout_s,
            "retries": retries,
            "retry_interval_s": retry_interval_s,
            "cases": len(case_list),
            "kinds": {"task": len(task_cases), "dialogue": len(dialogue_cases)},
        }
        run.metadata["engine"] = engine_meta

        attempts: dict[str, int] = {}
        cancelled = False
        conversations: dict[str, Any] = {}

        def controls() -> dict[str, Any]:
            return {
                "concurrency": concurrency,
                "timeout_s": timeout_s,
                "retries": retries,
                "retry_interval_s": retry_interval_s,
                "cancel_check": cancel_check,
            }

        def absorb(items: list[tuple[str, Any]], results: list[tuple[Any, Any, int]]) -> None:
            for (case_id, _item), (verdict, trace, tries) in zip(items, results):
                run.verdicts.append(verdict)
                if trace is not None:
                    run.traces.append(trace)
                attempts[case_id] = tries

        if dialogue_cases and self.dialogues is not None:
            runner = self.dialogues

            def run_dialogue(case: AnyCase) -> tuple[Any, Any]:
                verdict, trace, conversation = runner.run_case(case)
                verdict.scene = case.scene
                verdict.dataset_type = case.dataset_type
                conversations[case.id] = conversation.model_dump(mode="json")
                return verdict, trace

            items = [(case.id, case) for case in dialogue_cases]
            results, cancelled = execute_cases(items, run_dialogue, **controls())
            absorb(items, results)

        if task_cases and self.tasks is not None:
            runner = self.tasks
            # 任务的 attempts 是 pass@k 语义，展开为独立的执行单元
            units = [
                (attempt_case_id(task.id, attempt, task.attempts), (task, attempt))
                for task in task_cases
                for attempt in range(1, task.attempts + 1)
            ]
            def run_task_unit(item: tuple[Any, int]) -> tuple[Any, Any]:
                task, attempt = item
                verdict, trace = runner.run_task(task, attempt)
                # run_task 不回填归属；这里补上，供记分卡按场景与数据集聚合
                verdict.scene = task.scene
                verdict.dataset_type = task.dataset_type
                return verdict, trace

            results, cancelled = execute_cases(units, run_task_unit, **controls())
            absorb(units, results)
            run.metadata["task_report"] = summarize_tasks(task_cases, run.verdicts)
            run.metadata["attempt_report"] = summarize_attempts(task_cases, run.verdicts)

        if conversations:
            run.metadata["dialogues"] = conversations
        if self.judge is not None and self.judge_tasks:
            self._apply_judging(run, case_list)
        engine_meta["attempts"] = attempts
        if cancelled:
            engine_meta["cancelled"] = True

        run.metadata["metrics"] = summarize_metrics(run.verdicts).model_dump(mode="json")
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run


    def _apply_judging(self, run: Run, case_list: list[AnyCase]) -> None:
        """按用例执行裁判任务，把逐指标结论并进同一份判定。"""

        index: dict[str, AnyCase] = {}
        for case in case_list:
            if isinstance(case, TaskCase):
                for attempt in range(1, case.attempts + 1):
                    index[attempt_case_id(case.id, attempt, case.attempts)] = case
            else:
                index[case.id] = case
        traces = {trace.case_id: trace for trace in run.traces}
        tasks = self.judge_tasks or []
        judged = 0
        for verdict in run.verdicts:
            case = index.get(verdict.case_id)
            if case is None:
                continue
            judge_case: JudgeCase = judge_case_from(case, verdict, traces.get(verdict.case_id))
            wanted = set(judge_case.metrics)
            selected = [task for task in tasks if not wanted or task.metric in wanted]
            for task in selected:
                verdict.checks.append(judge_to_check(evaluate_judge_task(task, judge_case, self.judge)))
                judged += 1
            # 裁判判负时把用例判为不通过（执行错误保持原状）
            if verdict.status is Status.PASS and any(
                not check.passed and not check.skipped for check in verdict.checks
            ):
                verdict.status = Status.FAIL
        run.metadata.setdefault("engine", {})["judged_checks"] = judged


def run_plan(
    plan: EvalPlan,
    runner: Any,
    *,
    run_id: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    cases: Sequence[AnyCase] | None = None,
) -> Run:
    """执行一次评测计划，返回运行记录。"""

    cases = list(cases) if cases is not None else assemble_cases(plan)
    metadata = {
        "scope": plan.scope.value,
        "eval_mode": plan.eval_mode.value,
        "datasets": [dataset.value for dataset in plan.datasets],
        "assembled": {dataset.value: len(files) for dataset, files in plan.sources.items()},
        "missing_datasets": [dataset.value for dataset in plan.missing],
    }
    return runner.run(
        cases,
        metadata=metadata,
        run_id=run_id,
        concurrency=plan.concurrency,
        timeout_s=plan.timeout_s,
        retries=plan.retries,
        retry_interval_s=plan.retry_interval_s,
        cancel_check=cancel_check,
    )


def format_plan(plan: EvalPlan) -> str:
    """人类可读的执行计划。"""

    lines = [
        f"eval plan: {plan.scope.value}  eval_mode: {plan.eval_mode.value}",
        f"datasets: {', '.join(dataset.value for dataset in plan.datasets) or 'none'}",
        f"primary metric: {plan.primary_metric or 'none'}",
        (
            f"execution: concurrency={plan.concurrency}  "
            f"timeout_s={plan.timeout_s if plan.timeout_s is not None else 'none'}  "
            f"retries={plan.retries}  interval={plan.retry_interval_s}s"
        ),
    ]
    for dataset, files in plan.sources.items():
        lines.append(f"  - {dataset.value}: {len(files)} file(s)")
    if plan.missing:
        lines.append(
            f"  - missing (no files registered): {', '.join(d.value for d in plan.missing)}"
        )
    return "\n".join(lines)
