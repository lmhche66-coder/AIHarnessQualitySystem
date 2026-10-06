"""MCP 契约验证。

平台以子进程 + stdio 启动 MCP server，用最小的 JSON-RPC 2.0 客户端完成会话，
再对握手、能力协商、工具清单、参数契约与「schema 声明与实际行为一致性」做
确定性断言。不依赖官方 SDK，也不需要网络。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field

from agenteval.models import CaseMetrics, CheckOutcome, Run, Status, Trace, Verdict
from agenteval.process import extract_tool_calls, record_tool_call
from agenteval.runner import new_run_id
from agenteval.store import RunStore
from agenteval.tools import ToolErrorKind, ToolResult

PROTOCOL_VERSION = "2024-11-05"


class McpError(RuntimeError):
    """MCP 会话类错误的基类。"""


class McpTimeout(McpError):
    """server 在超时内没有响应。"""


class McpProtocolError(McpError):
    """协议层错误：无法启动、非法消息、方法失败。"""


class McpServerSpec(BaseModel):
    """如何启动一个 MCP server。"""

    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    timeout_s: float = Field(default=10.0, gt=0)
    protocol_version: str = PROTOCOL_VERSION


class _LineReader:
    """把阻塞的 ``readline`` 挪进线程，从而使读取支持超时。"""

    def __init__(self, stream: Any) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream: Any) -> None:
        try:
            for line in stream:
                self._queue.put(line)
        finally:
            self._queue.put(None)

    def read(self, timeout_s: float) -> str:
        try:
            item = self._queue.get(timeout=timeout_s)
        except queue.Empty as exc:
            raise McpTimeout(f"no response within {timeout_s:.3g}s") from exc
        if item is None:
            raise McpProtocolError("the MCP server closed its output stream")
        return item


class StdioTransport:
    """按行分隔的 JSON-RPC 2.0 over stdio 传输。"""

    def __init__(self, spec: McpServerSpec) -> None:
        env = dict(os.environ)
        env.update(spec.env)
        try:
            self.process = subprocess.Popen(
                [spec.command, *spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=spec.cwd,
                env=env,
            )
        except OSError as exc:
            raise McpError(f"could not start the MCP server: {exc}") from exc
        if self.process.stdin is None or self.process.stdout is None:
            raise McpError("the MCP server did not open its stdio pipes")
        self._reader = _LineReader(self.process.stdout)

    def send(self, message: Mapping[str, Any]) -> None:
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpProtocolError(f"could not write to the MCP server: {exc}") from exc

    def receive(self, timeout_s: float) -> dict[str, Any]:
        line = self._reader.read(timeout_s)
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpProtocolError(
                f"the MCP server sent a non-JSON line: {line[:120]!r}"
            ) from exc
        if not isinstance(message, dict):
            raise McpProtocolError("the MCP server sent a JSON message that is not an object")
        return message

    def close(self) -> None:
        process = self.process
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@dataclass
class McpResponse:
    """一次 JSON-RPC 请求的结果：或为 result，或为 error。"""

    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def failed(self) -> bool:
        """协议层失败：JSON-RPC error。"""

        return self.error is not None

    @property
    def is_error(self) -> bool:
        """MCP 语义下的调用失败：协议错误或 ``result.isError``。"""

        if self.error is not None:
            return True
        return bool((self.result or {}).get("isError"))

    def describe(self) -> str:
        if self.error is not None:
            return f"JSON-RPC error {self.error.get('code')}: {self.error.get('message')}"
        result = self.result or {}
        if result.get("isError"):
            return _content_text(result) or "the tool reported an error"
        return "ok"


def _content_text(result: Mapping[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    return " ".join(parts).strip()


class McpClient:
    """最小的 MCP 会话客户端：initialize / tools/list / tools/call。"""

    def __init__(self, spec: McpServerSpec) -> None:
        self.spec = spec
        self._transport: StdioTransport | None = None
        self._next_id = 1
        self.initialize_result: dict[str, Any] | None = None

    def __enter__(self) -> "McpClient":
        self._transport = StdioTransport(self.spec)
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _live(self) -> StdioTransport:
        if self._transport is None:
            raise McpProtocolError("the MCP session has not been started")
        return self._transport

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._live().send(message)

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> McpResponse:
        transport = self._live()
        request_id = self._next_id
        self._next_id += 1
        transport.send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})}
        )
        deadline = time.monotonic() + self.spec.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpTimeout(
                    f"the MCP server did not answer {method} within {self.spec.timeout_s:g}s"
                )
            reply = transport.receive(remaining)
            if reply.get("id") != request_id:
                continue
            if "error" in reply:
                raw = reply["error"]
                error = raw if isinstance(raw, dict) else {"message": str(raw)}
                return McpResponse(error=error)
            result = reply.get("result")
            return McpResponse(result=result if isinstance(result, dict) else {})

    def initialize(self) -> dict[str, Any]:
        response = self.request(
            "initialize",
            {
                "protocolVersion": self.spec.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "agenteval", "version": "0.1"},
            },
        )
        if response.failed:
            raise McpProtocolError(f"initialize failed: {response.describe()}")
        self.initialize_result = response.result or {}
        self.notify("notifications/initialized")
        return self.initialize_result

    def list_tools(self) -> list[dict[str, Any]]:
        response = self.request("tools/list")
        if response.failed:
            raise McpProtocolError(f"tools/list failed: {response.describe()}")
        tools = (response.result or {}).get("tools")
        if not isinstance(tools, list):
            raise McpProtocolError("tools/list result does not contain a tools list")
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> McpResponse:
        return self.request("tools/call", {"name": name, "arguments": dict(arguments)})


class McpCheck(BaseModel):
    """MCP 契约检查的公共基类。"""

    model_config = ConfigDict(extra="forbid")

    kind: str


class CapabilityCheck(McpCheck):
    """验证能力协商结果。"""

    kind: Literal["capability"] = "capability"
    capabilities: list[str] = Field(default_factory=lambda: ["tools"])


class ToolListedCheck(McpCheck):
    """验证工具清单与输入 schema 声明。"""

    kind: Literal["tool_listed"] = "tool_listed"
    tool: str | None = None
    require_schema: bool = True


class MissingRequiredCheck(McpCheck):
    """验证缺失必填参数被拒绝。"""

    kind: Literal["missing_required"] = "missing_required"
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    drop: list[str] = Field(default_factory=list)


class WrongTypeCheck(McpCheck):
    """验证参数类型错误被拒绝。"""

    kind: Literal["wrong_type"] = "wrong_type"
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class ValidCallCheck(McpCheck):
    """验证合法参数被接受。"""

    kind: Literal["valid_call"] = "valid_call"
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


class SchemaConsistencyCheck(McpCheck):
    """验证声明的必填参数在缺失时确实被拒绝。"""

    kind: Literal["schema_consistency"] = "schema_consistency"
    tool: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)


McpCheckSpec = Annotated[
    Union[
        CapabilityCheck,
        ToolListedCheck,
        MissingRequiredCheck,
        WrongTypeCheck,
        ValidCallCheck,
        SchemaConsistencyCheck,
    ],
    Field(discriminator="kind"),
]


class McpCase(BaseModel):
    """一条 MCP 契约用例。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str | None = None
    server: McpServerSpec | None = None
    checks: list[McpCheckSpec] = Field(min_length=1)


class McpCaseSet(BaseModel):
    """一个用例文件：共享的 server 定义加一组用例。"""

    model_config = ConfigDict(extra="forbid")

    server: McpServerSpec
    cases: list[McpCase] = Field(min_length=1)


def load_mcp_cases(path: Path) -> McpCaseSet:
    """载入 MCP 用例文件，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if not isinstance(payload, dict) or "server" not in payload or "cases" not in payload:
        raise ValueError(
            f"MCP case file must contain 'server' and 'cases' keys: {path}"
        )
    return McpCaseSet.model_validate(payload)


def _allowed_tools(spec: CapabilityCheck, initialize_result: Mapping[str, Any]) -> CheckOutcome:
    capabilities = initialize_result.get("capabilities")
    declared = set(capabilities) if isinstance(capabilities, dict) else set()
    missing = [name for name in spec.capabilities if name not in declared]
    return CheckOutcome(
        name="mcp.capability",
        passed=not missing,
        expected=f"capabilities declare {spec.capabilities}",
        actual=sorted(declared),
        message=None if not missing else f"the server did not declare capabilities: {missing}",
    )


def _tool_schema(tool: Mapping[str, Any]) -> dict[str, Any] | None:
    schema = tool.get("inputSchema")
    return schema if isinstance(schema, dict) else None


def _tool_listed_check(spec: ToolListedCheck, tools: Sequence[Mapping[str, Any]]) -> CheckOutcome:
    by_name = {str(tool.get("name", "")): tool for tool in tools}
    names = sorted(name for name in by_name if name)
    if spec.tool is None:
        without_schema = [name for name, tool in by_name.items() if _tool_schema(tool) is None]
        passed = bool(names) and not (spec.require_schema and without_schema)
        return CheckOutcome(
            name="mcp.tool_listed",
            passed=passed,
            expected="a non-empty tool list with input schemas",
            actual=names,
            message=(
                None
                if passed
                else (
                    "the tool list is empty"
                    if not names
                    else f"tools without an input schema: {sorted(without_schema)}"
                )
            ),
        )
    listed = by_name.get(spec.tool)
    if listed is None:
        return CheckOutcome(
            name=f"mcp.tool_listed[{spec.tool}]",
            passed=False,
            expected=f"{spec.tool} is listed",
            actual=names,
            message=f"{spec.tool} was not listed by the server",
        )
    if spec.require_schema and _tool_schema(listed) is None:
        return CheckOutcome(
            name=f"mcp.tool_listed[{spec.tool}]",
            passed=False,
            expected=f"{spec.tool} declares an input schema",
            actual=sorted(listed),
            message=f"{spec.tool} does not declare an inputSchema",
        )
    return CheckOutcome(
        name=f"mcp.tool_listed[{spec.tool}]",
        passed=True,
        expected=f"{spec.tool} is listed with an input schema",
        actual=sorted(listed),
    )


def _record(trace: Trace, tool: str, args: Mapping[str, Any], response: McpResponse) -> None:
    rejected = response.is_error
    if response.failed:
        error_kind: ToolErrorKind | None = ToolErrorKind.FAILED
    elif rejected:
        error_kind = ToolErrorKind.VALIDATION
    else:
        error_kind = None
    record_tool_call(
        trace,
        tool,
        args,
        ToolResult(
            ok=not rejected,
            error_kind=error_kind,
            error_message=None if not rejected else response.describe(),
        ),
    )


def _call(client: McpClient, trace: Trace, tool: str, args: Mapping[str, Any]) -> McpResponse:
    response = client.call_tool(tool, args)
    _record(trace, tool, args, response)
    return response


def _expect_rejected(
    client: McpClient,
    trace: Trace,
    name: str,
    tool: str,
    args: Mapping[str, Any],
) -> CheckOutcome:
    response = _call(client, trace, tool, args)
    rejected = response.is_error
    return CheckOutcome(
        name=name,
        passed=rejected,
        expected=f"{tool} rejects the call ({name})",
        actual=response.describe(),
        message=None if rejected else f"{tool} accepted an invalid call: {name}",
    )


def _missing_required_check(
    spec: MissingRequiredCheck, client: McpClient, trace: Trace
) -> CheckOutcome:
    args = {key: value for key, value in spec.args.items() if key not in spec.drop}
    return _expect_rejected(
        client, trace, f"mcp.missing_required[{spec.tool}]", spec.tool, args
    )


def _wrong_type_check(
    spec: WrongTypeCheck, client: McpClient, trace: Trace
) -> CheckOutcome:
    return _expect_rejected(
        client, trace, f"mcp.wrong_type[{spec.tool}]", spec.tool, spec.args
    )


def _valid_call_check(
    spec: ValidCallCheck, client: McpClient, trace: Trace
) -> CheckOutcome:
    response = _call(client, trace, spec.tool, spec.args)
    passed = not response.is_error
    return CheckOutcome(
        name=f"mcp.valid_call[{spec.tool}]",
        passed=passed,
        expected=f"{spec.tool} accepts valid arguments",
        actual=response.describe(),
        message=None if passed else f"{spec.tool} rejected a valid call: {response.describe()}",
    )


def _schema_consistency_checks(
    spec: SchemaConsistencyCheck,
    client: McpClient,
    trace: Trace,
    tools: Sequence[Mapping[str, Any]],
) -> list[CheckOutcome]:
    by_name = {str(tool.get("name", "")): tool for tool in tools}
    listed = by_name.get(spec.tool)
    if listed is None:
        return [
            CheckOutcome(
                name=f"mcp.schema_consistency[{spec.tool}]",
                passed=False,
                expected=f"{spec.tool} is listed",
                actual=sorted(name for name in by_name if name),
                message=f"{spec.tool} was not listed, so its schema cannot be cross-checked",
            )
        ]
    schema = _tool_schema(listed) or {}
    required = schema.get("required")
    required_names = [str(name) for name in required] if isinstance(required, list) else []
    if not required_names:
        return [
            CheckOutcome(
                name=f"mcp.schema_consistency[{spec.tool}]",
                passed=True,
                expected="declared required parameters are enforced",
                actual="the schema declares no required parameters",
                message="nothing to cross-check: the schema declares no required parameters",
            )
        ]
    outcomes: list[CheckOutcome] = []
    for name in required_names:
        args = {key: value for key, value in spec.args.items() if key != name}
        response = _call(client, trace, spec.tool, args)
        enforced = response.is_error
        outcomes.append(
            CheckOutcome(
                name=f"mcp.schema_consistency[{spec.tool}.{name}]",
                passed=enforced,
                expected=f"omitting the required parameter {name!r} is rejected",
                actual=response.describe(),
                message=(
                    None
                    if enforced
                    else (
                        f"the schema declares {name!r} as required but the server accepted "
                        f"a call without it"
                    )
                ),
            )
        )
    return outcomes


@dataclass
class McpRunner:
    """驱动 MCP 会话并产出与其它各层一致的运行记录。"""

    store: RunStore | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def run_case(self, case: McpCase, server: McpServerSpec) -> tuple[Verdict, Trace]:
        trace = Trace(case_id=case.id)
        started = time.perf_counter()
        try:
            checks = self._evaluate(case, case.server or server, trace)
        except McpError as exc:
            verdict = Verdict(
                case_id=case.id,
                status=Status.ERROR,
                error=f"{type(exc).__name__}: {exc}",
            )
            verdict.duration_ms = round((time.perf_counter() - started) * 1000, 3)
            return verdict, trace
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        status = Status.PASS if checks and all(check.passed for check in checks) else Status.FAIL
        verdict = Verdict(case_id=case.id, status=status, checks=checks)
        verdict.duration_ms = duration_ms
        verdict.metrics = CaseMetrics(
            calls=len(extract_tool_calls(trace)),
            duration_ms=duration_ms,
        )
        return verdict, trace

    def _evaluate(
        self, case: McpCase, server: McpServerSpec, trace: Trace
    ) -> list[CheckOutcome]:
        outcomes: list[CheckOutcome] = []
        tools: list[dict[str, Any]] | None = None
        with McpClient(server) as client:
            initialize_result = client.initialize()
            for spec in case.checks:
                if isinstance(spec, CapabilityCheck):
                    outcomes.append(_allowed_tools(spec, initialize_result))
                elif isinstance(spec, ToolListedCheck):
                    tools = tools if tools is not None else client.list_tools()
                    outcomes.append(_tool_listed_check(spec, tools))
                elif isinstance(spec, MissingRequiredCheck):
                    outcomes.append(_missing_required_check(spec, client, trace))
                elif isinstance(spec, WrongTypeCheck):
                    outcomes.append(_wrong_type_check(spec, client, trace))
                elif isinstance(spec, ValidCallCheck):
                    outcomes.append(_valid_call_check(spec, client, trace))
                elif isinstance(spec, SchemaConsistencyCheck):
                    tools = tools if tools is not None else client.list_tools()
                    outcomes.extend(_schema_consistency_checks(spec, client, trace, tools))
        return outcomes

    def run(
        self,
        case_set: McpCaseSet,
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        merged = dict(self.metadata)
        merged.update(metadata or {})
        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=merged,
        )
        for case in case_set.cases:
            verdict, trace = self.run_case(case, case_set.server)
            run.verdicts.append(verdict)
            run.traces.append(trace)
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run
