"""工具适配器协议、结构化错误类型与工具注册表。

契约断言的观察对象是「调用结果」，而不是平台内部的校验代码，因此既可验证
用户自带工具，也可验证平台提供的带 schema 校验的基类。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import jsonschema
from pydantic import BaseModel, ConfigDict


class ToolErrorKind(str, Enum):
    """结构化的工具错误类别。"""

    VALIDATION = "validation"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UPSTREAM = "upstream"
    NOT_FOUND = "not_found"
    ROLLBACK_FAILED = "rollback_failed"
    CASSETTE_MISS = "cassette_miss"


class ToolError(Exception):
    """携带错误类别的工具异常，供需要抛错风格的适配器使用。"""

    def __init__(
        self,
        kind: ToolErrorKind | str,
        message: str,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = ToolErrorKind(kind)
        self.message = message
        self.field = field


class ToolResult(BaseModel):
    """一次工具调用的结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    value: Any = None
    error_kind: ToolErrorKind | None = None
    error_message: str | None = None
    error_field: str | None = None
    attempts: int = 1


@runtime_checkable
class Tool(Protocol):
    """工具适配器协议。"""

    name: str
    input_schema: dict[str, Any]

    def invoke(self, **kwargs: Any) -> ToolResult:
        ...


class SchemaTool:
    """按 JSON Schema 校验入参的工具基类。

    子类实现 :meth:`run`，非法参数会被转换为 ``validation`` 类别的结构化
    错误，而不是抛出未处理异常。
    """

    name: str = ""
    input_schema: dict[str, Any] = {"type": "object"}

    def invoke(self, **kwargs: Any) -> ToolResult:
        try:
            jsonschema.validate(instance=kwargs, schema=self.input_schema)
        except jsonschema.ValidationError as exc:
            return ToolResult(
                ok=False,
                error_kind=ToolErrorKind.VALIDATION,
                error_message=exc.message,
                error_field=_error_field(exc),
            )
        return self.run(**kwargs)

    def run(self, **kwargs: Any) -> ToolResult:  # pragma: no cover - 抽象方法
        raise NotImplementedError


class ToolRegistry:
    """工具注册表，按名称分派契约用例。"""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        name = getattr(tool, "name", "")
        if not name:
            raise ValueError("tool must declare a non-empty name")
        if name in self._tools:
            raise ValueError(f"duplicate tool name: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"tool not registered: {name}") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return sorted(self._tools)


def _first_path_segment(path: Iterable[Any]) -> str | None:
    for segment in path:
        return str(segment)
    return None


def _error_field(exc: jsonschema.ValidationError) -> str | None:
    """定位出错字段。

    属性路径为空时通常是对象级校验失败（例如 ``required``），此时从校验器
    的约束里反推缺失或多余的属性名。
    """

    path_field = _first_path_segment(exc.absolute_path)
    if path_field is not None:
        return path_field
    instance = exc.instance if isinstance(exc.instance, dict) else {}
    if exc.validator == "required" and isinstance(exc.validator_value, list):
        missing = [name for name in exc.validator_value if name not in instance]
        if missing:
            return str(missing[0])
    if exc.validator == "additionalProperties":
        extra = [name for name in instance if name not in _schema_properties(exc)]
        if extra:
            return str(extra[0])
    return None


def _schema_properties(exc: jsonschema.ValidationError) -> set[str]:
    schema = exc.schema if isinstance(exc.schema, dict) else {}
    properties = schema.get("properties")
    return set(properties) if isinstance(properties, dict) else set()
