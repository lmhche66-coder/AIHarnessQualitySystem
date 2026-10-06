"""把 PhysioAIOps 后端适配成 Bridge agent。

后端自带 Agent 运行时与工具（知识库检索、当前时间、MCP），因此声明
``tool_mode=agent``：平台不替它执行工具，而是从后端的工具调用审计里读回它
实际调用了什么，再交给过程断言。

环境变量：

- ``KINGFAR_BASE_URL``：后端地址，默认 ``http://127.0.0.1:8010``
- ``KINGFAR_EMAIL`` / ``KINGFAR_PASSWORD``：复用固定账号；缺省时每条用例注册
  一个临时账号，从而天然隔离会话与数据
- ``KINGFAR_TIMEOUT_S``：单次请求超时，默认 180 秒
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid
from typing import Any

PROTOCOL = "agenteval.bridge/1"
DEFAULT_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_TIMEOUT_S = 180.0


def _base_url() -> str:
    return os.environ.get("KINGFAR_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    return float(os.environ.get("KINGFAR_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))


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
    email = os.environ.get("KINGFAR_EMAIL")
    password = os.environ.get("KINGFAR_PASSWORD")
    if email and password:
        payload = _request("POST", "/auth/login", {"email": email, "password": password})
        return str(payload["data"]["accessToken"])
    temporary = f"agenteval-{uuid.uuid4().hex[:10]}@example.com"
    payload = _request(
        "POST",
        "/auth/register",
        {"email": temporary, "display_name": "agenteval", "password": "AgentevalProbe123!"},
    )
    return str(payload["data"]["accessToken"])


def _create_session(token: str) -> str:
    payload = _request("POST", "/chat/sessions", {}, token)
    return str(payload["data"]["id"])


def _stream_answer(token: str, session_id: str, question: str) -> str:
    request = urllib.request.Request(
        f"{_base_url()}/chat/sessions/{session_id}/messages:stream",
        data=json.dumps({"content": question}).encode("utf-8"),
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"},
        method="POST",
    )
    parts: list[str] = []
    failure: str | None = None
    with urllib.request.urlopen(request, timeout=_timeout()) as response:
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "content.delta":
                parts.append(str(event.get("delta", "")))
            elif kind == "error":
                failure = str(event.get("message") or event)
    if failure:
        raise RuntimeError(f"the backend reported an error: {failure}")
    return "".join(parts)


def _tool_calls(token: str, session_id: str) -> list[dict[str, Any]]:
    payload = _request(
        "GET", f"/audits/tool-calls?sessionId={session_id}&limit=200", None, token
    )
    items = payload.get("data", {}).get("items", [])
    return [
        {"name": str(item.get("toolName", "")), "arguments": item.get("arguments") or {}}
        for item in items
        if item.get("toolName")
    ]


def handle(request: dict[str, Any]) -> dict[str, Any]:
    capability = request.get("capability")
    if capability != "task":
        return {"error": f"the kingfar adapter only serves the task capability: {capability}"}
    case = request.get("case") or {}
    question = str(case.get("description") or case.get("id") or "").strip()
    if not question:
        return {"error": "the task carries neither a description nor an id"}
    token = _authenticate()
    session_id = _create_session(token)
    answer = _stream_answer(token, session_id, question)
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
