"""Goal Manager plugin entrypoint."""

from __future__ import annotations

import importlib
from typing import Any

from voidcube.extensions.tools.registry import registry
from voidcube.extensions.tools.toolsets import create_custom_toolset


def activate(manager: Any, config: dict[str, Any]) -> None:
    """Register the Goal Manager toolset and its HTTP-backed tools."""
    del config
    module = importlib.import_module("plugins.goal_manager.tools.agent_tools")
    names = module.TOOL_NAMES
    toolset = "goal_manager"
    manager.register_toolset(
        toolset,
        {
            "label": "目标管理",
            "description": "目标、里程碑、依赖和审计管理工具集",
            "tools": list(names),
        },
    )
    create_custom_toolset(
        toolset,
        "目标、里程碑、依赖和审计管理工具集",
        list(names),
    )
    module.register_tools(registry)


def deactivate(manager: Any) -> None:
    """Remove only this plugin's registrations when an embedder supports it."""
    for name in getattr(importlib.import_module(
        "plugins.goal_manager.tools.agent_tools"
    ), "TOOL_NAMES", ()):
        registry.unregister(name)
    toolsets = getattr(manager, "_toolsets", None)
    if isinstance(toolsets, dict):
        toolsets.pop("goal_manager", None)
