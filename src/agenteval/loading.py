"""按引用载入工厂与工具注册表。

命令行与控制台共用同一套载入规则，避免两处对「合法的接入引用」有不同理解。
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from agenteval.tools import ToolRegistry


class LoadError(Exception):
    """载入失败，消息可直接展示给使用者。"""


def load_factory(ref: str | None, label: str) -> Callable[..., object]:
    """载入工厂函数本身，而不是它的调用结果。"""

    if not ref or ":" not in ref:
        raise LoadError(
            f"--{label} must look like 'package.module:factory' or 'path/to/module.py:factory'"
        )
    target, _, attribute = ref.rpartition(":")
    module = (
        _load_module(Path(target))
        if _looks_like_path(target)
        else importlib.import_module(target)
    )
    factory = getattr(module, attribute, None)
    if factory is None:
        raise LoadError(f"{label} factory not found: {attribute}")
    if not callable(factory):
        raise LoadError(f"{label} factory must be callable: {attribute}")
    return factory


def load_registry(ref: str | None) -> ToolRegistry:
    """按 ``module:factory`` 或 ``path/to/module.py:factory`` 载入工具注册表。"""

    factory = load_factory(ref, "registry")
    registry = factory()
    if not isinstance(registry, ToolRegistry):
        raise LoadError("registry factory must return a ToolRegistry instance")
    return registry


def _looks_like_path(target: str) -> bool:
    return target.endswith(".py") or "/" in target or "\\" in target


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise LoadError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
