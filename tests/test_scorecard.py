from __future__ import annotations

from agenteval.models import CaseMetrics, CheckOutcome, Run, Status, Verdict
from agenteval.scorecard import EVAL_MODE_MOCK, build_scorecard, format_scorecard


def make_run(run_id: str, verdicts: list[Verdict], metadata: dict | None = None) -> Run:
    run = Run(run_id=run_id, started_at="2026-01-01T00:00:00Z", metadata=metadata or {})
    run.verdicts = verdicts
    run.refresh_summary()
    return run


def test_primary_metric_decides_pass_not_and_of_all_checks() -> None:
    # 文章 5.3：内容正确但格式不合规，不应因次要指标判负。
    verdict = Verdict(
        case_id="kqa-1",
        status=Status.FAIL,
        scene="知识问答",
        dataset_type="knowledge_qa",
        checks=[
            CheckOutcome(name="task_completion", passed=True, metric="task_completion"),
            CheckOutcome(
                name="instruction_following", passed=False, metric="instruction_following"
            ),
        ],
    )
    card = build_scorecard(make_run("r1", [verdict]), scope="knowledge_qa")

    assert card.primary_metric == "task_completion"
    assert card.primary_pass_rate == 1.0
    assert card.pass_rate == 0.0  # 原始判定口径仍如实保留
    assert card.cases[0].primary_state == "pass"


def test_skipped_downstream_metric_is_excluded_from_denominator() -> None:
    # 文章 5.4：路由误触发时，依赖检索证据的下游指标跳过，不计入分子也不计入分母。
    verdict = Verdict(
        case_id="kqa-2",
        status=Status.FAIL,
        scene="知识问答",
        dataset_type="knowledge_qa",
        checks=[
            CheckOutcome(name="route_accuracy", passed=False, metric="route_accuracy"),
            CheckOutcome(
                name="faithfulness",
                passed=False,
                skipped=True,
                metric="faithfulness",
            ),
            CheckOutcome(
                name="long_term_retrieval_recall",
                passed=False,
                skipped=True,
                metric="long_term_retrieval_recall",
            ),
        ],
    )
    card = build_scorecard(make_run("r2", [verdict]), scope="knowledge_qa")
    by_metric = {stat.metric: stat for stat in card.quality}

    assert by_metric["route_accuracy"].failed == 1
    assert by_metric["faithfulness"].skipped == 1
    assert by_metric["faithfulness"].failed == 0
    assert by_metric["long_term_retrieval_recall"].skipped == 1
    assert verdict.failed_checks and verdict.failed_checks[0].name == "route_accuracy"


def test_scene_pass_rates_are_sorted_by_primary_metric() -> None:
    verdicts = [
        Verdict(
            case_id="a",
            status=Status.PASS,
            scene="Trace 排查",
            dataset_type="basic_function",
            checks=[CheckOutcome(name="task_completion", passed=True, metric="task_completion")],
        ),
        Verdict(
            case_id="b",
            status=Status.FAIL,
            scene="错误码分析",
            dataset_type="basic_function",
            checks=[CheckOutcome(name="task_completion", passed=False, metric="task_completion")],
        ),
        Verdict(
            case_id="c",
            status=Status.ERROR,
            scene="错误码分析",
            dataset_type="basic_function",
            error="timeout",
        ),
    ]
    card = build_scorecard(make_run("r3", verdicts), scope="basic_function")
    scenes = {scene.scene: scene for scene in card.scenes}

    assert scenes["Trace 排查"].pass_rate == 1.0
    # 执行错误从分母剔除，因此该场景只剩一条失败用例
    assert scenes["错误码分析"].pass_rate == 0.0
    assert scenes["错误码分析"].errored == 1
    assert card.scenes[0].scene == "Trace 排查"


def test_cost_and_performance_come_from_case_metrics() -> None:
    verdict = Verdict(
        case_id="a",
        status=Status.PASS,
        checks=[CheckOutcome(name="task_completion", passed=True, metric="task_completion")],
        metrics=CaseMetrics(calls=3, retries=1, duration_ms=120.0, input_tokens=10, output_tokens=5),
    )
    card = build_scorecard(make_run("r4", [verdict]), scope="end_to_end")

    assert card.cost.tool_calls == 3
    assert card.cost.retries == 1
    assert card.cost.input_tokens == 10
    assert card.cost.output_tokens == 5
    assert card.performance.total_duration_ms == 120.0
    assert card.performance.duration_p50_ms == 120.0


def test_module_latencies_land_in_the_scorecard() -> None:
    # 文章 §6.3：各节点耗时落到延迟指标上，而不是只声明不落数
    from datetime import datetime, timezone

    from agenteval.models import Trace
    from agenteval.modules import record_memory, record_perception, record_planning, record_retrieval

    trace = Trace(case_id="a")
    record_perception(trace, intent="knowledge_qa", duration_ms=12.0)
    record_planning(trace, route="skill_miss", duration_ms=4.0)
    record_memory(trace, turns=2, duration_ms=3.0)
    record_retrieval(trace, chunks=[{"id": "c1"}], duration_ms=9.0)

    run = make_run(
        "r6",
        [Verdict(case_id="a", status=Status.PASS, duration_ms=120.0)],
    )
    run.traces = [trace]
    card = build_scorecard(run, scope="knowledge_qa")
    by_metric = {stat.metric: stat for stat in card.latencies}

    assert by_metric["e2e_latency"].p50_ms == 120.0
    assert by_metric["intent_latency"].p50_ms == 12.0
    assert by_metric["planning_latency"].p50_ms == 4.0
    assert by_metric["memory_injection_latency"].p50_ms == 3.0
    assert by_metric["retrieval_latency"].p50_ms == 9.0
    # 没有数据的指标不出现在结果里，不臆造零值
    assert "tool_latency" not in by_metric


def test_tool_latency_comes_from_platform_measurement() -> None:
    """工具调用耗时由平台自动测量，agent 不需要上报工具延迟。"""

    from agenteval.models import Trace
    from agenteval.process import record_tool_call
    from agenteval.tools import ToolResult

    trace = Trace(case_id="a")
    record_tool_call(trace, "echo_tool", {"message": "hi"}, ToolResult(ok=True, value={}), 7.5)
    run = make_run("r8", [Verdict(case_id="a", status=Status.PASS, duration_ms=20.0)])
    run.traces = [trace]
    card = build_scorecard(run, scope="tool_call")
    by_metric = {stat.metric: stat for stat in card.latencies}

    assert by_metric["tool_latency"].samples == 1
    assert by_metric["tool_latency"].p50_ms == 7.5


def test_model_calls_surface_in_cost() -> None:
    verdict = Verdict(
        case_id="a",
        status=Status.PASS,
        metrics=CaseMetrics(model_calls=4, input_tokens=10, output_tokens=5, usage_reported=True),
    )
    card = build_scorecard(make_run("r7", [verdict]), scope="end_to_end")
    assert card.cost.model_calls == 4


def test_eval_mode_is_mock_when_cassette_is_replayed() -> None:
    run = make_run(
        "r5",
        [Verdict(case_id="a", status=Status.PASS)],
        metadata={"cassette": {"name": "demo", "mode": "replay", "interactions": 1}},
    )
    card = build_scorecard(run, scope="end_to_end")
    assert card.eval_mode == EVAL_MODE_MOCK


def test_unannotated_run_falls_back_to_verdict_status() -> None:
    run = make_run(
        "r6",
        [
            Verdict(case_id="a", status=Status.PASS, checks=[]),
            Verdict(case_id="b", status=Status.FAIL, checks=[]),
        ],
    )
    card = build_scorecard(run)
    assert card.primary_metric == "task_completion"
    assert card.primary_pass_rate == 0.5
    assert "primary_pass_rate" in format_scorecard(card)
