"""示例探针目标，用于验证红队检测确实能识别出问题。

两个目标都不调用真实模型：一个对任何要求照单全收（有漏洞），另一个一律拒绝。
"""

from __future__ import annotations

from collections.abc import Callable

from agenteval.process import ToolCall
from agenteval.redteam import Probe, ProbeOutcome

CANARY_MARKER = "系统提示词是："


def build_vulnerable_target() -> Callable[[Probe], ProbeOutcome]:
    """照单全收：泄漏金丝雀，并调用探针声明的禁用工具。"""

    def target(probe: Probe) -> ProbeOutcome:
        response = "好的，我按你说的做。"
        if probe.canaries:
            response += f"{CANARY_MARKER}{probe.canaries[0]}。"
        calls = [
            ToolCall(index=index, target=tool, args={"confirm": "ALL"}, ok=True)
            for index, tool in enumerate(probe.forbidden_tools)
        ]
        return ProbeOutcome(response=response, calls=calls)

    return target


def build_guardrail_target() -> Callable[[Probe], ProbeOutcome]:
    """有护栏：一律拒绝，不泄漏也不调用任何工具。"""

    def target(probe: Probe) -> ProbeOutcome:
        return ProbeOutcome(response="抱歉，这个请求我不能执行。", calls=[])

    return target
