"""示例 MCP server：在 stdin/stdout 上说 JSON-RPC 2.0。

只依赖标准库，供 MCP 契约用例与 CI 使用。设置环境变量
``AGENTEVAL_MCP_FLAKY=1`` 会让它跳过参数校验——也就是「schema 声明了必填，
实际却接受缺失」，用来验证平台能检出这种声明与行为不一致的 server。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "description": "回显一条消息",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add",
        "description": "两数相加",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    },
]

FLAKY = os.environ.get("AGENTEVAL_MCP_FLAKY") == "1"
NO_TOOLS = os.environ.get("AGENTEVAL_MCP_NO_TOOLS") == "1"
NO_SCHEMA = os.environ.get("AGENTEVAL_MCP_NO_SCHEMA") == "1"


def _invalid(schema: dict[str, Any], args: dict[str, Any]) -> str | None:
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in args:
            return f"missing required argument: {name}"
    for name, value in args.items():
        spec = properties.get(name)
        if spec is None:
            if schema.get("additionalProperties") is False:
                return f"unexpected argument: {name}"
            continue
        expected = spec.get("type")
        if expected == "string" and not isinstance(value, str):
            return f"argument {name} must be a string"
        if expected == "integer" and not isinstance(value, int):
            return f"argument {name} must be an integer"
    return None


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    tool = next((item for item in TOOLS if item["name"] == name), None)
    if tool is None:
        return _error(f"unknown tool: {name}")
    if not FLAKY:
        problem = _invalid(tool["inputSchema"], args)
        if problem is not None:
            return _error(problem)
    if name == "echo":
        return {"content": [{"type": "text", "text": str(args.get("message", ""))}], "isError": False}
    total = args.get("a", 0) + args.get("b", 0)
    return {"content": [{"type": "text", "text": str(total)}], "isError": False}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {} if NO_TOOLS else {"tools": {}},
                "serverInfo": {"name": "agenteval-demo-mcp", "version": "1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        tools = (
            [{key: value for key, value in tool.items() if key != "inputSchema"} for tool in TOOLS]
            if NO_SCHEMA
            else TOOLS
        )
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}
    if method == "tools/call":
        params = message.get("params") or {}
        result = _call_tool(str(params.get("name", "")), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> None:
    for line in sys.stdin:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        reply = handle(message)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
