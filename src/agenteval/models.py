"""评测平台的统一数据模型。

Case / Trace / Verdict / Run 四类对象是后续所有层（轨迹断言、裁判、门禁、
可观测）共用的契约，因此字段命名与序列化格式在此固定下来。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class Status(str, Enum):
    """单条用例的判定状态。"""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class ContractCheck(BaseModel):
    """契约检查的公共基类，``kind`` 用于区分具体检查类型。"""

    model_config = ConfigDict(extra="forbid")

    kind: str


class MissingRequiredCheck(ContractCheck):
    """验证必填参数缺失时返回结构化校验错误。"""

    kind: Literal["missing_required"] = "missing_required"
    drop: list[str] = Field(default_factory=list)


class WrongTypeCheck(ContractCheck):
    """验证参数类型错误时返回结构化校验错误。"""

    kind: Literal["wrong_type"] = "wrong_type"
    overrides: dict[str, Any] = Field(default_factory=dict)


class TimeoutCheck(ContractCheck):
    """验证工具超时后受控结束且不阻塞调用方。"""

    kind: Literal["timeout"] = "timeout"
    timeout_s: float = Field(default=1.0, gt=0)


class RateLimitCheck(ContractCheck):
    """验证限流重试行为。"""

    kind: Literal["rate_limit"] = "rate_limit"
    max_attempts: int = Field(default=3, ge=1)
    expect_success: bool = True


class IdempotencyCheck(ContractCheck):
    """验证相同幂等键重复调用不产生重复副作用。"""

    kind: Literal["idempotent_retry"] = "idempotent_retry"
    idempotency_key: str
    repeat: int = Field(default=2, ge=2)


class RollbackCheck(ContractCheck):
    """验证多步操作部分失败后回滚已完成步骤。"""

    kind: Literal["partial_rollback"] = "partial_rollback"
    steps: list[str] = Field(default_factory=list)
    fail_at: str = ""


ContractCheckSpec = Annotated[
    Union[
        MissingRequiredCheck,
        WrongTypeCheck,
        TimeoutCheck,
        RateLimitCheck,
        IdempotencyCheck,
        RollbackCheck,
    ],
    Field(discriminator="kind"),
]


class Case(BaseModel):
    """一条评测用例。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["tool_contract"] = "tool_contract"
    target: str = Field(min_length=1)
    description: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    check: ContractCheckSpec


class CheckOutcome(BaseModel):
    """单条断言的执行结果。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    expected: Any = None
    actual: Any = None
    message: str | None = None


class Verdict(BaseModel):
    """一条用例的判定结果。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Status
    checks: list[CheckOutcome] = Field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def failed_checks(self) -> list[CheckOutcome]:
        return [check for check in self.checks if not check.passed]


class TraceEvent(BaseModel):
    """轨迹中的单个事件。"""

    model_config = ConfigDict(extra="forbid")

    seq: int
    name: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Trace(BaseModel):
    """一条用例的执行轨迹。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    events: list[TraceEvent] = Field(default_factory=list)
    attempts: int = 0
    side_effects: list[str] = Field(default_factory=list)

    def record(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            seq=len(self.events) + 1,
            name=name,
            payload=payload or {},
            error=error,
        )
        self.events.append(event)
        return event


class RunSummary(BaseModel):
    """一次运行的判定汇总。"""

    model_config = ConfigDict(extra="forbid")

    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0


class Run(BaseModel):
    """一次完整运行。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    schema_version: str = "1"
    started_at: datetime
    finished_at: datetime | None = None
    verdicts: list[Verdict] = Field(default_factory=list)
    traces: list[Trace] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def refresh_summary(self) -> RunSummary:
        """按当前判定重新计算汇总计数。"""

        counts = {status: 0 for status in Status}
        for verdict in self.verdicts:
            counts[verdict.status] += 1
        self.summary = RunSummary(
            total=len(self.verdicts),
            passed=counts[Status.PASS],
            failed=counts[Status.FAIL],
            errored=counts[Status.ERROR],
        )
        return self.summary
