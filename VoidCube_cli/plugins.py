"""
插件系统 - 管理 VoidCube 工具和命令的插件式扩展。

支持通过 PluginManager 注册:
  - Hook 回调 (事件驱动)
  - 工具集 (toolsets)
  - 命令处理器
  - 插件元数据
"""

from typing import Any, Callable, Dict, List, Optional

from VoidCube_app.plugins import (
    PluginManager,
    get_plugin_manager,
    invoke_hook,
)

_plugin_manager = get_plugin_manager()

def discover_plugins() -> List[str]:
    """Discover and auto-register built-in toolsets from VoidCube_cli/tools/."""
    discovered = []
    try:
        from VoidCube_cli.tools import list_tools, get_tool_schema
        from VoidCube_cli.tools import (http_tool, browser_tool, vision_tool,
                                         append_file_tool, git_tool)

        for mod in (http_tool, browser_tool, vision_tool, append_file_tool,
                     git_tool):
            try:
                mod.register()
            except Exception:
                pass

        _plugin_manager.register_toolset("http_request", {
            "name": "http_request",
            "label": "🌐 HTTP请求",
            "description": "REST API调用 (GET/POST/PUT/DELETE)",
            "tools": ["http_request"],
        })
        _plugin_manager.register_toolset("browser", {
            "name": "browser",
            "label": "🧭 浏览器控制",
            "description": "无头浏览器自动化操作",
            "tools": ["browser"],
        })
        _plugin_manager.register_toolset("vision", {
            "name": "vision",
            "label": "👁️ 视觉分析",
            "description": "图像分析、OCR、截图理解",
            "tools": ["vision_analyze"],
        })
        _plugin_manager.register_toolset("append_file", {
            "name": "append_file",
            "label": "📎 文件追加",
            "description": "原子追加写入文件（避免大文件O(n)读开销）",
            "tools": ["append_file"],
        })
        _plugin_manager.register_toolset("git", {
            "name": "git",
            "label": "📋 Git版本管理",
            "description": "本地Git操作（提交/分支/回退/审计）",
            "tools": ["git_manage"],
        })

        discovered = ["http_request", "browser", "vision",
                       "append_file", "git"]
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
