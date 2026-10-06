"""示例逐轮 agent：先问清时间再动手，并以确认收尾。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agenteval.dialogue import Conversation, Role


def _agent_turns(conversation: Conversation) -> list[Any]:
    return [turn for turn in conversation.turns if turn.role is Role.AGENT]


def build_agent() -> Callable[[Any, Conversation], str]:
    """先问后做。"""

    def respond(registry: Any, conversation: Conversation) -> str:
        if not _agent_turns(conversation):
            return "请先告诉我故障发生的时间范围。"
        registry.get("echo_tool").invoke(message="EEG sample loss")
        return "已确认：电极接触阻抗偏高。"

    return respond


def build_hasty_agent() -> Callable[[Any, Conversation], str]:
    """一上来就动手，不先问清信息。"""

    def respond(registry: Any, conversation: Conversation) -> str:
        if not _agent_turns(conversation):
            registry.get("echo_tool").invoke(message="EEG sample loss")
            return "已确认：电极接触阻抗偏高。"
        return "已确认：如上。"

    return respond
