from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

import pytest

from agenteval.cli import main
from agenteval.models import Status
from agenteval.store import RunStore
from agenteval.ui import (
    DriverUnavailable,
    PlaywrightDriver,
    ScriptedDriver,
    UiFlow,
    UiRunner,
    build_driver_factory,
    load_flows,
    resolve_url,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PLAYWRIGHT_READY = importlib.util.find_spec("playwright") is not None


def flow(**overrides: object) -> UiFlow:
    payload: dict[str, object] = {
        "id": "f1",
        "steps": [{"action": "goto", "target": "/"}],
        "expect": [{"kind": "visible", "target": ".app"}],
    }
    payload.update(overrides)
    return UiFlow.model_validate(payload)


def screen(**elements: dict) -> ScriptedDriver:
    return ScriptedDriver(elements=elements, url="http://host/")


def runner(driver: ScriptedDriver, artifacts: Path | None = None) -> UiRunner:
    return UiRunner(driver_factory=lambda: driver, artifacts_dir=artifacts)


def test_flow_requires_steps_and_assertions() -> None:
    with pytest.raises(ValueError):
        UiFlow.model_validate({"id": "f1", "expect": [{"kind": "visible", "target": ".a"}]})
    with pytest.raises(ValueError):
        UiFlow.model_validate({"id": "f1", "steps": [{"action": "goto", "target": "/"}]})


def test_unknown_action_and_assertion_are_rejected() -> None:
    with pytest.raises(ValueError):
        UiFlow.model_validate(
            {
                "id": "f1",
                "steps": [{"action": "hover", "target": "/"}],
                "expect": [{"kind": "visible", "target": ".a"}],
            }
        )
    with pytest.raises(ValueError):
        UiFlow.model_validate(
            {
                "id": "f1",
                "steps": [{"action": "goto", "target": "/"}],
                "expect": [{"kind": "pixel", "target": ".a"}],
            }
        )


def test_resolve_url_uses_base_for_relative_targets() -> None:
    assert resolve_url("/work", "http://host") == "http://host/work"
    assert resolve_url("http://other/x", "http://host") == "http://other/x"
    assert resolve_url("/work", "") == "/work"


def test_load_example_flows() -> None:
    flows = load_flows(EXAMPLES / "ui_flows.json")
    assert flows[0].id == "console-tabs-render"
    assert flows[0].max_steps == 5


def test_passing_flow_reports_no_screenshot(tmp_path: Path) -> None:
    driver = screen(**{".app": {"visible": True}})
    verdict, artifact = runner(driver, tmp_path / "ui").run_flow(flow())
    assert verdict.status is Status.PASS
    assert artifact == {}
    assert list((tmp_path / "ui").glob("*.png")) == []
    assert driver.actions[0] == ("goto", "/", None)


def test_failing_assertion_is_fail_and_captures_a_screenshot(tmp_path: Path) -> None:
    driver = screen(**{".app": {"visible": False}})
    verdict, artifact = runner(driver, tmp_path / "ui").run_flow(flow())
    assert verdict.status is Status.FAIL
    assert [check.name for check in verdict.failed_checks] == ["ui.visible"]
    assert artifact["screenshot"].endswith("f1.png")
    assert (tmp_path / "ui" / "f1.png").is_file()
    assert "ui.screenshot" in [check.name for check in verdict.checks]


def test_step_exception_is_error_not_fail() -> None:
    driver = screen()
    driver.failures["/"] = RuntimeError("page did not load")
    verdict, _ = runner(driver).run_flow(flow())
    assert verdict.status is Status.ERROR
    assert "page did not load" in (verdict.error or "")
    assert verdict.failed_checks[0].name == "ui.steps"


def test_text_count_and_url_assertions() -> None:
    driver = ScriptedDriver(
        elements={".list": {"count": 3}, ".title": {"text": "诊断报告"}},
        url="http://host/report",
    )
    verdict, _ = runner(driver).run_flow(
        flow(
            steps=[{"action": "goto", "target": "/report"}],
            expect=[
                {"kind": "count", "target": ".list", "expected": 3},
                {"kind": "text", "target": ".title", "expected": "报告"},
                {"kind": "url", "target": "", "expected": "/report"},
            ]
        )
    )
    assert verdict.status is Status.PASS


def test_screenshot_failure_does_not_change_the_verdict() -> None:
    class BrokenScreenshot(ScriptedDriver):
        def screenshot(self, path: Path) -> None:
            raise RuntimeError("no space left")

    driver = BrokenScreenshot(elements={".app": {"visible": False}})
    verdict, artifact = runner(driver, Path("unused")).run_flow(flow())
    assert verdict.status is Status.FAIL
    assert "no space left" in artifact["screenshot_error"]


def test_budget_overrun_fails_the_flow() -> None:
    ok_driver = screen(**{".app": {"visible": True}})
    verdict, _ = runner(ok_driver).run_flow(flow(max_steps=1))
    assert verdict.status is Status.PASS

    over = flow(max_duration_ms=0.0001)
    verdict, _ = runner(screen(**{".app": {"visible": True}})).run_flow(over)
    assert verdict.status is Status.FAIL
    assert any(check.name == "ui.max_duration_ms" for check in verdict.failed_checks)


def test_driver_unavailable_becomes_an_error_verdict() -> None:
    def broken() -> ScriptedDriver:
        raise DriverUnavailable("install playwright")

    verdict, _ = UiRunner(driver_factory=broken).run_flow(flow())
    assert verdict.status is Status.ERROR
    assert "install playwright" in (verdict.error or "")


def test_unknown_driver_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown driver"):
        build_driver_factory("selenium")


def test_runner_writes_a_run_record(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    driver = screen(**{".app": {"visible": True}})
    run = UiRunner(
        driver_factory=lambda: driver, store=store, artifacts_dir=tmp_path / "ui"
    ).run([flow(), flow(id="f2")], run_id="ui-run")
    assert run.summary.passed == 2
    assert len(run.metadata["ui_artifacts"]) == 2
    assert store.load("ui-run").summary.total == 2


def test_cli_reports_missing_flow_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--home", str(tmp_path / "home"), "ui", "run", "--flows", str(tmp_path / "no.json")]
    )
    assert code == 2
    assert "flow file not found" in capsys.readouterr().err


@pytest.mark.skipif(not PLAYWRIGHT_READY, reason="playwright is an optional dependency")
def test_playwright_driver_runs_against_the_console() -> None:
    """真实浏览器驱动：对控制台执行一次流程，验证断言与截图链路。"""

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8000)) != 0:
            pytest.skip("console is not running on 127.0.0.1:8000")

    flows = load_flows(EXAMPLES / "ui_flows.json")
    real_runner = UiRunner(driver_factory=lambda: PlaywrightDriver(headless=True))
    verdict, _ = real_runner.run_flow(flows[0])
    assert verdict.status is Status.PASS, [check.message for check in verdict.failed_checks]
    assert all(check.passed for check in verdict.checks)
