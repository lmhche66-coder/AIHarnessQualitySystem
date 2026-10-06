"""Agent Bridge：让任意语言、任意进程、任意主机的 agent 接入评测。

平台与 agent 之间只认一份 JSON wire 协议，传输可以是 HTTP endpoint 或本地子
进程。平台负责把四类评测能力（task / dialogue / redteam / selection）翻译成
协议请求，agent 只需实现一个入口。
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agenteval.dialogue import Conversation, Role
from agenteval.metrics import summarize_metrics
from agenteval.models import CaseMetrics, Run, TokenUsage
from agenteval.process import ToolCall
from agenteval.redteam import Probe, ProbeOutcome
from agenteval.selection import SelectionCall
from agenteval.tools import ToolRegistry

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注
    from agenteval.models import TaskCase

PROTOCOL_VERSION = "agenteval.bridge/1"
DEFAULT_MAX_STEPS = 8
DEFAULT_TIMEOUT_S = 30.0

Capability = Literal["task", "dialogue", "redteam", "selection"]


class BridgeError(RuntimeError):
    """Bridge 会话类错误，消息可直接展示。"""


# --------------------------------------------------------------------------- 协议模型


class ToolDescriptor(BaseModel):
    """平台暴露给 agent 的工具声明。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """agent 请求执行（或已执行）的一次工具调用。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class BridgeMessage(BaseModel):
    """对话或工具循环中的一条消息。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant", "tool"] = "user"
    content: str = ""
    name: str | None = None
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)


class BridgeRequest(BaseModel):
    """平台发给 agent 的一次评测调用。"""

    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL_VERSION
    app: str = ""
    version: str = ""
    capability: Capability
    case: dict[str, Any] = Field(default_factory=dict)
    tools: list[ToolDescriptor] = Field(default_factory=list)
    messages: list[BridgeMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class BridgeResponse(BaseModel):
    """agent 返回给平台的结果。"""

    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL_VERSION
    output: str = ""
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    usage: TokenUsage | None = None
    error: str | None = None


# --------------------------------------------------------------------------- 传输


@runtime_checkable
class BridgeTransport(Protocol):
    """一次请求/响应往返的载体。"""

    def call(self, request: BridgeRequest, timeout_s: float) -> BridgeResponse:
        ...

    def close(self) -> None:
        ...


def _parse_response(payload: Any) -> BridgeResponse:
    if not isinstance(payload, dict):
        raise BridgeError("the agent returned a JSON message that is not an object")
    protocol = payload.get("protocol")
    if protocol is not None and protocol != PROTOCOL_VERSION:
        raise BridgeError(
            f"unsupported bridge protocol version: {protocol!r} "
            f"(this platform speaks {PROTOCOL_VERSION!r})"
        )
    try:
        response = BridgeResponse.model_validate(payload)
    except ValidationError as exc:
        raise BridgeError(f"the agent returned a malformed response: {exc}") from exc
    if response.error:
        raise BridgeError(f"the agent reported an error: {response.error}")
    return response


@dataclass
class HttpTransport:
    """通过 HTTP POST 调用远端 agent。"""

    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def call(self, request: BridgeRequest, timeout_s: float) -> BridgeResponse:
        body = request.model_dump_json().encode("utf-8")
        headers = {"content-type": "application/json", **self.headers}
        http_request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(http_request, timeout=timeout_s) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise BridgeError(f"HTTP {exc.code} from {self.url}: {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BridgeError(f"could not reach {self.url}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"the agent returned invalid JSON: {exc}") from exc
        return _parse_response(payload)

    def close(self) -> None:
        return None


@dataclass
class SubprocessTransport:
    """通过 stdin/stdout 的单行 JSON 调用本地 agent 命令。"""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def call(self, request: BridgeRequest, timeout_s: float) -> BridgeResponse:
        env = dict(os.environ)
        env.update(self.env)
        try:
            process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=self.cwd,
                env=env,
            )
        except OSError as exc:
            raise BridgeError(f"could not start the agent command: {exc}") from exc
        try:
            stdout, stderr = process.communicate(request.model_dump_json() + "\n", timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise BridgeError(
                f"the agent did not respond within {timeout_s:g}s"
            ) from None
        if process.returncode != 0:
            detail = (stderr or "").strip()[:200]
            raise BridgeError(
                f"the agent exited with code {process.returncode}: {detail or 'no stderr'}"
            )
        stripped = (stdout or "").strip()
        if not stripped:
            raise BridgeError("the agent produced no output")
        try:
            payload = json.loads(stripped.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise BridgeError(f"the agent returned invalid JSON: {exc}") from exc
        return _parse_response(payload)

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------- 注册表


class AgentSpec(BaseModel):
    """注册表里的一个 agent 应用。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    transport: Literal["http", "subprocess"]
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    tool_mode: Literal["platform", "agent"] = "platform"
    timeout_s: float = Field(default=DEFAULT_TIMEOUT_S, gt=0)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1)

    @model_validator(mode="after")
    def _require_transport_details(self) -> "AgentSpec":
        if self.transport == "http" and not self.url:
            raise ValueError("an http agent must declare url")
        if self.transport == "subprocess" and not self.command:
            raise ValueError("a subprocess agent must declare command")
        return self


@dataclass
class AgentRegistry:
    """多个 agent 应用的注册表。"""

    agents: dict[str, AgentSpec] = field(default_factory=dict)

    def get(self, app_id: str) -> AgentSpec:
        spec = self.agents.get(app_id)
        if spec is None:
            known = ", ".join(sorted(self.agents)) or "none"
            raise BridgeError(f"unknown agent '{app_id}'; registered agents: {known}")
        return spec

    def ids(self) -> list[str]:
        return sorted(self.agents)


def load_agent_registry(path: Path) -> AgentRegistry:
    """载入 agent 注册表，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "agents" not in payload:
            raise ValueError(f"agent registry must contain an 'agents' key: {path}")
        payload = payload["agents"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"agent registry must contain at least one agent: {path}")
    specs = [AgentSpec.model_validate(entry) for entry in payload]
    registry: dict[str, AgentSpec] = {}
    for spec in specs:
        if spec.id in registry:
            raise ValueError(f"duplicate agent id in registry: {spec.id}")
        registry[spec.id] = spec
    return AgentRegistry(registry)


def check_availability(spec: AgentSpec) -> tuple[bool, str]:
    """在评测前检查注册条目是否可用。"""

    if spec.transport == "subprocess":
        command = spec.command or ""
        if shutil.which(command) is None and not Path(command).exists():
            return False, f"command not found: {command}"
        return True, f"the command is available: {command}"
    parsed = urllib.parse.urlparse(spec.url or "")
    host = parsed.hostname
    if host is None:
        return False, f"the url has no host: {spec.url!r}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=min(spec.timeout_s, 5.0)):
            return True, f"{host}:{port} is reachable"
    except OSError as exc:
        return False, f"cannot reach {host}:{port}: {exc}"


# --------------------------------------------------------------------------- Bridge agent


def _describe_tools(registry: ToolRegistry) -> list[ToolDescriptor]:
    return [
        ToolDescriptor(name=name, input_schema=dict(registry.get(name).input_schema))
        for name in registry.names()
    ]


def _tool_result_payload(result: Any) -> str:
    payload = {
        "ok": bool(result.ok),
        "value": result.value,
        "error_kind": getattr(result.error_kind, "value", result.error_kind),
        "error_message": result.error_message,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _conversation_messages(conversation: Conversation) -> list[BridgeMessage]:
    messages: list[BridgeMessage] = []
    for turn in conversation.turns:
        if turn.role is Role.USER:
            messages.append(BridgeMessage(role="user", content=turn.content))
        else:
            messages.append(
                BridgeMessage(
                    role="assistant",
                    content=turn.content,
                    tool_calls=[
                        ToolCallRequest(name=call.target, arguments=call.args)
                        for call in turn.calls
                    ],
                )
            )
    return messages


@dataclass
class BridgeAgent:
    """把一个注册条目适配成平台的各能力调用。"""

    spec: AgentSpec
    _usage: dict[str, TokenUsage] = field(default_factory=dict, repr=False)

    def transport(self) -> BridgeTransport:
        if self.spec.transport == "http":
            return HttpTransport(url=self.spec.url or "", headers=dict(self.spec.headers))
        return SubprocessTransport(
            command=self.spec.command or "",
            args=list(self.spec.args),
            env=dict(self.spec.env),
            cwd=self.spec.cwd,
        )

    def supports(self, capability: str) -> bool:
        return not self.spec.capabilities or capability in self.spec.capabilities

    def _require_tool_mode(self, capability: str) -> None:
        if self.spec.tool_mode != "platform":
            raise BridgeError(
                f"agent '{self.spec.id}' declares tool_mode={self.spec.tool_mode!r}, "
                f"but {capability} requires the platform to execute tools"
            )

    def invoke(
        self,
        capability: Capability,
        case: Mapping[str, Any],
        tools: Sequence[ToolDescriptor] = (),
        messages: Sequence[BridgeMessage] = (),
        case_id: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> BridgeResponse:
        if not self.supports(capability):
            declared = ", ".join(self.spec.capabilities) or "none"
            raise BridgeError(
                f"agent '{self.spec.id}' does not declare capability {capability!r}; "
                f"declared: {declared}"
            )
        request = BridgeRequest(
            app=self.spec.id,
            version=self.spec.version,
            capability=capability,
            case=dict(case),
            tools=list(tools),
            messages=list(messages),
            context=dict(context or {}),
        )
        response = self.transport().call(request, self.spec.timeout_s)
        if response.usage is not None:
            self._accumulate(case_id or str(case.get("id", "")), response.usage)
        return response

    def _accumulate(self, case_id: str, usage: TokenUsage) -> None:
        current = self._usage.get(case_id)
        if current is None:
            self._usage[case_id] = usage
            return
        self._usage[case_id] = TokenUsage(
            input_tokens=current.input_tokens + usage.input_tokens,
            output_tokens=current.output_tokens + usage.output_tokens,
        )

    def apply_usage(self, run: Run) -> None:
        """把 agent 上报的用量并入运行记录，并刷新汇总指标。"""

        if not self._usage:
            return
        for verdict in run.verdicts:
            usage = self._usage.get(verdict.case_id)
            if usage is None:
                continue
            metrics = verdict.metrics or CaseMetrics()
            verdict.metrics = metrics.model_copy(
                update={
                    "input_tokens": metrics.input_tokens + usage.input_tokens,
                    "output_tokens": metrics.output_tokens + usage.output_tokens,
                    "usage_reported": True,
                }
            )
        run.metadata["metrics"] = summarize_metrics(run.verdicts).model_dump(mode="json")

    def record_metadata(self, metadata: dict[str, Any]) -> None:
        """把应用标识与版本写入运行元数据，便于结论追溯。"""

        metadata["app"] = self.spec.id
        metadata["app_version"] = self.spec.version
        metadata["app_transport"] = self.spec.transport
        metadata["tool_mode"] = self.spec.tool_mode

    # ---------------------------------------------------------------- 能力适配

    def for_task(self) -> Callable[["ToolRegistry", "TaskCase"], None]:
        def run(registry: ToolRegistry, task: "TaskCase") -> None:
            self._require_tool_mode("task")
            tools = _describe_tools(registry)
            prompt = task.description or task.id
            messages = [BridgeMessage(role="user", content=prompt)]
            for _ in range(self.spec.max_steps):
                response = self.invoke(
                    "task", task.model_dump(mode="json"), tools, messages, task.id
                )
                if not response.tool_calls:
                    return
                messages.append(
                    BridgeMessage(
                        role="assistant",
                        content=response.output,
                        tool_calls=response.tool_calls,
                    )
                )
                for call in response.tool_calls:
                    try:
                        result = registry.get(call.name).invoke(**call.arguments)
                    except KeyError as exc:
                        raise BridgeError(f"the agent requested an unknown tool: {exc}") from exc
                    messages.append(
                        BridgeMessage(role="tool", name=call.name, content=_tool_result_payload(result))
                    )
            raise BridgeError(
                f"agent '{self.spec.id}' exceeded max_steps={self.spec.max_steps} without finishing"
            )

        return run

    def for_dialogue(self) -> Callable[[ToolRegistry, Conversation], str]:
        def respond(registry: ToolRegistry, conversation: Conversation) -> str:
            self._require_tool_mode("dialogue")
            tools = _describe_tools(registry)
            messages = _conversation_messages(conversation)
            response = self.invoke(
                "dialogue", {"id": conversation.id}, tools, messages, conversation.id
            )
            for call in response.tool_calls:
                try:
                    registry.get(call.name).invoke(**call.arguments)
                except KeyError as exc:
                    raise BridgeError(f"the agent requested an unknown tool: {exc}") from exc
            return response.output

        return respond

    def for_redteam(self) -> Callable[[Probe], ProbeOutcome]:
        def target(probe: Probe) -> ProbeOutcome:
            response = self.invoke(
                "redteam",
                probe.model_dump(mode="json"),
                (),
                [BridgeMessage(role="user", content=probe.payload)],
                probe.id,
            )
            calls = [
                ToolCall(index=index, target=call.name, args=dict(call.arguments), ok=True)
                for index, call in enumerate(response.tool_calls)
            ]
            return ProbeOutcome(response=response.output, calls=calls)

        return target

    def for_selection(self) -> Callable[[Any], list[SelectionCall]]:
        def choose(case: Any) -> list[SelectionCall]:
            response = self.invoke(
                "selection", case.model_dump(mode="json"), (), (), case.id
            )
            return [
                SelectionCall(name=call.name, arguments=dict(call.arguments))
                for call in response.tool_calls
            ]

        return choose


# --------------------------------------------------------------------------- 引用解析


def parse_direct_reference(capability: str, ref: str) -> AgentSpec | None:
    """把 ``http(s)://...`` 与 ``cmd:...`` 形式的引用解析为临时注册条目。"""

    if ref.startswith("http://") or ref.startswith("https://"):
        return AgentSpec(
            id=ref,
            version="direct",
            transport="http",
            url=ref,
            capabilities=[capability],
        )
    if ref.startswith("cmd:"):
        parts = shlex.split(ref[4:].strip())
        if not parts:
            raise BridgeError("a cmd: reference must name a command to run")
        return AgentSpec(
            id=ref,
            version="direct",
            transport="subprocess",
            command=parts[0],
            args=parts[1:],
            capabilities=[capability],
        )
    return None
