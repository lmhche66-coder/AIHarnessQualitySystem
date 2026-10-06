"""UI 流程检查。

流程与驱动解耦：脚本驱动用于精确验证断言引擎，Playwright 驱动用于端到端确认。
失败时留下截图——没有截图的 UI 失败基本无法定位。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agenteval.models import CaseMetrics, CheckOutcome, Run, Status, Verdict
from agenteval.runner import new_run_id
from agenteval.store import RunStore

UI_DIRNAME = "ui"
DRIVER_PLAYWRIGHT = "playwright"


class DriverUnavailable(RuntimeError):
    """驱动不可用，消息中说明需要安装什么。"""


class UiStep(BaseModel):
    """一个流程步骤。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["goto", "click", "fill", "wait"]
    target: str = ""
    value: str | None = None
    timeout_ms: int = Field(default=5000, gt=0)


class UiAssertion(BaseModel):
    """一条断言。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["visible", "text", "count", "url"]
    target: str = ""
    expected: Any = None


class UiFlow(BaseModel):
    """一条 UI 流程。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str | None = None
    base_url: str = ""
    steps: list[UiStep] = Field(default_factory=list)
    expect: list[UiAssertion] = Field(default_factory=list)
    max_steps: int | None = Field(default=None, ge=1)
    max_duration_ms: float | None = Field(default=None, gt=0)
    screenshot_on_failure: bool = True

    @model_validator(mode="after")
    def _require_steps_and_assertions(self) -> "UiFlow":
        if not self.steps:
            raise ValueError("flow must declare at least one step")
        if not self.expect:
            raise ValueError("flow must declare at least one assertion")
        return self


class UiDriver(Protocol):
    """流程执行所需的全部页面操作。"""

    def goto(self, url: str, timeout_ms: int) -> None: ...

    def click(self, selector: str, timeout_ms: int) -> None: ...

    def fill(self, selector: str, value: str, timeout_ms: int) -> None: ...

    def wait(self, selector: str, timeout_ms: int) -> None: ...

    def is_visible(self, selector: str) -> bool: ...

    def text(self, selector: str) -> str: ...

    def count(self, selector: str) -> int: ...

    def current_url(self) -> str: ...

    def screenshot(self, path: Path) -> None: ...

    def close(self) -> None: ...


def resolve_url(target: str, base_url: str) -> str:
    """把相对地址解析到流程的基础地址。"""

    if not base_url or "://" in target:
        return target
    return f"{base_url.rstrip('/')}/{target.lstrip('/')}"


def load_flows(path: Path) -> list[UiFlow]:
    """载入流程定义，接受 JSON 或 YAML。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict):
        if "flows" not in payload:
            raise ValueError(f"flow file must contain a list or a 'flows' key: {path}")
        payload = payload["flows"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"flow file must contain at least one flow: {path}")
    return [UiFlow.model_validate(entry) for entry in payload]


class ScriptedDriver:
    """脚本驱动：用一份内存页面验证断言引擎，不启动浏览器。"""

    def __init__(
        self,
        elements: Mapping[str, Mapping[str, Any]] | None = None,
        url: str = "",
    ) -> None:
        self.elements = {key: dict(value) for key, value in (elements or {}).items()}
        self.url = url
        self.actions: list[tuple[str, str, str | None]] = []
        self.failures: dict[str, Exception] = {}
        self.screenshots: list[Path] = []

    def _entry(self, selector: str) -> Mapping[str, Any]:
        return self.elements.get(selector, {})

    def _guard(self, selector: str) -> None:
        failure = self.failures.get(selector)
        if failure is not None:
            raise failure

    def goto(self, url: str, timeout_ms: int) -> None:
        self._guard(url)
        self.url = url
        self.actions.append(("goto", url, None))

    def click(self, selector: str, timeout_ms: int) -> None:
        self._guard(selector)
        self.actions.append(("click", selector, None))

    def fill(self, selector: str, value: str, timeout_ms: int) -> None:
        self._guard(selector)
        self.actions.append(("fill", selector, value))

    def wait(self, selector: str, timeout_ms: int) -> None:
        self._guard(selector)
        self.actions.append(("wait", selector, None))

    def is_visible(self, selector: str) -> bool:
        return bool(self._entry(selector).get("visible", False))

    def text(self, selector: str) -> str:
        return str(self._entry(selector).get("text", ""))

    def count(self, selector: str) -> int:
        entry = self._entry(selector)
        if "count" in entry:
            return int(entry["count"])
        return 1 if entry else 0

    def current_url(self) -> str:
        return self.url

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"scripted")
        self.screenshots.append(path)

    def close(self) -> None:
        return None


class PlaywrightDriver:
    """真实浏览器驱动；``playwright`` 未安装时构造即给出可读错误。"""

    def __init__(self, headless: bool = True) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise DriverUnavailable(
                "the playwright driver needs the optional dependency: "
                "pip install 'agenteval[ui]' && playwright install chromium"
            ) from exc
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=headless)
        except Exception as exc:  # pragma: no cover - 浏览器缺失路径
            self._playwright.stop()
            raise DriverUnavailable(
                f"could not launch chromium, run 'playwright install chromium': {exc}"
            ) from exc
        self._page = self._browser.new_page()

    def goto(self, url: str, timeout_ms: int) -> None:
        self._page.goto(url, timeout=timeout_ms, wait_until="load")

    def click(self, selector: str, timeout_ms: int) -> None:
        self._page.click(selector, timeout=timeout_ms)

    def fill(self, selector: str, value: str, timeout_ms: int) -> None:
        self._page.fill(selector, value, timeout=timeout_ms)

    def wait(self, selector: str, timeout_ms: int) -> None:
        self._page.wait_for_selector(selector, timeout=timeout_ms)

    def is_visible(self, selector: str) -> bool:
        return bool(self._page.is_visible(selector))

    def text(self, selector: str) -> str:
        return self._page.inner_text(selector)

    def count(self, selector: str) -> int:
        return int(self._page.locator(selector).count())

    def current_url(self) -> str:
        return self._page.url

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(path))

    def close(self) -> None:
        try:
            self._browser.close()
        finally:
            self._playwright.stop()


def build_driver_factory(name: str, headless: bool = True) -> Callable[[], UiDriver]:
    if name != DRIVER_PLAYWRIGHT:
        raise ValueError(f"unknown driver: {name}")
    return lambda: PlaywrightDriver(headless=headless)


@dataclass
class UiRunner:
    """执行 UI 流程并产出与其它各层一致的运行记录。"""

    driver_factory: Callable[[], UiDriver]
    store: RunStore | None = None
    artifacts_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def run_flow(self, flow: UiFlow) -> tuple[Verdict, dict[str, Any]]:
        artifact: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            driver = self.driver_factory()
        except Exception as exc:  # noqa: BLE001 - 驱动不可用也要成为判定
            return (
                Verdict(case_id=flow.id, status=Status.ERROR, error=f"{type(exc).__name__}: {exc}"),
                artifact,
            )

        checks: list[CheckOutcome] = []
        error: str | None = None
        try:
            _execute(driver, flow)
        except Exception as exc:  # noqa: BLE001 - 步骤异常记为 error
            error = f"{type(exc).__name__}: {exc}"
            checks.append(
                CheckOutcome(
                    name="ui.steps",
                    passed=False,
                    expected="all steps complete",
                    actual=error,
                    message="a step failed before assertions could run",
                )
            )
        else:
            checks.extend(_evaluate(driver, flow))
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            checks.extend(_budget_checks(flow, elapsed_ms))
            status = (
                Status.ERROR
                if error
                else (Status.PASS if all(check.passed for check in checks) else Status.FAIL)
            )
            verdict = Verdict(case_id=flow.id, status=status, checks=checks, error=error)
            verdict.duration_ms = round(elapsed_ms, 3)
            verdict.metrics = CaseMetrics(calls=len(flow.steps), duration_ms=verdict.duration_ms)
            if verdict.status is not Status.PASS and flow.screenshot_on_failure:
                self._capture(driver, flow, artifact, verdict)
            driver.close()
        return verdict, artifact

    def _capture(
        self,
        driver: UiDriver,
        flow: UiFlow,
        artifact: dict[str, Any],
        verdict: Verdict,
    ) -> None:
        if self.artifacts_dir is None:
            return
        path = Path(self.artifacts_dir) / f"{flow.id}.png"
        try:
            driver.screenshot(path)
        except Exception as exc:  # noqa: BLE001 - 截图失败不影响结论
            artifact["screenshot_error"] = f"{type(exc).__name__}: {exc}"
            return
        artifact["screenshot"] = str(path)
        verdict.checks.append(
            CheckOutcome(
                name="ui.screenshot",
                passed=True,
                expected="a screenshot is saved when a flow fails",
                actual=str(path),
            )
        )

    def run(
        self,
        flows: Sequence[UiFlow],
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Run:
        flow_list = list(flows)
        merged = dict(self.metadata)
        merged.update(metadata or {})
        run = Run(
            run_id=run_id or new_run_id(),
            started_at=datetime.now(timezone.utc),
            metadata=merged,
        )
        artifacts: list[dict[str, Any]] = []
        for flow in flow_list:
            verdict, artifact = self.run_flow(flow)
            run.verdicts.append(verdict)
            artifacts.append(artifact)
        run.metadata["ui_artifacts"] = artifacts
        run.finished_at = datetime.now(timezone.utc)
        run.refresh_summary()
        if self.store is not None:
            self.store.save(run)
        return run


def _execute(driver: UiDriver, flow: UiFlow) -> None:
    for step in flow.steps:
        if step.action == "goto":
            driver.goto(resolve_url(step.target, flow.base_url), step.timeout_ms)
        elif step.action == "click":
            driver.click(step.target, step.timeout_ms)
        elif step.action == "fill":
            driver.fill(step.target, step.value or "", step.timeout_ms)
        else:
            driver.wait(step.target, step.timeout_ms)


def _evaluate(driver: UiDriver, flow: UiFlow) -> list[CheckOutcome]:
    outcomes: list[CheckOutcome] = []
    for assertion in flow.expect:
        name = f"ui.{assertion.kind}"
        if assertion.kind == "visible":
            actual = driver.is_visible(assertion.target)
            passed = actual is True
            expected: Any = True
        elif assertion.kind == "text":
            actual = driver.text(assertion.target)
            passed = str(assertion.expected) in actual
            expected = assertion.expected
        elif assertion.kind == "count":
            actual = driver.count(assertion.target)
            passed = actual == assertion.expected
            expected = assertion.expected
        else:
            actual = driver.current_url()
            passed = str(assertion.expected) in actual
            expected = assertion.expected
        outcomes.append(
            CheckOutcome(name=name, passed=passed, expected=expected, actual=actual)
        )
    return outcomes


def _budget_checks(flow: UiFlow, elapsed_ms: float) -> list[CheckOutcome]:
    outcomes: list[CheckOutcome] = []
    if flow.max_steps is not None:
        actual = len(flow.steps)
        outcomes.append(
            CheckOutcome(
                name="ui.max_steps",
                passed=actual <= flow.max_steps,
                expected=f"<= {flow.max_steps}",
                actual=actual,
            )
        )
    if flow.max_duration_ms is not None:
        elapsed = round(elapsed_ms, 3)
        outcomes.append(
            CheckOutcome(
                name="ui.max_duration_ms",
                passed=elapsed <= flow.max_duration_ms,
                expected=f"<= {flow.max_duration_ms}",
                actual=elapsed,
            )
        )
    return outcomes
