"""
插件系统 - 管理 VoidCube 工具和命令的插件式扩展。

支持通过 PluginManager 注册:
  - Hook 回调 (事件驱动)
  - 工具集 (toolsets)
  - 命令处理器
  - 插件元数据
"""

from typing import Any, Callable, Dict, List, Optional
import json
import subprocess
from pathlib import Path

import requests

from VoidCube_app.plugins import (
    PluginManager,
    get_plugin_manager,
    invoke_hook,
)

_plugin_manager = get_plugin_manager()


def _plugin_http_request(args: dict, **_kwargs: Any) -> str:
    url = str(args.get("url") or "").strip()
    if not url:
        return json.dumps({"success": False, "error": "url is required"}, ensure_ascii=False)
    method = str(args.get("method") or "GET").upper()
    timeout = max(1, min(120, int(args.get("timeout") or 30)))
    headers = dict(args.get("headers") or {})
    body = args.get("body")
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            data=body,
            timeout=timeout,
            allow_redirects=bool(args.get("follow_redirects", True)),
        )
        try:
            payload = response.json()
        except ValueError:
            payload = response.text[:20000]
        return json.dumps(
            {"success": response.ok, "status_code": response.status_code, "body": payload},
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def _plugin_append_file(args: dict, **_kwargs: Any) -> str:
    raw_path = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    if not raw_path:
        return json.dumps({"success": False, "error": "path is required"}, ensure_ascii=False)
    path = Path(raw_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_bytes() if path.exists() else b""
        separator = b"\n" if args.get("ensure_newline", True) and existing and not existing.endswith((b"\n", b"\r")) else b""
        with path.open("ab") as stream:
            stream.write(separator + content.encode("utf-8"))
        return json.dumps({"success": True, "path": str(path), "bytes_appended": len(separator) + len(content.encode("utf-8"))}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def _plugin_git_manage(args: dict, **_kwargs: Any) -> str:
    action = str(args.get("action") or "status").strip().lower()
    allowed = {"status": ["status", "--short"], "diff": ["diff"], "diff_staged": ["diff", "--staged"], "log": ["log", "--oneline", f"-{max(1, min(50, int(args.get('limit') or 10)))}"], "remote_status": ["status", "-sb"]}
    command = allowed.get(action)
    if command is None:
        return json.dumps({"success": False, "error": f"git action not enabled: {action}"}, ensure_ascii=False)
    try:
        result = subprocess.run(["git", *command], capture_output=True, text=True, timeout=30, check=False)
        return json.dumps({"success": result.returncode == 0, "returncode": result.returncode, "output": (result.stdout + result.stderr)[:20000]}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def _plugin_browser(args: dict, **_kwargs: Any) -> str:
    from tools import browser_tool

    action = str(args.get("action") or "snapshot").strip().lower()
    task_id = args.get("task_id")
    handlers = {
        "navigate": lambda: browser_tool.browser_navigate(str(args.get("url") or ""), task_id),
        "click": lambda: browser_tool.browser_click(str(args.get("selector") or args.get("ref") or ""), task_id),
        "type": lambda: browser_tool.browser_type(str(args.get("selector") or args.get("ref") or ""), str(args.get("text") or ""), task_id),
        "scroll": lambda: browser_tool.browser_scroll(str(args.get("direction") or "down"), task_id),
        "back": lambda: browser_tool.browser_back(task_id),
        "evaluate": lambda: browser_tool.browser_console(expression=str(args.get("script") or ""), task_id=task_id),
        "snapshot": lambda: browser_tool.browser_snapshot(task_id=task_id),
    }
    handler = handlers.get(action)
    if handler is None:
        return json.dumps({"success": False, "error": f"browser action not supported: {action}"}, ensure_ascii=False)
    try:
        return handler()
    except Exception as exc:
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def _register_executable_plugin_tools() -> None:
    from tools.registry import registry
    from tools.toolsets import create_custom_toolset
    from VoidCube_cli.tools import http_tool, browser_tool, append_file_tool, git_tool

    git_schema = dict(git_tool.TOOL_SCHEMA)
    git_schema["parameters"] = dict(git_schema["parameters"])
    git_schema["parameters"]["properties"] = dict(
        git_schema["parameters"]["properties"]
    )
    git_schema["parameters"]["properties"]["action"] = {
        "type": "string",
        "enum": ["status", "diff", "diff_staged", "log", "remote_status"],
        "description": "Read-only Git inspection action.",
    }
    browser_schema = dict(browser_tool.TOOL_SCHEMA)
    browser_schema["parameters"] = dict(browser_schema["parameters"])
    browser_schema["parameters"]["properties"] = dict(
        browser_schema["parameters"]["properties"]
    )
    browser_schema["parameters"]["properties"]["action"] = {
        "type": "string",
        "enum": ["navigate", "click", "type", "scroll", "back", "evaluate", "snapshot"],
        "description": "Browser action to perform.",
    }
    adapters = {
        "http_request": (http_tool.TOOL_SCHEMA, _plugin_http_request, "http_request"),
        "browser": (browser_schema, _plugin_browser, "browser_legacy"),
        "append_file": (append_file_tool.TOOL_SCHEMA, _plugin_append_file, "append_file"),
        "git_manage": (git_schema, _plugin_git_manage, "git"),
    }
    for name, (schema, handler, toolset) in adapters.items():
        registry.register(name=name, toolset=toolset, schema={k: v for k, v in schema.items() if k != "name"}, handler=handler)
        create_custom_toolset(
            name=toolset,
            description=str(schema.get("description") or ""),
            tools=[name],
        )

def discover_plugins() -> List[str]:
    """Discover and auto-register built-in toolsets from VoidCube_cli/tools/."""
    discovered = []
    try:
        from VoidCube_cli.tools import list_tools, get_tool_schema
        _register_executable_plugin_tools()

        _plugin_manager.register_toolset("http_request", {
            "name": "http_request",
            "label": "🌐 HTTP请求",
            "description": "REST API调用 (GET/POST/PUT/DELETE)",
            "tools": ["http_request"],
        })
        _plugin_manager.register_toolset("browser_legacy", {
            "name": "browser_legacy",
            "label": "🧭 浏览器控制",
            "description": "无头浏览器自动化操作",
            "tools": ["browser"],
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

        discovered = ["http_request", "browser_legacy", "append_file", "git"]
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
