"""把一个自带工具与会话的 HTTP agent 接进 Bridge 的模板。

生产 agent 往往自己管工具与审计，平台既拿不到也不需要那些工具。这类 agent 用
``tool_mode=agent`` 接入：桥按它自己的协议完成一次调用，再把它的工具调用记录
转成 Bridge 的格式，平台只记录、不重复执行，于是过程断言照常可用。

下面用的是「登录 → 建会话 → 发消息 → 拉动作记录」这套常见形状。换成你自己的
系统时，只需要改 ``_authenticate`` / ``_create_session`` / ``_ask`` /
``_tool_calls`` 里的路径与字段名。

环境变量：

- ``AGENT_BASE_URL``：agent 服务地址，默认 ``http://127.0.0.1:8080``
- ``AGENT_TOKEN``：直接给访问令牌；缺省时用 ``AGENT_EMAIL`` /
  ``AGENT_PASSWORD`` 登录换取
- ``AGENT_TIMEOUT_S``：单次请求超时，默认 180 秒
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any

PROTOCOL = "agenteval.bridge/1"
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT_S = 180.0


def _base_url() -> str:
    return os.environ.get("AGENT_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    return float(os.environ.get("AGENT_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        _base_url() + path,
        data=data,
        headers={"content-type": "application/json"},
        method=method,
    )
    if token:
        request.add_header("authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        return json.loads(response.read().decode("utf-8"))


def _authenticate() -> str:
    token = os.environ.get("AGENT_TOKEN")
    if token:
        return token
    email = os.environ.get("AGENT_EMAIL")
    password = os.environ.get("AGENT_PASSWORD")
    if not (email and password):
        raise RuntimeError(
            "缺少凭据：请设置 AGENT_TOKEN，或同时设置 AGENT_EMAIL 与 AGENT_PASSWORD"
        )
    payload = _request("POST", "/auth/login", {"email": email, "password": password})
    return str(payload["data"]["accessToken"])


def _create_session(token: str) -> str:
    payload = _request("POST", "/sessions", {}, token)
    return str(payload["data"]["id"])


def _ask(token: str, session_id: str, question: str) -> str:
    payload = _request(
        "POST", f"/sessions/{session_id}/messages", {"content": question}, token
    )
    return str(payload.get("data", {}).get("reply", ""))


def _tool_calls(token: str, session_id: str) -> list[dict[str, Any]]:
    payload = _request("GET", f"/sessions/{session_id}/actions", None, token)
    items = payload.get("data", {}).get("items", [])
    return [
        {"name": str(item.get("toolName", "")), "arguments": item.get("arguments") or {}}
        for item in items
        if item.get("toolName")
    ]


def handle(request: dict[str, Any]) -> dict[str, Any]:
    capability = request.get("capability")
    if capability != "task":
        return {"error": f"this bridge only serves the task capability: {capability}"}
    case = request.get("case") or {}
    question = str(case.get("description") or case.get("id") or "").strip()
    if not question:
        return {"error": "the task carries neither a description nor an id"}
    token = _authenticate()
    session_id = _create_session(token)
    answer = _ask(token, session_id, question)
    return {"output": answer, "tool_calls": _tool_calls(token, session_id)}


def main() -> None:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        request = json.loads(stripped)
        try:
            response: dict[str, Any] = {"protocol": PROTOCOL, **handle(request)}
        except Exception as exc:  # noqa: BLE001 - 单次调用失败以协议错误返回
            response = {"protocol": PROTOCOL, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return


if __name__ == "__main__":
    main()
