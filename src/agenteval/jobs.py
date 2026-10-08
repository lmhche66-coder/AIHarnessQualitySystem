"""评测作业：异步提交、轮询进度与取消。

文章 §6.7：批量评测采用异步提交-轮询模式——客户端提交评测请求立即拿到一个任务
标识符（taskId），评测在后台异步执行，客户端轮询查询进度，并支持在运行中取消。

作业记录落在 ``<home>/jobs`` 下，每条一个文件，因此提交、轮询与取消都是独立的
进程间通信，进程退出不影响后台执行。真正的执行仍由 ``eval run --job <id>`` 完成。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

JOBS_DIRNAME = "jobs"
JOB_SUFFIX = ".json"

# 文章 §6.7：3 线程并行，在效率与 LLM API 限流之间权衡
DEFAULT_CONCURRENCY = 3
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_RETRIES = 2
DEFAULT_RETRY_INTERVAL_S = 3.0


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id() -> str:
    """生成按时间排序且唯一的任务标识符。"""

    from uuid import uuid4

    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid4().hex[:8]}"


class JobRecord(BaseModel):
    """一条评测作业记录，兼作客户端轮询的响应体。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # 评测请求
    scope: str
    eval_mode: str = "e2e_real"
    datasets_path: str
    demo: bool = False
    registry: str | None = None
    agent: str | None = None
    agents_path: str | None = None
    judge: str | None = None
    judge_tasks_path: str | None = None
    concurrency: int = DEFAULT_CONCURRENCY
    timeout_s: float | None = DEFAULT_TIMEOUT_S
    retries: int = DEFAULT_RETRIES
    retry_interval_s: float = DEFAULT_RETRY_INTERVAL_S
    turn_settle_s: float = 2.0

    # 结果
    run_id: str | None = None
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    primary_pass_rate: float | None = None
    error: str | None = None
    cancel_requested: bool = False


class JobStore:
    """每条作业一个文件；单文件读写让提交、轮询、取消彼此独立。"""

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = Path(jobs_dir)

    @classmethod
    def for_home(cls, home: Path) -> "JobStore":
        return cls(Path(home) / JOBS_DIRNAME)

    def path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}{JOB_SUFFIX}"

    def save(self, record: JobRecord) -> Path:
        record.updated_at = _now()
        path = self.path(record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        return path

    def load(self, job_id: str) -> JobRecord:
        path = self.path(job_id)
        if not path.is_file():
            raise FileNotFoundError(f"job not found: {job_id}")
        return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, **fields: Any) -> JobRecord:
        record = self.load(job_id)
        updated = record.model_copy(update=fields)
        self.save(updated)
        return updated

    def list(self) -> list[JobRecord]:
        if not self.jobs_dir.is_dir():
            return []
        records: list[JobRecord] = []
        for path in self.jobs_dir.iterdir():
            if not path.is_file() or not path.name.endswith(JOB_SUFFIX):
                continue
            try:
                records.append(JobRecord.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        records.sort(key=lambda record: record.created_at, reverse=True)
        return records

    def cancel(self, job_id: str) -> JobRecord:
        """请求取消：置位标志。后台执行在每条用例前检查该标志。"""

        record = self.load(job_id)
        if record.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return record
        return self.update(job_id, cancel_requested=True)


def spawn_worker(home: Path, job_id: str) -> int:
    """在后台启动一次评测执行；立即返回，不等它结束。"""

    argv = [
        sys.executable,
        "-m",
        "agenteval",
        "--home",
        str(home),
        "eval",
        "run",
        "--job",
        job_id,
    ]
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":  # pragma: no cover - Windows 分支
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    else:  # pragma: no cover - POSIX 分支
        kwargs["start_new_session"] = True
    process = subprocess.Popen(argv, **kwargs)
    return int(process.pid)
