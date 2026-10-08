from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agenteval.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ENVIRONMENT = "agenteval.fakes:build_task_registry"


def _write_agent_registry(path: Path) -> Path:
    """用当前解释器作为 command，避免依赖 PATH 上的 python。"""

    path.write_text(
        json.dumps(
            {
                "agents": [
                    {
                        "id": "module-agent",
                        "version": "0.1.0",
                        "transport": "subprocess",
                        "command": sys.executable,
                        "args": [str(EXAMPLES / "agents" / "module_agent.py")],
                        "capabilities": ["task"],
                        "timeout_s": 30,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_module_task_run_then_module_scorecard(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    registry = _write_agent_registry(tmp_path / "agents.json")

    code = main(
        [
            "--home",
            str(home),
            "--agents",
            str(registry),
            "task",
            "run",
            "--tasks",
            str(EXAMPLES / "module_tasks.json"),
            "--registry",
            ENVIRONMENT,
            "--agent",
            "@module-agent",
        ]
    )
    capsys.readouterr()
    assert code == 0

    assert main(["--home", str(home), "report", "--scope", "core_module", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["primary_metric"] == "task_completion"
    modules = {stat["module"]: stat for stat in payload["modules"]}
    assert set(modules) == {"perception", "planning", "memory"}
    assert modules["perception"]["pass_rate"] == 1.0
    assert modules["planning"]["pass_rate"] == 1.0
    # agent 上报的用量与耗时必须落盘，成本与性能维度不能是 0
    assert payload["cost"]["usage_reported"] is True
    assert payload["cost"]["input_tokens"] == 440
    assert payload["cost"]["output_tokens"] == 124
    assert payload["performance"]["total_duration_ms"] > 0
    assert {scene["scene"] for scene in payload["scenes"]} == {"Trace 排查", "领域知识问答"}
