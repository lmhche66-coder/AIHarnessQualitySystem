"""示例工具注册表，供 ``--registry examples/registry.py:build_registry`` 使用。"""

from __future__ import annotations

from agenteval.fakes import build_demo_registry
from agenteval.tools import ToolRegistry


def build_registry() -> ToolRegistry:
    return build_demo_registry()
