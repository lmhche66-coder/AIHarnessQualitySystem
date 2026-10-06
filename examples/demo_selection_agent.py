"""示例选择 agent：按用例请求返回结构化工具调用。

它演示 ``selection run --agent`` 的接入形状——接入方负责把模型的自由输出
解析成结构化调用，平台只负责打分。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_agent() -> Callable[[Any], list[dict[str, Any]]]:
    def choose(case: Any) -> list[dict[str, Any]]:
        if case.id == "single-correct":
            return [{"name": "get_weather", "arguments": {"city": "北京"}}]
        if case.id == "parallel-complete":
            return [
                {"name": "get_weather", "arguments": {"city": "北京"}},
                {"name": "get_weather", "arguments": {"city": "上海"}},
            ]
        return []

    return choose


def build_wrong_agent() -> Callable[[Any], list[dict[str, Any]]]:
    """选错工具的 agent，用于演示打分能把它拦下来。"""

    def choose(case: Any) -> list[dict[str, Any]]:
        if case.id == "single-correct":
            return [{"name": "send_email", "arguments": {"to": "someone@example.com"}}]
        return []

    return choose
