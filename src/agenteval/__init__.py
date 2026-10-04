"""agenteval: 面向 agent 项目的评测平台。

当前版本聚焦第 1 层能力：工具调用契约的确定性验证。这一层不调用 LLM，
不依赖真实外部服务，因此同一用例重复执行必须得到相同结论。
"""

from agenteval.models import (
    AnyCase,
    Case,
    CheckOutcome,
    ProcessCase,
    Run,
    RunSummary,
    Status,
    Trace,
    TraceEvent,
    Verdict,
)
from agenteval.tools import (
    SchemaTool,
    Tool,
    ToolError,
    ToolErrorKind,
    ToolRegistry,
    ToolResult,
)

__version__ = "0.1.0"

__all__ = [
    "Case",
    "CheckOutcome",
    "AnyCase",
    "ProcessCase",
    "Run",
    "RunSummary",
    "SchemaTool",
    "Status",
    "Tool",
    "ToolError",
    "ToolErrorKind",
    "ToolRegistry",
    "ToolResult",
    "Trace",
    "TraceEvent",
    "Verdict",
    "__version__",
]
