"""容器沙箱驱动。

把一组 compose 服务当成评测环境的沙箱：可以启动、检查健康、快照数据卷、按策略
重置到已知状态。评测在坏环境上跑只会产出一批看起来像模型问题的假失败，因此
启动前的健康门禁与用例之间的重置都属于这个能力的核心，而不是可选装饰。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_HEALTH_TIMEOUT_S = 120.0
DEFAULT_POLL_INTERVAL_S = 2.0
DEFAULT_COMMAND_TIMEOUT_S = 300.0
SNAPSHOT_IMAGE = "alpine:3"

ResetStrategy = Literal["recreate", "snapshot", "none"]
TeardownPolicy = Literal["keep", "down"]


class SandboxError(RuntimeError):
    """沙箱类错误，消息可直接展示给使用者。"""


class SandboxSpec(BaseModel):
    """一个 compose 级沙箱的定义。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    project: str = Field(min_length=1)
    compose_file: Path
    services: list[str] = Field(default_factory=list)
    health_services: list[str] = Field(default_factory=list)
    volumes: list[str] = Field(default_factory=list)
    reset: ResetStrategy = "recreate"
    snapshot: str | None = None
    teardown: TeardownPolicy = "keep"
    snapshot_dir: Path | None = None
    health_timeout_s: float = Field(default=DEFAULT_HEALTH_TIMEOUT_S, gt=0)
    poll_interval_s: float = Field(default=DEFAULT_POLL_INTERVAL_S, gt=0)
    command_timeout_s: float = Field(default=DEFAULT_COMMAND_TIMEOUT_S, gt=0)

    @model_validator(mode="after")
    def _validate_snapshot_strategy(self) -> "SandboxSpec":
        if self.reset == "snapshot" and self.snapshot is None:
            raise ValueError("reset strategy 'snapshot' requires a snapshot name")
        return self


def load_sandbox_spec(path: Path) -> SandboxSpec:
    """载入沙箱定义，相对路径按定义文件所在目录解析。"""

    path = Path(path)
    text = path.read_text(encoding="utf-8")
    payload: Any = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if isinstance(payload, dict) and "sandbox" in payload:
        payload = payload["sandbox"]
    if not isinstance(payload, dict):
        raise ValueError(f"sandbox definition must be an object: {path}")
    spec = SandboxSpec.model_validate(payload)
    base = path.parent
    compose_file = spec.compose_file
    if not compose_file.is_absolute():
        compose_file = (base / compose_file).resolve()
    snapshot_dir = spec.snapshot_dir
    if snapshot_dir is not None and not snapshot_dir.is_absolute():
        snapshot_dir = (base / snapshot_dir).resolve()
    return spec.model_copy(update={"compose_file": compose_file, "snapshot_dir": snapshot_dir})


@dataclass
class CommandResult:
    """一次外部命令的执行结果。"""

    returncode: int
    stdout: str = ""
    stderr: str = ""
    command: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@runtime_checkable
class CommandRunner(Protocol):
    """外部命令执行器，测试可替换为确定性实现。"""

    def run(self, args: Sequence[str], timeout_s: float) -> CommandResult:
        ...


@dataclass
class SubprocessRunner:
    """真实命令执行器。"""

    def run(self, args: Sequence[str], timeout_s: float) -> CommandResult:
        command = [str(item) for item in args]
        if shutil.which(command[0]) is None and not Path(command[0]).exists():
            raise SandboxError(f"command not found: {command[0]}")
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(
                f"command timed out after {timeout_s:g}s: {' '.join(command)}"
            ) from exc
        except OSError as exc:
            raise SandboxError(f"could not run {command[0]!r}: {exc}") from exc
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            command=command,
        )


def _parse_ps(output: str) -> list[dict[str, Any]]:
    """解析 ``docker compose ps --format json``，兼容数组与逐行两种形态。"""

    text = output.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return [payload] if isinstance(payload, dict) else []


@dataclass
class DockerSandbox:
    """对一个 compose 项目执行沙箱操作。"""

    spec: SandboxSpec
    runner: CommandRunner = field(default_factory=SubprocessRunner)

    # ---------------------------------------------------------------- 命令封装

    def _base(self) -> list[str]:
        return [
            "docker",
            "compose",
            "-f",
            str(self.spec.compose_file),
            "-p",
            self.spec.project,
        ]

    def _run(self, args: Sequence[str], timeout_s: float | None = None) -> CommandResult:
        return self.runner.run(list(args), timeout_s or self.spec.command_timeout_s)

    def _checked(self, args: Sequence[str], timeout_s: float | None = None) -> CommandResult:
        result = self._run(args, timeout_s)
        if not result.ok:
            detail = (result.stderr or result.stdout).strip()[:400]
            raise SandboxError(
                f"command failed ({result.returncode}): {' '.join(result.command)}: {detail}"
            )
        return result

    # ---------------------------------------------------------------- 生命周期

    def up(self) -> None:
        self._checked(self._base() + ["up", "-d", *self.spec.services])

    def down(self, remove_volumes: bool = False) -> None:
        args = self._base() + ["down"]
        if remove_volumes:
            args.append("-v")
        self._checked(args)

    def rows(self) -> list[dict[str, Any]]:
        result = self._checked(self._base() + ["ps", "--format", "json"])
        return _parse_ps(result.stdout)

    def _expected_services(self, rows: Sequence[dict[str, Any]]) -> list[str]:
        declared = set(self.spec.services) | set(self.spec.health_services)
        if declared:
            return sorted(declared)
        found = sorted({str(row.get("Service", "")) for row in rows if row.get("Service")})
        return found or ["<no services>"]

    def health(self) -> tuple[bool, list[dict[str, str]]]:
        """返回 (是否就绪, 未就绪明细)。"""

        rows = self.rows()
        by_service = {str(row.get("Service", "")): row for row in rows}
        problems: list[dict[str, str]] = []
        for name in self._expected_services(rows):
            row = by_service.get(name)
            if row is None:
                problems.append({"service": name, "state": "missing", "health": ""})
                continue
            state = str(row.get("State", "")).lower()
            health = str(row.get("Health", "")).lower()
            if state != "running":
                problems.append({"service": name, "state": state or "unknown", "health": health})
            elif name in self.spec.health_services and health != "healthy":
                problems.append(
                    {"service": name, "state": state, "health": health or "no healthcheck"}
                )
        return (not problems), problems

    def wait_until_healthy(self) -> None:
        deadline = time.monotonic() + self.spec.health_timeout_s
        problems: list[dict[str, str]] = []
        while True:
            ok, problems = self.health()
            if ok:
                return
            if time.monotonic() >= deadline:
                raise SandboxError(
                    f"sandbox '{self.spec.project}' was not healthy within "
                    f"{self.spec.health_timeout_s:g}s: {problems}"
                )
            time.sleep(self.spec.poll_interval_s)

    # ---------------------------------------------------------------- 数据卷

    def volumes(self) -> list[str]:
        if self.spec.volumes:
            prefix = f"{self.spec.project}_"
            return [
                name if name.startswith(prefix) else f"{prefix}{name}"
                for name in self.spec.volumes
            ]
        result = self._checked(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={self.spec.project}",
                "--format",
                "{{.Name}}",
            ]
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def snapshot_dir(self) -> Path:
        if self.spec.snapshot_dir is not None:
            return self.spec.snapshot_dir
        return self.spec.compose_file.parent / ".agenteval" / "sandboxes"

    def snapshot(self, name: str | None = None) -> dict[str, Any]:
        """把数据卷打包成可恢复的快照。"""

        name = name or self.spec.snapshot or "default"
        target = self.snapshot_dir() / name
        target.mkdir(parents=True, exist_ok=True)
        volumes = self.volumes()
        if not volumes:
            raise SandboxError(
                f"sandbox '{self.spec.project}' has no volumes to snapshot"
            )
        self._checked(self._base() + ["stop"])
        try:
            for volume in volumes:
                self._checked(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{volume}:/data:ro",
                        "-v",
                        f"{target}:/backup",
                        SNAPSHOT_IMAGE,
                        "tar",
                        "czf",
                        f"/backup/{volume}.tgz",
                        "-C",
                        "/data",
                        ".",
                    ]
                )
        finally:
            self._checked(self._base() + ["start"])
        manifest = {
            "sandbox": self.spec.id,
            "project": self.spec.project,
            "name": name,
            "volumes": volumes,
            "compose_sha256": self.compose_hash(),
        }
        (target / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def restore(self, name: str | None = None) -> dict[str, Any]:
        """从快照恢复数据卷。"""

        name = name or self.spec.snapshot or "default"
        target = self.snapshot_dir() / name
        manifest_path = target / "manifest.json"
        if not manifest_path.exists():
            raise SandboxError(
                f"snapshot {name!r} was not found at {manifest_path}; create one first"
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        volumes = [str(item) for item in manifest.get("volumes", [])]
        self._checked(self._base() + ["down"])
        for volume in volumes:
            archive = f"/backup/{volume}.tgz"
            self._checked(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{volume}:/data",
                    "-v",
                    f"{target}:/backup",
                    SNAPSHOT_IMAGE,
                    "sh",
                    "-c",
                    f"rm -rf /data/* /data/.[!.]* 2>/dev/null; tar xzf {archive} -C /data",
                ]
            )
        self.up()
        return manifest

    def reset(self) -> None:
        """按定义的重置策略把沙箱恢复到已知状态。"""

        if self.spec.reset == "none":
            self.wait_until_healthy()
            return
        if self.spec.reset == "recreate":
            self._checked(self._base() + ["down", "-v"])
            self.up()
            self.wait_until_healthy()
            return
        name = self.spec.snapshot or "default"
        if not (self.snapshot_dir() / name / "manifest.json").exists():
            raise SandboxError(
                f"reset strategy is 'snapshot' but snapshot {name!r} does not exist; "
                f"create it with 'sandbox snapshot' first"
            )
        self.restore(name)
        self.wait_until_healthy()

    # ---------------------------------------------------------------- 版本

    def compose_hash(self) -> str:
        return hashlib.sha256(self.spec.compose_file.read_bytes()).hexdigest()

    def version(self) -> dict[str, Any]:
        images: list[str] = []
        try:
            images = sorted(
                {
                    str(row.get("Image", ""))
                    for row in self.rows()
                    if row.get("Image")
                }
            )
        except SandboxError:
            images = []
        return {
            "sandbox": self.spec.id,
            "project": self.spec.project,
            "compose_file": str(self.spec.compose_file),
            "compose_sha256": self.compose_hash(),
            "reset_strategy": self.spec.reset,
            "images": images,
        }


@dataclass
class SandboxSession:
    """把沙箱接到评测运行的生命周期上。"""

    sandbox: DockerSandbox

    def ensure_ready(self) -> None:
        self.sandbox.up()
        self.sandbox.wait_until_healthy()

    def before_case(self, case_id: str) -> None:
        self.sandbox.reset()

    def close(self) -> None:
        if self.sandbox.spec.teardown == "down":
            self.sandbox.down()

    def describe(self) -> dict[str, Any]:
        return self.sandbox.version()
