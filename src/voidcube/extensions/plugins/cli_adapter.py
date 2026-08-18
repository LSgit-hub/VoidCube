"""
插件系统 - 管理 VoidCube 工具和命令的插件式扩展。

支持通过 PluginManager 注册:
  - Hook 回调 (事件驱动)
  - 工具集 (toolsets)
  - 命令处理器
  - 插件元数据
"""

from typing import Any, Callable, Dict, List, Optional

from .manager import (
    PluginManager,
    get_plugin_manager,
    invoke_hook,
)

_plugin_manager = get_plugin_manager()


def _register_executable_plugin_tools() -> None:
    """Register executable plugin tools with the tool registry.

    Previously registered legacy tools (http_request, browser, append_file,
    git_manage) that were superseded by the main tool system.  No-op now —
    kept as an extension point for future plugin-provided tools.
    """


def discover_plugins() -> List[str]:
    """Discover and auto-register built-in toolsets from VoidCube_cli/tools/."""
    discovered: List[str] = []
    try:
        _register_executable_plugin_tools()
    except Exception:
        pass
    return discovered


def get_plugin_toolsets() -> List[tuple]:
    """Return plugin-provided toolsets as (key, label, description) tuples."""
    toolsets = []
    for name, info in _plugin_manager.get_toolsets().items():
        if isinstance(info, dict):
            toolsets.append((name, info.get("label", name), info.get("description", "")))
    return toolsets


def get_plugin_command_handler(name: str) -> Optional[Callable]:
    return _plugin_manager.get_command_handler(name)


def list_plugins() -> Dict[str, Dict[str, Any]]:
    """List all registered plugins."""
    return _plugin_manager.list_plugins()


def get_plugin_context_engine(**kwargs) -> Optional[Any]:
    return None
