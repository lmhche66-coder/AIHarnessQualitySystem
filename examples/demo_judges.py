"""示例裁判，用于演示校准能识别出哪些偏置。

这三个都不是真实裁判，只用来验证校准机器本身：一个与人工标注一致，另外两个
分别带有位置偏置与长度偏置。
"""

from __future__ import annotations

from collections.abc import Callable

Judge = Callable[[str, str, str], str]

MARKER = "根因"


def build_keyword_judge() -> Judge:
    """按关键词判断，与示例金标集的人工标注一致。"""

    def judge(prompt: str, response_a: str, response_b: str) -> str:
        in_a = MARKER in response_a
        in_b = MARKER in response_b
        if in_a == in_b:
            return "tie"
        return "a" if in_a else "b"

    return judge


def build_position_biased_judge() -> Judge:
    """永远选第一个候选，用于暴露位置偏置。"""

    def judge(prompt: str, response_a: str, response_b: str) -> str:
        return "a"

    return judge


def build_length_biased_judge() -> Judge:
    """永远选更长的候选，用于暴露长度偏置。"""

    def judge(prompt: str, response_a: str, response_b: str) -> str:
        if len(response_a) == len(response_b):
            return "tie"
        return "a" if len(response_a) > len(response_b) else "b"

    return judge
