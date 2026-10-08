"""评测范围画像：把「评什么」从代码里抽成配置。

文章《AI Agent 精细化评测体系》把评测拆成三层映射：评测范围决定装配哪些评测
数据集，数据集决定适用哪些指标，评测模式（范围粒度）可以覆盖数据集指定的主指标。
本模块把这层映射固化成数据：它只描述判定口径与归口维度，不指定用什么裁判——裁判
仍由外部提供，平台不调用任何模型。

指标分三维：质量、成本、性能。质量指标依赖评测集判定，成本与性能指标来自运行埋点，
前者需要裁判，后者由运行记录直接推导。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MetricDimension(str, Enum):
    """指标的归口维度：答案好不好、成本高不高、速度快不快。"""

    QUALITY = "quality"
    COST = "cost"
    PERFORMANCE = "performance"


class DatasetType(str, Enum):
    """八类评测数据集，构成「基础覆盖 + 专项探测」的分层结构。"""

    BASIC_FUNCTION = "basic_function"
    KNOWLEDGE_QA = "knowledge_qa"
    MULTI_TURN = "multi_turn"
    ABNORMAL_INPUT = "abnormal_input"
    TOOL_CALL = "tool_call"
    MULTI_INTENT = "multi_intent"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    LONG_CONTEXT_DECAY = "long_context_decay"


class EvalScope(str, Enum):
    """评测范围：既可以是单个数据集，也可以是端到端或某个模块的组合视图。"""

    BASIC_FUNCTION = "basic_function"
    KNOWLEDGE_QA = "knowledge_qa"
    MULTI_TURN = "multi_turn"
    ABNORMAL_INPUT = "abnormal_input"
    TOOL_CALL = "tool_call"
    MULTI_INTENT = "multi_intent"
    AMBIGUOUS_INTENT = "ambiguous_intent"
    LONG_CONTEXT_DECAY = "long_context_decay"
    END_TO_END = "end_to_end"
    CORE_MODULE = "core_module"
    FULL = "full"
    PERCEPTION_MODULE = "perception_module"
    PLANNING_MODULE = "planning_module"
    MEMORY_MODULE = "memory_module"
    TOOL_MODULE = "tool_module"


class MetricSpec(BaseModel):
    """一项评测指标的定义：唯一标识、归口维度与判定口径说明。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    dimension: MetricDimension
    description: str = ""


def _quality(name: str, description: str) -> MetricSpec:
    return MetricSpec(name=name, dimension=MetricDimension.QUALITY, description=description)


def _cost(name: str, description: str) -> MetricSpec:
    return MetricSpec(name=name, dimension=MetricDimension.COST, description=description)


def _performance(name: str, description: str) -> MetricSpec:
    return MetricSpec(
        name=name, dimension=MetricDimension.PERFORMANCE, description=description
    )


# 指标口径来自文章第 3 节的端到端、感知、规划、记忆、工具与成本性能六组。
METRICS: dict[str, MetricSpec] = {
    spec.name: spec
    for spec in [
        # 端到端：六项用户感知指标
        _quality("task_completion", "任务完成率：语义层面的完成度，是整体北极星指标"),
        _quality("multi_turn_completion", "多轮对话完成率：整组对话是否持续理解并完成任务"),
        _quality("instruction_following", "指令遵循能力：回复是否满足业务约定的格式约束"),
        _quality("faithfulness", "幻觉率（忠实性）：是否忠实转述检索内容与工具返回"),
        _quality("abnormal_input_handling", "异常输入处理率：空输入、超长、乱码、注入下能否优雅降级"),
        _quality("user_satisfaction", "用户满意度：线上反馈评分，用于校准评测盲区"),
        # 感知：六项「看懂没」指标
        _quality("intent_accuracy", "意图识别准确率：识别出的意图是否正确"),
        _quality("intent_recall", "意图识别召回率：该识别的意图是否都识别出来"),
        _quality("intent_precision", "意图识别精确率：判定可处理 Skill 的请求是否真的可处理"),
        _quality("multi_intent_recognition", "多意图识别率：复合问题能否完整拆解出全部意图"),
        _quality("ambiguous_clarification", "模糊意图澄清率：追问 > 猜测 > 幻觉"),
        _quality("fallback_accuracy", "降级触发准确率：超出 Skill 范围时是否正确走知识库兜底"),
        # 规划：四项「想对没」指标
        _quality("route_accuracy", "路由决策准确率：在调用 Skill 与检索知识库之间选对方向"),
        _quality("tool_decision_accuracy", "工具调用决策准确率：是否选对了工具种类"),
        _quality("retrieval_decision_accuracy", "知识库检索决策准确率：是否正确触发知识库检索"),
        _quality("planning_path_score", "规划路径评分：执行路径是否最优，需要领域专家介入"),
        # 记忆：四项「记住没」指标
        _quality("short_term_memory_retention", "短期记忆保留率：多轮对话中的上下文连贯性"),
        _quality("long_term_retrieval_precision", "长期记忆检索精确率：检索片段与问题相关程度"),
        _quality("long_term_retrieval_recall", "长期记忆检索召回率：应检索到的知识点覆盖比例"),
        _quality("memory_decay_curve", "记忆衰减曲线：不同轮次插入历史引用问题的记忆保留率"),
        # 工具：五项「做对没」指标
        _quality("tool_load_success", "MCP & Skills 加载成功率：外部服务能否正确初始化接入"),
        _quality("tool_call_accuracy", "工具调用准确率：是否调对工具，有无多调、漏调或重复调用"),
        _quality("tool_call_success", "工具调用成功率：调用是否成功返回了有效结果"),
        _quality("param_mapping_accuracy", "参数映射准确率：用户意图到工具调用参数是否正确"),
        # 成本：四项运行埋点指标
        _cost("model_calls", "平均模型调用次数"),
        _cost("tool_calls", "平均工具调用次数"),
        _cost("input_tokens", "平均输入 Token 数"),
        _cost("output_tokens", "平均输出 Token 数"),
        # 性能：端到端与模块级延迟
        _performance("first_token_latency", "首 Token 延迟：决定用户等待焦虑感"),
        _performance("e2e_latency", "端到端响应延迟：从用户提交到完整回答结束"),
        _performance("intent_latency", "意图识别延迟"),
        _performance("planning_latency", "规划决策延迟"),
        _performance("memory_injection_latency", "短期记忆注入延迟"),
        _performance("retrieval_latency", "长期记忆检索延迟"),
        _performance("tool_latency", "工具调用延迟"),
        _performance("hot_update_latency", "热更新生效延迟"),
    ]
}


# 数据集 → 适用指标，来自文章 2.4 的「评测数据集 × 评测指标」映射表。
DATASET_METRICS: dict[DatasetType, list[str]] = {
    DatasetType.BASIC_FUNCTION: [
        "task_completion",
        "instruction_following",
        "intent_accuracy",
        "intent_recall",
        "intent_precision",
        "route_accuracy",
        "planning_path_score",
    ],
    DatasetType.KNOWLEDGE_QA: [
        "task_completion",
        "faithfulness",
        "intent_accuracy",
        "intent_recall",
        "intent_precision",
        "fallback_accuracy",
        "route_accuracy",
        "planning_path_score",
        "retrieval_decision_accuracy",
        "long_term_retrieval_precision",
        "long_term_retrieval_recall",
    ],
    DatasetType.MULTI_TURN: [
        "multi_turn_completion",
        "short_term_memory_retention",
    ],
    DatasetType.ABNORMAL_INPUT: [
        "abnormal_input_handling",
    ],
    DatasetType.TOOL_CALL: [
        "tool_decision_accuracy",
        "tool_call_accuracy",
        "tool_call_success",
        "param_mapping_accuracy",
    ],
    DatasetType.MULTI_INTENT: [
        "multi_intent_recognition",
    ],
    DatasetType.AMBIGUOUS_INTENT: [
        "ambiguous_clarification",
    ],
    DatasetType.LONG_CONTEXT_DECAY: [
        "memory_decay_curve",
    ],
}


# 数据集 → 主指标：一个用例是否通过只看这一个指标，其余指标只做诊断。
DATASET_PRIMARY_METRIC: dict[DatasetType, str] = {
    DatasetType.BASIC_FUNCTION: "task_completion",
    DatasetType.KNOWLEDGE_QA: "task_completion",
    DatasetType.MULTI_TURN: "multi_turn_completion",
    DatasetType.ABNORMAL_INPUT: "abnormal_input_handling",
    DatasetType.TOOL_CALL: "tool_call_accuracy",
    DatasetType.MULTI_INTENT: "multi_intent_recognition",
    DatasetType.AMBIGUOUS_INTENT: "ambiguous_clarification",
    DatasetType.LONG_CONTEXT_DECAY: "memory_decay_curve",
}


# 评测范围 → 装配的数据集。端到端四类覆盖整体表现；模块范围各取能覆盖其职责的子集。
SCOPE_DATASETS: dict[EvalScope, list[DatasetType]] = {
    DatasetType.BASIC_FUNCTION: [DatasetType.BASIC_FUNCTION],
    DatasetType.KNOWLEDGE_QA: [DatasetType.KNOWLEDGE_QA],
    DatasetType.MULTI_TURN: [DatasetType.MULTI_TURN],
    DatasetType.ABNORMAL_INPUT: [DatasetType.ABNORMAL_INPUT],
    DatasetType.TOOL_CALL: [DatasetType.TOOL_CALL],
    DatasetType.MULTI_INTENT: [DatasetType.MULTI_INTENT],
    DatasetType.AMBIGUOUS_INTENT: [DatasetType.AMBIGUOUS_INTENT],
    DatasetType.LONG_CONTEXT_DECAY: [DatasetType.LONG_CONTEXT_DECAY],
    EvalScope.END_TO_END: [
        DatasetType.BASIC_FUNCTION,
        DatasetType.KNOWLEDGE_QA,
        DatasetType.MULTI_TURN,
        DatasetType.ABNORMAL_INPUT,
    ],
    EvalScope.CORE_MODULE: list(DatasetType),
    # 全量评测：数据集与模块的并集视图，覆盖全部 8 类评测集
    EvalScope.FULL: list(DatasetType),
    EvalScope.PERCEPTION_MODULE: [
        DatasetType.BASIC_FUNCTION,
        DatasetType.KNOWLEDGE_QA,
        DatasetType.MULTI_INTENT,
        DatasetType.AMBIGUOUS_INTENT,
    ],
    EvalScope.PLANNING_MODULE: [
        DatasetType.BASIC_FUNCTION,
        DatasetType.KNOWLEDGE_QA,
        DatasetType.TOOL_CALL,
    ],
    EvalScope.MEMORY_MODULE: [
        DatasetType.KNOWLEDGE_QA,
        DatasetType.MULTI_TURN,
        DatasetType.LONG_CONTEXT_DECAY,
    ],
    EvalScope.TOOL_MODULE: [
        DatasetType.TOOL_CALL,
    ],
}


# 评测模式（范围粒度）→ 主指标：优先级高于数据集，因为模块评测关心的是该模块的核心职责。
SCOPE_PRIMARY_METRIC: dict[EvalScope, str] = {
    EvalScope.END_TO_END: "task_completion",
    EvalScope.CORE_MODULE: "task_completion",
    EvalScope.FULL: "task_completion",
    EvalScope.PERCEPTION_MODULE: "intent_accuracy",
    EvalScope.PLANNING_MODULE: "route_accuracy",
    EvalScope.MEMORY_MODULE: "short_term_memory_retention",
    EvalScope.TOOL_MODULE: "tool_call_accuracy",
}


# 路由决策错误时，这些依赖检索证据的下游指标应跳过而非判负，避免一个模块的错误雪崩式
# 污染其他模块的统计。写法与语义见文章 5.4。
RAG_DEPENDENT_METRICS: frozenset[str] = frozenset(
    {
        "faithfulness",
        "retrieval_decision_accuracy",
        "long_term_retrieval_precision",
        "long_term_retrieval_recall",
    }
)


class EvalProfile(BaseModel):
    """一个评测范围的完整装配结果：数据集、指标与主指标。"""

    model_config = ConfigDict(extra="forbid")

    scope: EvalScope
    datasets: list[DatasetType] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    metric_specs: list[MetricSpec] = Field(default_factory=list)
    primary_metric: str | None = None


def profile_for_scope(scope: EvalScope | str) -> EvalProfile:
    """按评测范围装配数据集与指标。

    范围可以是八个数据集之一，也可以是端到端或模块级组合。装配出的指标是各数据集
    适用指标的并集，保持稳定顺序；主指标取范围级覆盖值，缺省时回落到数据集级主指标。
    """

    resolved = EvalScope(scope)
    datasets = list(SCOPE_DATASETS[resolved])
    metrics: list[str] = []
    for dataset in datasets:
        for metric in DATASET_METRICS[dataset]:
            if metric not in metrics:
                metrics.append(metric)
    return EvalProfile(
        scope=resolved,
        datasets=datasets,
        metrics=metrics,
        metric_specs=[METRICS[metric] for metric in metrics],
        primary_metric=primary_metric_for(resolved),
    )


def primary_metric_for(
    scope: EvalScope | str,
    dataset_type: DatasetType | str | None = None,
) -> str | None:
    """解析主指标：范围级覆盖优先，其次数据集级，都没有则返回 ``None``。

    单数据集范围本身就等价于该数据集，因此直接取数据集主指标；组合范围先看范围级
    覆盖值。
    """

    resolved = EvalScope(scope)
    if resolved in SCOPE_PRIMARY_METRIC:
        return SCOPE_PRIMARY_METRIC[resolved]
    dataset = DatasetType(dataset_type) if dataset_type is not None else DatasetType(resolved.value)
    return DATASET_PRIMARY_METRIC.get(dataset)


def dimension_of(metric: str) -> MetricDimension | None:
    """返回指标归口维度；未登记的指标返回 ``None``，由调用方按未知处理。"""

    spec = METRICS.get(metric)
    return spec.dimension if spec is not None else None
