from __future__ import annotations

from agenteval.profiles import (
    DATASET_METRICS,
    DATASET_PRIMARY_METRIC,
    METRICS,
    SCOPE_PRIMARY_METRIC,
    DatasetType,
    EvalScope,
    dimension_of,
    primary_metric_for,
    profile_for_scope,
)


def test_end_to_end_scope_assembles_four_datasets() -> None:
    profile = profile_for_scope(EvalScope.END_TO_END)
    assert profile.datasets == [
        DatasetType.BASIC_FUNCTION,
        DatasetType.KNOWLEDGE_QA,
        DatasetType.MULTI_TURN,
        DatasetType.ABNORMAL_INPUT,
    ]
    assert profile.primary_metric == "task_completion"


def test_core_module_scope_covers_all_datasets() -> None:
    profile = profile_for_scope(EvalScope.CORE_MODULE)
    assert set(profile.datasets) == set(DatasetType)
    assert profile.primary_metric == "task_completion"


def test_full_scope_covers_every_dataset() -> None:
    # 文章 §6.2：15 种评测范围，含「全量评测」
    from agenteval.profiles import DatasetType

    assert len(list(EvalScope)) == 15
    profile = profile_for_scope(EvalScope.FULL)
    assert set(profile.datasets) == set(DatasetType)
    assert profile.primary_metric == "task_completion"


def test_module_scope_primary_overrides_dataset_primary() -> None:
    # 文章 5.3：评测模式的优先级高于评测数据集。
    assert primary_metric_for(EvalScope.PERCEPTION_MODULE) == "intent_accuracy"
    assert primary_metric_for(EvalScope.PLANNING_MODULE) == "route_accuracy"
    assert primary_metric_for(EvalScope.MEMORY_MODULE) == "short_term_memory_retention"
    assert primary_metric_for(EvalScope.TOOL_MODULE) == "tool_call_accuracy"


def test_dataset_scope_uses_dataset_primary() -> None:
    assert primary_metric_for(EvalScope.MULTI_TURN) == "multi_turn_completion"
    assert primary_metric_for("knowledge_qa") == "task_completion"


def test_every_dataset_metric_is_registered() -> None:
    for dataset, metrics in DATASET_METRICS.items():
        for metric in metrics:
            assert metric in METRICS, f"{dataset} references unregistered metric {metric}"


def test_every_primary_metric_is_applicable_to_its_dataset() -> None:
    for dataset, primary in DATASET_PRIMARY_METRIC.items():
        assert primary in DATASET_METRICS[dataset], f"{dataset} primary {primary} not applicable"
    for scope, primary in SCOPE_PRIMARY_METRIC.items():
        applicable = {metric for dataset in profile_for_scope(scope).datasets for metric in DATASET_METRICS[dataset]}
        assert primary in applicable, f"{scope} primary {primary} not applicable"


def test_dimension_lookup() -> None:
    assert dimension_of("task_completion").value == "quality"
    assert dimension_of("input_tokens").value == "cost"
    assert dimension_of("e2e_latency").value == "performance"
    assert dimension_of("not-a-metric") is None
