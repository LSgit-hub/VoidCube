from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Any, Literal

from .catalog import resolve_command


DynamicRouteKind = Literal[
    "custom_exec",
    "custom_invalid",
    "plugin",
    "skill",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ParsedCliCommand:
    original: str
    normalized: str
    base_token: str
    name: str
    canonical: str
    arguments: str


@dataclass(frozen=True, slots=True)
class DynamicCommandRoute:
    kind: DynamicRouteKind
    request: ParsedCliCommand
    executable: str = ""
    custom_type: str = ""


def looks_like_slash_command(text: str) -> bool:
    """Return whether text is a slash command rather than an absolute path."""
    if not text or not text.startswith("/"):
        return False
    first_word = text.split()[0]
    return "/" not in first_word[1:]


def parse_cli_command(command: str) -> ParsedCliCommand:
    """Normalize a CLI command while preserving argument spelling and spacing."""
    original = command.strip()
    normalized = original.lower()
    parts = original.split(maxsplit=1)
    raw_base = parts[0] if parts else ""
    base_token = raw_base.lower()
    name = base_token.lstrip("/")
    command_def = resolve_command(name)
    canonical = command_def.name if command_def else name
    arguments = parts[1].strip() if len(parts) > 1 else ""
    return ParsedCliCommand(
        original=original,
        normalized=normalized,
        base_token=base_token,
        name=name,
        canonical=canonical,
        arguments=arguments,
    )


def slow_command_status(command: str | ParsedCliCommand) -> str:
    request = command if isinstance(command, ParsedCliCommand) else parse_cli_command(command)
    normalized = request.normalized
    if normalized.startswith("/skills search"):
        return "正在搜索技能……"
    if normalized.startswith("/skills browse"):
        return "正在加载技能……"
    if normalized.startswith("/skills inspect"):
        return "正在查看技能……"
    if normalized.startswith("/skills install"):
        return "正在安装技能……"
    if request.canonical == "skills":
        return "正在处理技能命令……"
    if request.canonical == "reload-mcp":
        return "正在重新加载 MCP 服务……"
    if request.canonical == "browser":
        return "正在配置浏览器……"
    return "正在处理命令……"


def resolve_dynamic_command(
    request: ParsedCliCommand,
    *,
    custom_commands: Mapping[str, Mapping[str, Any]],
    plugin_names: Set[str],
    skill_commands: Mapping[str, Any],
) -> DynamicCommandRoute:
    """Resolve non-built-in CLI command sources in execution priority order."""
    custom = custom_commands.get(request.name)
    if custom is not None:
        custom_type = str(custom.get("type") or "")
        if custom_type == "exec":
            executable = str(custom.get("command") or "")
            return DynamicCommandRoute(
                "custom_exec" if executable else "custom_invalid",
                request,
                executable=executable,
                custom_type=custom_type,
            )
        return DynamicCommandRoute(
            "custom_invalid",
            request,
            custom_type=custom_type,
        )

    if request.name in plugin_names:
        return DynamicCommandRoute("plugin", request)

    if request.base_token in skill_commands:
        return DynamicCommandRoute("skill", request)

    return DynamicCommandRoute("unknown", request)
