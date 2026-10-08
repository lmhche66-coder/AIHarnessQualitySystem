from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.judge_tasks import (
    DEFAULT_TASK_KIND,
    evidence_from_trace,
    JudgeCase,
    JudgeState,
    JudgeTask,
    JudgeTaskKind,
    build_tasks,
    evaluate,
    evaluate_case,
    is_skipped,
    load_cases,
    load_task_specs,
    run_tasks,
)
from agenteval.models import Status
from agenteval.store import RunStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def judge_returning(payload):
    def judge(task, case):
        return payload

    return judge


def as_json(**payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


# --- 任务装配 ---------------------------------------------------------------


def test_build_tasks_maps_metrics_to_kinds() -> None:
    tasks = build_tasks(["task_completion", "route_accuracy", "param_mapping_accuracy"])
    assert [(task.metric, task.kind) for task in tasks] == [
        ("task_completion", JudgeTaskKind.BINARY),
        ("route_accuracy", JudgeTaskKind.CLASSIFICATION),
        ("param_mapping_accuracy", JudgeTaskKind.EXTRACTION),
    ]


def test_build_tasks_deduplicates_and_rejects_unknown() -> None:
    tasks = build_tasks(["task_completion", "task_completion"])
    assert [task.metric for task in tasks] == ["task_completion"]
    with pytest.raises(ValueError):
        build_tasks(["not_a_metric"])


def test_default_kind_registry_covers_profiled_metrics() -> None:
    from agenteval.profiles import METRICS

    # 所有质量指标都应有默认裁判任务类型；成本与性能来自埋点，不在此列
    from agenteval.profiles import MetricDimension

    quality = {
        name
        for name, spec in METRICS.items()
        if spec.dimension is MetricDimension.QUALITY
    }
    missing = quality - set(DEFAULT_TASK_KIND)
    assert not missing, missing


# --- 二元判定 ---------------------------------------------------------------


def test_binary_pass_and_fail() -> None:
    task = JudgeTask(metric="task_completion", kind=JudgeTaskKind.BINARY)
    case = JudgeCase(id="c1", expected={"task_completion": "pass"})
    assert evaluate(task, case, judge_returning(as_json(reasoning="ok", verdict="pass"))).state is JudgeState.PASS
    failing = JudgeCase(id="c2", expected={"task_completion": "fail"})
    assert evaluate(task, failing, judge_returning(as_json(reasoning="no", verdict="fail"))).state is JudgeState.PASS
    assert evaluate(task, case, judge_returning(as_json(reasoning="no", verdict="fail"))).state is JudgeState.FAIL


def test_binary_defaults_to_pass_when_expected_missing() -> None:
    task = JudgeTask(metric="faithfulness", kind=JudgeTaskKind.BINARY)
    case = JudgeCase(id="c1")
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", verdict="pass"))).state is JudgeState.PASS


# --- 结构化输出校验 ---------------------------------------------------------


def test_non_json_output_is_error() -> None:
    task = JudgeTask(metric="task_completion", kind=JudgeTaskKind.BINARY)
    result = evaluate(task, JudgeCase(id="c1"), judge_returning("not json"))
    assert result.state is JudgeState.ERROR
    assert "JSON" in (result.error or "")


def test_missing_reasoning_is_error() -> None:
    task = JudgeTask(metric="task_completion", kind=JudgeTaskKind.BINARY)
    result = evaluate(task, JudgeCase(id="c1"), judge_returning(as_json(verdict="pass")))
    assert result.state is JudgeState.ERROR
    assert "reasoning" in (result.error or "")


def test_missing_kind_field_is_error() -> None:
    task = JudgeTask(metric="route_accuracy", kind=JudgeTaskKind.CLASSIFICATION)
    result = evaluate(task, JudgeCase(id="c1"), judge_returning(as_json(reasoning="r")))
    assert result.state is JudgeState.ERROR
    assert "label" in (result.error or "")


def test_classification_label_outside_declared_is_error() -> None:
    task = JudgeTask(
        metric="route_accuracy", kind=JudgeTaskKind.CLASSIFICATION, labels=["skill_hit", "skill_miss"]
    )
    result = evaluate(task, JudgeCase(id="c1"), judge_returning(as_json(reasoning="r", label="nope")))
    assert result.state is JudgeState.ERROR
    assert "outside" in (result.error or "")


def test_score_out_of_scale_is_error() -> None:
    task = JudgeTask(metric="planning_path_score", kind=JudgeTaskKind.SCORE, threshold=0.5)
    result = evaluate(task, JudgeCase(id="c1"), judge_returning(as_json(reasoning="r", score=1.5)))
    assert result.state is JudgeState.ERROR


def test_extraction_undeclared_field_is_error() -> None:
    task = JudgeTask(
        metric="param_mapping_accuracy", kind=JudgeTaskKind.EXTRACTION, fields=["country"]
    )
    result = evaluate(
        task, JudgeCase(id="c1"), judge_returning(as_json(reasoning="r", fields={"planet": "Mars"}))
    )
    assert result.state is JudgeState.ERROR
    assert "undeclared" in (result.error or "")


def test_judge_exception_is_error() -> None:
    def boom(task, case):
        raise RuntimeError("judge exploded")

    result = evaluate(
        JudgeTask(metric="task_completion", kind=JudgeTaskKind.BINARY), JudgeCase(id="c1"), boom
    )
    assert result.state is JudgeState.ERROR
    assert "judge exploded" in (result.error or "")


# --- 逐类型折算 -------------------------------------------------------------


def test_classification_matches_expected_label() -> None:
    task = JudgeTask(metric="route_accuracy", kind=JudgeTaskKind.CLASSIFICATION, labels=["skill_hit", "skill_miss"])
    case = JudgeCase(id="c1", expected={"route_accuracy": "skill_miss"})
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", label="skill_miss"))).state is JudgeState.PASS
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", label="skill_hit"))).state is JudgeState.FAIL


def test_multi_label_compares_sets() -> None:
    task = JudgeTask(
        metric="tool_call_accuracy", kind=JudgeTaskKind.MULTI_LABEL, labels=["a", "b", "c"]
    )
    case = JudgeCase(id="c1", expected={"tool_call_accuracy": ["a", "b"]})
    same_order = evaluate(task, case, judge_returning(as_json(reasoning="r", labels=["b", "a"])))
    assert same_order.state is JudgeState.PASS
    extra = evaluate(task, case, judge_returning(as_json(reasoning="r", labels=["a", "b", "c"])))
    assert extra.state is JudgeState.FAIL


def test_extraction_compares_declared_fields() -> None:
    task = JudgeTask(
        metric="param_mapping_accuracy",
        kind=JudgeTaskKind.EXTRACTION,
        fields=["country", "product_id"],
    )
    case = JudgeCase(
        id="c1",
        expected={"param_mapping_accuracy": {"country": "KR", "product_id": "100"}},
    )
    good = evaluate(
        task, case, judge_returning(as_json(reasoning="r", fields={"country": "KR", "product_id": "100"}))
    )
    assert good.state is JudgeState.PASS
    bad = evaluate(
        task, case, judge_returning(as_json(reasoning="r", fields={"country": "US", "product_id": "100"}))
    )
    assert bad.state is JudgeState.FAIL


def test_extraction_without_declared_fields_compares_all() -> None:
    task = JudgeTask(metric="param_mapping_accuracy", kind=JudgeTaskKind.EXTRACTION)
    case = JudgeCase(id="c1", expected={"param_mapping_accuracy": {"a": "1"}})
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", fields={"a": "1"}))).state is JudgeState.PASS
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", fields={"a": "2"}))).state is JudgeState.FAIL


def test_score_threshold_folding() -> None:
    task = JudgeTask(metric="planning_path_score", kind=JudgeTaskKind.SCORE, threshold=0.5)
    below = evaluate(task, JudgeCase(id="c1"), judge_returning(as_json(reasoning="r", score=0.4)))
    assert below.state is JudgeState.FAIL
    above = evaluate(task, JudgeCase(id="c1"), judge_returning(as_json(reasoning="r", score=0.6)))
    assert above.state is JudgeState.PASS


def test_preference_choice() -> None:
    task = JudgeTask(metric="user_satisfaction", kind=JudgeTaskKind.PREFERENCE)
    case = JudgeCase(id="c1", expected={"user_satisfaction": "b"})
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", choice="b"))).state is JudgeState.PASS
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", choice="a"))).state is JudgeState.FAIL


# --- 跳过语义 ---------------------------------------------------------------


def test_route_error_skips_rag_dependent_metric() -> None:
    task = JudgeTask(metric="faithfulness", kind=JudgeTaskKind.BINARY)
    case = JudgeCase(id="c1", evidence={"route_error": True})
    assert is_skipped(task, case)
    result = evaluate(task, case, judge_returning(as_json(reasoning="r", verdict="pass")))
    assert result.state is JudgeState.SKIPPED


def test_route_error_leaves_unrelated_metric_judged() -> None:
    task = JudgeTask(metric="task_completion", kind=JudgeTaskKind.BINARY)
    case = JudgeCase(id="c1", evidence={"route_error": True}, expected={"task_completion": "pass"})
    assert not is_skipped(task, case)
    assert evaluate(task, case, judge_returning(as_json(reasoning="r", verdict="pass"))).state is JudgeState.PASS


# --- 轨迹证据（§6.1 步骤 4d）------------------------------------------------


def _trace_with_signals(case_id: str = "c1", route: str = "skill_miss"):
    from datetime import datetime, timezone

    from agenteval.models import Trace
    from agenteval.modules import record_memory, record_perception, record_planning, record_retrieval
    from agenteval.process import record_tool_call
    from agenteval.tools import ToolResult

    trace = Trace(case_id=case_id)
    record_perception(trace, intent="knowledge_qa", skill=None, hit=False, duration_ms=12)
    record_planning(trace, route=route, tools=["repo_vector_search"], duration_ms=4)
    record_memory(trace, turns=2, injected=["商品 100"], duration_ms=3)
    record_retrieval(trace, chunks=[{"id": "chunk-1"}], duration_ms=30)
    record_tool_call(trace, "repo_vector_search", {"q": "x"}, ToolResult(ok=True, value={"hits": 1}))
    return trace


def test_evidence_from_trace_extracts_module_signals() -> None:
    evidence = evidence_from_trace(_trace_with_signals())
    assert evidence["intent"] == "knowledge_qa"
    assert evidence["skill_hit"] is False
    assert evidence["route"] == "skill_miss"
    assert evidence["planned_tools"] == ["repo_vector_search"]
    assert evidence["tools"] == ["repo_vector_search"]
    assert evidence["session_turns"] == 2
    assert evidence["chunks"] == [{"id": "chunk-1"}]
    assert evidence["durations_ms"]["perception"] == 12
    assert "route_error" not in evidence


def test_evidence_from_trace_flags_route_error() -> None:
    evidence = evidence_from_trace(_trace_with_signals(route="skill_hit"), expected_route="skill_miss")
    assert evidence["route_error"] is True


def test_judge_task_run_attaches_trace_evidence(tmp_path: Path, capsys) -> None:
    """judge task --run 应把轨迹证据带上：路由错误 → 依赖检索的指标自动跳过。"""

    from agenteval.models import Run, Status, Verdict
    from agenteval.store import RunStore

    store = RunStore(tmp_path / "runs")
    trace = _trace_with_signals(case_id="c1", route="skill_hit")
    run = Run(run_id="src-run", started_at=datetime.now(timezone.utc))
    run.verdicts = [Verdict(case_id="c1", status=Status.PASS)]
    run.traces = [trace]
    run.refresh_summary()
    store.save(run)

    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "c1",
                        "metrics": ["faithfulness"],
                        "expected_route": "skill_miss",
                        "output": "回答",
                        "expected": {"faithfulness": "pass"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "--home",
            str(tmp_path),
            "judge",
            "task",
            "--cases",
            str(cases),
            "--run",
            "src-run",
            "--judge",
            "examples/demo_judge_tasks.py:build_keyword_judge",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "faithfulness=skipped" in out


def test_judge_case_from_uses_case_and_verdict() -> None:
    from agenteval.judge_tasks import judge_case_from
    from agenteval.models import TaskCase, Verdict, Status

    case = TaskCase(
        id="t",
        user_input="帮我看下订单",
        expected_output="已处理",
        expected={"task_completion": "pass"},
        checks=[{"kind": "no_extra_calls", "allowed": ["echo_tool"], "metric": "task_completion"}],
    )
    verdict = Verdict(case_id="t", status=Status.PASS, output="已处理完毕")
    judge_case = judge_case_from(case, verdict)

    assert judge_case.input == "帮我看下订单"
    assert judge_case.output == "已处理完毕"
    assert judge_case.reference == "已处理"
    assert judge_case.metrics == ["task_completion"]
    assert judge_case.expected == {"task_completion": "pass"}


def test_judge_case_from_attaches_trace_evidence() -> None:
    from agenteval.judge_tasks import judge_case_from
    from agenteval.models import Status, TaskCase, Verdict

    case = TaskCase(id="c1", expected_route="skill_miss")
    verdict = Verdict(case_id="c1", status=Status.PASS, output="回答")
    judge_case = judge_case_from(case, verdict, _trace_with_signals(case_id="c1", route="skill_hit"))

    assert judge_case.evidence["route"] == "skill_hit"
    assert judge_case.evidence["route_error"] is True


# --- 用例与运行 -------------------------------------------------------------


def test_evaluate_case_status_and_skipped_excluded_from_failures() -> None:
    tasks = build_tasks(["task_completion", "faithfulness"])
    case = JudgeCase(
        id="c1",
        metrics=["task_completion", "faithfulness"],
        evidence={"route_error": True},
        expected={"task_completion": "pass", "faithfulness": "pass"},
    )
    verdict = evaluate_case(case, tasks, judge_returning(as_json(reasoning="r", verdict="pass")))
    assert verdict.status is Status.PASS
    skipped = [check for check in verdict.checks if check.skipped]
    assert len(skipped) == 1 and skipped[0].metric == "faithfulness"
    # 跳过不进失败断言列表
    assert all(not check.skipped for check in verdict.failed_checks)


def test_evaluate_case_marks_error_when_task_errors() -> None:
    tasks = build_tasks(["task_completion"])
    case = JudgeCase(id="c1", metrics=["task_completion"])
    verdict = evaluate_case(case, tasks, judge_returning("not json"))
    assert verdict.status is Status.ERROR
    assert verdict.error


def test_run_tasks_persists_run_with_scope(tmp_path: Path) -> None:
    tasks = build_tasks(["task_completion"])
    case = JudgeCase(
        id="c1",
        dataset_type="knowledge_qa",
        metrics=["task_completion"],
        expected={"task_completion": "pass"},
    )
    store = RunStore(tmp_path / "runs")
    # 直接调用 run_tasks 不入库时也需要 scope 由调用方给出，这里验证入库与汇总
    run = run_tasks(
        [case],
        tasks,
        judge_returning(as_json(reasoning="r", verdict="pass")),
        store=store,
        metadata={"scope": "knowledge_qa"},
    )
    assert run.metadata["scope"] == "knowledge_qa"
    assert store.load(run.run_id).run_id == run.run_id
    assert run.verdicts[0].status is Status.PASS


# --- 载入 -------------------------------------------------------------------


def test_load_cases_and_task_specs(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [{"id": "c1", "metrics": ["task_completion"]}]}),
        encoding="utf-8",
    )
    assert load_cases(cases_path)[0].id == "c1"

    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(
        json.dumps({"tasks": [{"metric": "route_accuracy", "kind": "classification", "labels": ["a", "b"]}]}),
        encoding="utf-8",
    )
    tasks = load_task_specs(tasks_path)
    assert tasks[0].labels == ["a", "b"]


def test_load_empty_cases_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


# --- CLI --------------------------------------------------------------------


def test_cli_judge_task_detects_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    code = main(
        [
            "--home",
            str(home),
            "judge",
            "task",
            "--cases",
            str(EXAMPLES / "judge_task_cases.json"),
            "--tasks",
            str(EXAMPLES / "judge_tasks.json"),
            "--judge",
            "examples/demo_judge_tasks.py:build_keyword_judge",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "faithfulness=skipped" in out
    assert (home / "runs").is_dir()


def test_cli_judge_task_passes_clean_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "ok",
                        "dataset_type": "knowledge_qa",
                        "metrics": ["task_completion"],
                        "output": "该商品在韩国不可售。",
                        "expected": {"task_completion": "pass"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "--home",
            str(home),
            "judge",
            "task",
            "--cases",
            str(cases),
            "--judge",
            "examples/demo_judge_tasks.py:build_keyword_judge",
        ]
    )
    capsys.readouterr()
    assert code == 0


def test_cli_judge_task_without_metrics_is_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"cases": [{"id": "c1"}]}), encoding="utf-8")
    code = main(
        [
            "--home",
            str(home),
            "judge",
            "task",
            "--cases",
            str(cases),
            "--judge",
            "examples/demo_judge_tasks.py:build_keyword_judge",
        ]
    )
    capsys.readouterr()
    assert code == 2
