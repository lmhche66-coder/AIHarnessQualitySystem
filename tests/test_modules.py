from __future__ import annotations

from agenteval.bridge import BridgeResponse
from agenteval.models import (
    CaseMetrics,
    CheckOutcome,
    ProcessCase,
    Run,
    Status,
    Trace,
    Verdict,
)
from agenteval.modules import (
    METRIC_MODULE,
    ModuleName,
    extract_signals,
    latest_signal,
    module_of_metric,
    record_memory,
    record_perception,
    record_planning,
    record_retrieval,
)
from agenteval.process import apply_upstream_skip, check_process_case
from agenteval.scorecard import build_scorecard


def trace_with_perception(**kwargs: object) -> Trace:
    trace = Trace(case_id="c")
    record_perception(trace, **kwargs)  # type: ignore[arg-type]
    return trace


def process_case(*checks: dict) -> ProcessCase:
    return ProcessCase.model_validate({"id": "c", "checks": list(checks)})


def test_retrieval_signal_carries_count_and_usage_event() -> None:
    """对齐文章 §6.3：RAG 要有结果数量；模型消耗也要写进轨迹。"""

    from agenteval.models import Trace
    from agenteval.modules import record_retrieval, record_usage

    trace = Trace(case_id="c1")
    record_retrieval(trace, chunks=[{"id": "a"}, {"id": "b"}], duration_ms=9.0)
    record_usage(trace, model_calls=2, input_tokens=120, output_tokens=40)

    events = {event.name: event.payload for event in trace.events}
    assert events["module.retrieval"]["count"] == 2
    assert events["module.retrieval"]["duration_ms"] == 9.0
    assert events["model.usage"] == {
        "model_calls": 2,
        "input_tokens": 120,
        "output_tokens": 40,
    }


def test_record_and_extract_signals() -> None:
    trace = Trace(case_id="c")
    record_perception(trace, intent="trace_lookup", skill="trace-analyzer", duration_ms=12.0)
    record_planning(trace, route="skill_hit", tools=["trace_query"], duration_ms=3.0)
    record_memory(trace, turns=3, injected=["商品 1005007651467330"], duration_ms=2.0)
    record_retrieval(trace, chunks=[{"id": "chunk-1"}], duration_ms=8.0)

    assert len(extract_signals(trace)) == 4
    perception = latest_signal(trace, ModuleName.PERCEPTION)
    assert perception is not None
    assert perception.payload["intent"] == "trace_lookup"
    assert perception.duration_ms == 12.0
    assert latest_signal(trace, ModuleName.TOOL) is None


def test_metric_module_mapping() -> None:
    assert module_of_metric("intent_accuracy") is ModuleName.PERCEPTION
    assert module_of_metric("route_accuracy") is ModuleName.PLANNING
    assert module_of_metric("long_term_retrieval_recall") is ModuleName.MEMORY
    assert module_of_metric("tool_call_accuracy") is ModuleName.TOOL
    assert module_of_metric("not-a-metric") is None
    assert set(METRIC_MODULE.values()) == set(ModuleName) - {ModuleName.RETRIEVAL}


def test_intent_match_pass_and_fail() -> None:
    trace = trace_with_perception(intent="trace_lookup", skill="trace-analyzer")
    case = process_case(
        {"kind": "intent_match", "expected_intent": "trace_lookup", "metric": "intent_accuracy"}
    )
    verdict = check_process_case(case, trace)
    assert verdict.status is Status.PASS

    wrong = process_case(
        {"kind": "intent_match", "expected_intent": "knowledge_qa", "metric": "intent_accuracy"}
    )
    assert check_process_case(wrong, trace).status is Status.FAIL


def test_intent_match_accept_any() -> None:
    trace = trace_with_perception(intent="trace_lookup", skill="trace-analyzer")
    case = process_case(
        {
            "kind": "intent_match",
            "expected_intent": "knowledge_qa",
            "expected_skill": "trace-analyzer",
            "accept_any": True,
        }
    )
    assert check_process_case(case, trace).status is Status.PASS


def test_route_and_tool_decision() -> None:
    trace = Trace(case_id="c")
    record_planning(trace, route="skill_miss", tools=["repo_vector_search"])
    case = process_case(
        {
            "kind": "route_decision",
            "expected_route": "skill_miss",
            "expected_tools": ["repo_vector_search"],
            "metric": "route_accuracy",
        },
        {
            "kind": "tool_decision",
            "expected_tools": ["repo_vector_search"],
            "metric": "tool_decision_accuracy",
        },
    )
    assert check_process_case(case, trace).status is Status.PASS


def test_memory_retention() -> None:
    trace = Trace(case_id="c")
    record_memory(trace, turns=4, injected=["商品 1005007651467330 在韩国不可售"])
    case = process_case(
        {
            "kind": "memory_retention",
            "markers": ["1005007651467330"],
            "min_turns": 3,
            "metric": "short_term_memory_retention",
        }
    )
    assert check_process_case(case, trace).status is Status.PASS

    missing = process_case(
        {"kind": "memory_retention", "markers": ["另一个商品"], "metric": "short_term_memory_retention"}
    )
    assert check_process_case(missing, trace).status is Status.FAIL


def test_retrieval_hit() -> None:
    trace = Trace(case_id="c")
    record_retrieval(trace, chunks=[{"id": "chunk-1"}, {"id": "chunk-2"}])
    case = process_case(
        {
            "kind": "retrieval_hit",
            "expected_ids": ["chunk-2"],
            "min_chunks": 2,
            "metric": "long_term_retrieval_recall",
        }
    )
    assert check_process_case(case, trace).status is Status.PASS


def test_route_error_skips_downstream_metrics() -> None:
    # 文章 5.4：路由误触发时，依赖检索证据的下游指标跳过，不计入分子分母。
    trace = Trace(case_id="c")
    record_planning(trace, route="skill_hit", tools=["some_skill"])
    case = process_case(
        {"kind": "route_decision", "expected_route": "skill_miss", "metric": "route_accuracy"},
        {
            "kind": "retrieval_hit",
            "min_chunks": 1,
            "metric": "long_term_retrieval_recall",
        },
    )
    verdict = check_process_case(case, trace)
    by_name = {check.name: check for check in verdict.checks}

    assert by_name["route_decision"].passed is False
    assert by_name["retrieval_hit.signal"].skipped is True
    assert verdict.status is Status.FAIL  # 原始判定仍如实反映未通过
    assert [check.name for check in verdict.failed_checks] == ["route_decision"]


def test_apply_upstream_skip_ignores_unrelated_metrics() -> None:
    outcomes = [
        CheckOutcome(name="route_decision", passed=False, metric="route_accuracy"),
        CheckOutcome(name="intent_match", passed=False, metric="intent_accuracy"),
    ]
    apply_upstream_skip(outcomes)
    assert outcomes[1].skipped is False


def test_bridge_response_carries_signals() -> None:
    response = BridgeResponse.model_validate(
        {
            "output": "ok",
            "signals": [
                {"module": "perception", "payload": {"intent": "trace_lookup"}},
                {"module": "planning", "payload": {"route": "skill_hit"}, "duration_ms": 4.0},
            ],
        }
    )
    assert [signal.module for signal in response.signals] == [
        ModuleName.PERCEPTION,
        ModuleName.PLANNING,
    ]
    assert response.signals[1].duration_ms == 4.0


def test_scorecard_groups_metrics_by_module() -> None:
    verdict = Verdict(
        case_id="a",
        status=Status.FAIL,
        scene="Trace 排查",
        dataset_type="basic_function",
        checks=[
            CheckOutcome(name="intent_match", passed=True, metric="intent_accuracy"),
            CheckOutcome(name="route_decision", passed=False, metric="route_accuracy"),
        ],
        metrics=CaseMetrics(calls=1, duration_ms=5.0),
    )
    run = Run(run_id="r", started_at="2026-01-01T00:00:00Z")
    run.verdicts = [verdict]
    run.refresh_summary()

    card = build_scorecard(run, scope="perception_module")
    modules = {stat.module: stat for stat in card.modules}
    assert modules[ModuleName.PERCEPTION].pass_rate == 1.0
    assert modules[ModuleName.PLANNING].pass_rate == 0.0
