"""运行结果的持久化。

每次运行写入一个独立目录：``summary.json`` 保存运行元信息与汇总计数，
``verdicts.jsonl`` 与 ``traces.jsonl`` 逐条保存判定与轨迹。JSONL 便于后续
把失败样本回流成新的评测用例。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agenteval.models import Run, Trace, Verdict

HOME_ENV_VAR = "AGENTEVAL_HOME"
DEFAULT_HOME_DIRNAME = ".agenteval"
RUNS_DIRNAME = "runs"
SUMMARY_FILENAME = "summary.json"
VERDICTS_FILENAME = "verdicts.jsonl"
TRACES_FILENAME = "traces.jsonl"


def resolve_home(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析运行根目录：环境变量优先，其次为 ``<base>/.agenteval``。"""

    environ = os.environ if env is None else env
    override = environ.get(HOME_ENV_VAR)
    if override:
        return Path(override)
    return (Path(base) if base is not None else Path.cwd()) / DEFAULT_HOME_DIRNAME


def resolve_runs_dir(
    base: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """解析运行目录，即 ``<home>/runs``。"""

    return resolve_home(base=base, env=env) / RUNS_DIRNAME


class RunStore:
    """基于文件系统的运行记录存储。"""

    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = Path(runs_dir)

    @classmethod
    def default(
        cls,
        base: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> "RunStore":
        return cls(resolve_runs_dir(base=base, env=env))

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def save(self, run: Run) -> Path:
        """写入一次运行，返回该运行的目录。目录不存在时自动创建。"""

        target = self.run_dir(run.run_id)
        target.mkdir(parents=True, exist_ok=True)
        metadata = run.model_dump(mode="json", exclude={"verdicts", "traces"})
        _write_text(target / SUMMARY_FILENAME, json.dumps(metadata, ensure_ascii=False, indent=2))
        _write_jsonl(target / VERDICTS_FILENAME, (v.model_dump(mode="json") for v in run.verdicts))
        _write_jsonl(target / TRACES_FILENAME, (t.model_dump(mode="json") for t in run.traces))
        return target

    def load(self, run_id: str) -> Run:
        """按运行标识恢复完整运行记录。"""

        target = self.run_dir(run_id)
        summary_path = target / SUMMARY_FILENAME
        if not summary_path.is_file():
            raise FileNotFoundError(f"run not found: {run_id}")
        metadata: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
        verdicts = [Verdict.model_validate(item) for item in _read_jsonl(target / VERDICTS_FILENAME)]
        traces = [Trace.model_validate(item) for item in _read_jsonl(target / TRACES_FILENAME)]
        return Run.model_validate({**metadata, "verdicts": verdicts, "traces": traces})

    def list_runs(self) -> list[str]:
        """按名称排序列出已有运行标识。"""

        if not self.runs_dir.is_dir():
            return []
        return sorted(entry.name for entry in self.runs_dir.iterdir() if entry.is_dir())


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False))
            handle.write("\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            records.append(json.loads(stripped))
    return records
