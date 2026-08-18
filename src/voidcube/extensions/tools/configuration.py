"""Toolset configuration policy independent of terminal presentation.

The CLI wizard owns prompts and rendering.  This module owns the durable
platform-toolset rules: defaults, plugin toolsets, MCP passthrough entries,
and enable/disable mutations.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable

from ...infrastructure.config.provider_config import load_current_config, save_config
from ..plugins.manager import get_plugin_manager


CONFIGURABLE_TOOLSETS: tuple[tuple[str, str, str], ...] = (
    ("terminal", "💻 终端执行", "执行Shell命令、进程管理"),
    ("file", "📁 文件操作", "读写文件、搜索、补丁"),
    ("web", "🔍 Web搜索", "网络搜索、网页抓取"),
    ("playback", "▶ 媒体播放", "在 VoidCube Web UI 播放 B 站和直链音视频"),
    ("browser", "🌐 浏览器自动化", "网页导航、截图、点击、输入"),
    ("code_execution", "🔧 代码执行", "Python/Shell代码执行"),
)

# This registry is intentionally data-only.  Message platforms were retired;
# CLI is the only supported interactive platform in the current architecture.
PLATFORMS: OrderedDict[str, dict[str, str]] = OrderedDict(
    [("cli", {"label": "🖥️  CLI终端", "default_toolset": "VoidCube-cli"})]
)


def get_plugin_toolsets() -> list[tuple[str, str, str]]:
    """Return plugin toolset display records from the shared manager."""
    records: list[tuple[str, str, str]] = []
    for name, info in get_plugin_manager().get_toolsets().items():
        if isinstance(info, dict):
            records.append((name, str(info.get("label", name)), str(info.get("description", ""))))
    return records


def get_effective_configurable_toolsets() -> list[tuple[str, str, str]]:
    return [*CONFIGURABLE_TOOLSETS, *get_plugin_toolsets()]


def get_plugin_toolset_keys() -> set[str]:
    return {key for key, _, _ in get_plugin_toolsets()}


def get_enabled_platforms() -> list[str]:
    return list(PLATFORMS)


def platform_toolset_summary(
    config: dict[str, Any],
    platforms: Iterable[str] | None = None,
) -> dict[str, set[str]]:
    selected = list(PLATFORMS if platforms is None else platforms)
    return {platform: get_platform_tools(config, platform) for platform in selected}


def parse_enabled_flag(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def _resolve_toolset(name: str) -> list[str]:
    from .toolsets import resolve_toolset

    return resolve_toolset(name)


def _is_valid_toolset(name: str) -> bool:
    from .toolsets import is_valid_toolset

    return is_valid_toolset(name)


def get_platform_tools(
    config: dict[str, Any],
    platform: str,
    *,
    include_default_mcp_servers: bool = True,
) -> set[str]:
    """Resolve enabled toolsets while preserving legacy config semantics."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")

    platform_toolsets = config.get("platform_toolsets", {})
    toolset_names = platform_toolsets.get(platform) if isinstance(platform_toolsets, dict) else None
    if not isinstance(toolset_names, list):
        toolset_names = [PLATFORMS[platform]["default_toolset"]]
    toolset_names = [str(name) for name in toolset_names]

    configurable_keys = {key for key, _, _ in CONFIGURABLE_TOOLSETS}
    has_explicit_config = any(name in configurable_keys for name in toolset_names)
    if has_explicit_config:
        enabled_toolsets = {name for name in toolset_names if name in configurable_keys}
    else:
        all_tool_names: set[str] = set()
        for name in toolset_names:
            all_tool_names.update(_resolve_toolset(name))
        enabled_toolsets: set[str] = set()
        for key, _, _ in CONFIGURABLE_TOOLSETS:
            tool_names = set(_resolve_toolset(key))
            if tool_names and tool_names.issubset(all_tool_names):
                enabled_toolsets.add(key)
        enabled_toolsets.update(name for name in toolset_names if _is_valid_toolset(name))

    plugin_keys = get_plugin_toolset_keys()
    known_map = config.get("known_plugin_toolsets", {})
    known_for_platform = set(known_map.get(platform, [])) if isinstance(known_map, dict) else set()
    for key in plugin_keys:
        if key in toolset_names or key not in known_for_platform:
            enabled_toolsets.add(key)

    platform_defaults = {info["default_toolset"] for info in PLATFORMS.values()}
    explicit_passthrough = {
        name for name in toolset_names
        if name not in configurable_keys and name not in plugin_keys and name not in platform_defaults
    }

    mcp_servers = config.get("mcp_servers") or {}
    enabled_mcp_servers = {
        str(name)
        for name, server_cfg in mcp_servers.items()
        if isinstance(server_cfg, dict) and parse_enabled_flag(server_cfg.get("enabled", True))
    }
    if "no_mcp" in toolset_names:
        explicit_mcp_servers: set[str] = set()
        enabled_toolsets.update(explicit_passthrough - enabled_mcp_servers - {"no_mcp"})
    else:
        explicit_mcp_servers = explicit_passthrough & enabled_mcp_servers
        enabled_toolsets.update(explicit_passthrough - enabled_mcp_servers)
    if include_default_mcp_servers:
        enabled_toolsets.update(
            explicit_mcp_servers if explicit_mcp_servers or "no_mcp" in toolset_names else enabled_mcp_servers
        )
    else:
        enabled_toolsets.update(explicit_mcp_servers)
    return enabled_toolsets


def save_platform_tools(
    config: dict[str, Any],
    platform: str,
    enabled_toolset_keys: set[str],
) -> dict[str, Any]:
    """Persist selected toolsets and return the mutated config."""
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    platform_toolsets = config.setdefault("platform_toolsets", {})
    if not isinstance(platform_toolsets, dict):
        platform_toolsets = config["platform_toolsets"] = {}

    configurable_keys = {key for key, _, _ in CONFIGURABLE_TOOLSETS} | get_plugin_toolset_keys()
    platform_defaults = {info["default_toolset"] for info in PLATFORMS.values()}
    existing = platform_toolsets.get(platform, [])
    if not isinstance(existing, list):
        existing = []
    preserved = {
        str(entry) for entry in existing
        if str(entry) not in configurable_keys and str(entry) not in platform_defaults
    }
    platform_toolsets[platform] = sorted({str(name) for name in enabled_toolset_keys} | preserved)

    plugin_keys = get_plugin_toolset_keys()
    if plugin_keys:
        known = config.setdefault("known_plugin_toolsets", {})
        if isinstance(known, dict):
            known[platform] = sorted(plugin_keys)
    save_config(config)
    return config


def apply_toolset_change(config: dict[str, Any], platform: str, names: Iterable[str], action: str) -> dict[str, Any]:
    enabled = get_platform_tools(config, platform, include_default_mcp_servers=False)
    requested = {str(name) for name in names}
    updated = enabled - requested if action == "disable" else enabled | requested
    return save_platform_tools(config, platform, updated)


def apply_mcp_change(config: dict[str, Any], targets: Iterable[str], action: str) -> set[str]:
    """Update MCP tool filters and return server names missing from config."""
    failed: set[str] = set()
    mcp_servers = config.get("mcp_servers") or {}
    for target in targets:
        if ":" not in target:
            continue
        server_name, tool_name = target.split(":", 1)
        if server_name not in mcp_servers:
            failed.add(server_name)
            continue
        tools_cfg = mcp_servers[server_name].setdefault("tools", {})
        exclude = list(tools_cfg.get("exclude") or [])
        if action == "disable":
            if tool_name not in exclude:
                exclude.append(tool_name)
        else:
            exclude = [name for name in exclude if name != tool_name]
        tools_cfg["exclude"] = exclude
    return failed


__all__ = [
    "CONFIGURABLE_TOOLSETS",
    "PLATFORMS",
    "apply_mcp_change",
    "apply_toolset_change",
    "get_effective_configurable_toolsets",
    "get_enabled_platforms",
    "get_platform_tools",
    "get_plugin_toolset_keys",
    "get_plugin_toolsets",
    "parse_enabled_flag",
    "platform_toolset_summary",
    "save_platform_tools",
]
