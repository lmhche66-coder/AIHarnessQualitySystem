"""单条用例的输入上下文：sessionId 与 Mock 数据。

文章 §6.1 步骤 4a 要求「构造输入：注入 sessionId、Mock 数据，模拟真实用户会话
环境」。平台在执行一条用例时把这两个信息放进一个上下文变量，bridge 在拼请求时
自动带上，因此无论任务还是多轮对话、无论进程内还是跨进程，agent 都能拿到同一个
会话标识与预设的 Mock 数据。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

_CURRENT: ContextVar[dict[str, Any]] = ContextVar("agenteval_case_context", default={})


def current_case_context() -> dict[str, Any]:
    """返回当前用例的输入上下文；没有用例在执行时返回空字典。"""

    return dict(_CURRENT.get())


@contextmanager
def case_context(
    *,
    session_id: str,
    mock: Mapping[str, Any] | None = None,
    eval_mode: str | None = None,
) -> Iterator[None]:
    """在执行一条用例期间注入 sessionId、Mock 数据与评测模式。"""

    payload: dict[str, Any] = {"session_id": session_id}
    if eval_mode:
        payload["eval_mode"] = eval_mode
    if mock:
        payload["mock"] = dict(mock)
    token: Token = _CURRENT.set(payload)
    try:
        yield
    finally:
        _CURRENT.reset(token)
