"""Display-state command handlers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class StatusBarCommandPorts:
    visible: Callable[[], bool]
    set_visible: Callable[[bool], None]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ConfigDisplayPorts:
    model: Callable[[], str]
    base_url: Callable[[], str]
    api_key: Callable[[], str | None]
    terminal_environment: Callable[[], str]
    terminal_working_directory: Callable[[], str]
    terminal_timeout: Callable[[], str]
    ssh_target: Callable[[], tuple[str, str, str]]
    max_turns: Callable[[], int]
    enabled_toolsets: Callable[[], Sequence[str] | None]
    verbose: Callable[[], bool]
    session_start: Callable[[], datetime]
    config_path: Callable[[], Path]
    translate: Callable[..., str]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ToolsetsDisplayPorts:
    toolsets: Callable[[], Sequence[tuple[str, int, str]]]
    enabled_toolsets: Callable[[], Sequence[str] | None]
    translate: Callable[..., str]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ToolsCatalogPorts:
    tools: Callable[[], Sequence[Mapping[str, Any]]]
    toolset_for_tool: Callable[[str], str | None]
    translate: Callable[..., str]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class SessionStatusDisplayPorts:
    session_metadata: Callable[[], Mapping[str, Any]]
    session_id: Callable[[], str]
    session_start: Callable[[], datetime]
    home_path: Callable[[], str]
    model: Callable[[], str | None]
    provider: Callable[[], str | None]
    total_tokens: Callable[[], int]
    agent_running: Callable[[], bool]
    subagent_snapshot: Callable[[], Mapping[str, Any]]
    autonomous_sections: Callable[[], Sequence[str]]
    emit: Callable[[str], None]


def handle_statusbar_command(
    request: ParsedCliCommand,
    *,
    ports: StatusBarCommandPorts,
) -> None:
    del request
    visible = not ports.visible()
    ports.set_visible(visible)
    ports.emit(f"  Status bar {'visible' if visible else 'hidden'}")


def handle_config_display_command(
    request: ParsedCliCommand,
    *,
    ports: ConfigDisplayPorts,
) -> None:
    """Render the active runtime configuration without mutating it."""
    del request
    config_path = ports.config_path()
    enabled_toolsets = ports.enabled_toolsets()
    api_key = ports.api_key()
    api_key_display = (
        "********" + api_key[-4:] if api_key and len(api_key) > 4 else "Not set!"
    )
    terminal_environment = ports.terminal_environment()
    title = "(^_^) Configuration"
    width = 50
    pad = width - len(title)
    lines = [
        "",
        "+" + "-" * width + "+",
        "|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|",
        "+" + "-" * width + "+",
        "",
        ports.translate("model"),
        f"  Model:     {ports.model()}",
        f"  Base URL:  {ports.base_url()}",
        f"  API Key:   {api_key_display}",
        "",
        ports.translate("terminal"),
        f"  Environment:  {terminal_environment}",
    ]
    if terminal_environment == "ssh":
        ssh_user, ssh_host, ssh_port = ports.ssh_target()
        lines.append(f"  SSH Target:   {ssh_user}@{ssh_host}:{ssh_port}")
    lines.extend(
        [
            f"  Working Dir:  {ports.terminal_working_directory()}",
            f"  Timeout:      {ports.terminal_timeout()}s",
            "",
            ports.translate("agent"),
            f"  Max Turns:  {ports.max_turns()}",
            f"  Toolsets:   {', '.join(enabled_toolsets) if enabled_toolsets else 'all'}",
            f"  Verbose:    {ports.verbose()}",
            "",
            ports.translate("session"),
            f"  Started:     {ports.session_start().strftime('%Y-%m-%d %H:%M:%S')}",
            f"  Config File: {config_path} {'(loaded)' if config_path.exists() else '(not found)'}",
            "",
        ]
    )
    for line in lines:
        ports.emit(line)


def handle_toolsets_display_command(
    request: ParsedCliCommand,
    *,
    ports: ToolsetsDisplayPorts,
) -> None:
    """Render available toolsets and the active selection without mutation."""
    del request
    enabled_toolsets = ports.enabled_toolsets()
    title = ports.translate(
        "prompts.available_toolsets_title", default="(^_^)b Available Toolsets"
    )
    width = 58
    pad = width - len(title)
    ports.emit("")
    ports.emit("+" + "-" * width + "+")
    ports.emit("|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|")
    ports.emit("+" + "-" * width + "+")
    ports.emit("")
    unit = ports.translate("prompts.toolsets_unit", default="工具")
    for name, tool_count, description in ports.toolsets():
        marker = "(*)" if enabled_toolsets and name in enabled_toolsets else "   "
        ports.emit(f"  {marker} {name:<18} [{tool_count:>2} {unit}] - {description}")
    ports.emit("")
    current_enabled = ", ".join(enabled_toolsets) if enabled_toolsets else "none"
    ports.emit(
        f"  {ports.translate('prompts.toolsets_current_enabled', default='Currently enabled toolsets:')} "
        f"{current_enabled}"
    )
    ports.emit("")
    ports.emit(
        f"  {ports.translate('prompts.toolsets_tip_all', default='Use --toolsets full to enable the full toolset.')}"
    )
    ports.emit(
        f"  {ports.translate('prompts.toolsets_example', default='Example: python cli.py --toolsets web,terminal,file')}"
    )
    ports.emit("")


def handle_tools_catalog_command(
    request: ParsedCliCommand,
    *,
    ports: ToolsCatalogPorts,
) -> None:
    """Render the available tool catalog without changing tool configuration."""
    del request
    tools = ports.tools()
    if not tools:
        ports.emit(ports.translate("prompts.no_tools_available"))
        return

    title = ports.translate(
        "prompts.available_tools_title", default="(^_^)/ Available Tools"
    )
    width = 78
    pad = width - len(title)
    ports.emit("")
    ports.emit("+" + "-" * width + "+")
    ports.emit("|" + " " * (pad // 2) + title + " " * (pad - pad // 2) + "|")
    ports.emit("+" + "-" * width + "+")
    ports.emit("")

    toolsets: dict[str, list[tuple[str, str]]] = {}
    for tool in sorted(tools, key=lambda value: str(value["function"]["name"])):
        function = tool["function"]
        name = str(function["name"])
        toolset = ports.toolset_for_tool(name) or "unknown"
        description = str(function.get("description", "")).split("\n", 1)[0]
        if ". " in description:
            description = description[: description.index(". ") + 1]
        toolsets.setdefault(toolset, []).append((name, description))

    for toolset in sorted(toolsets):
        ports.emit(f"  [{toolset}]")
        for name, description in toolsets[toolset]:
            ports.emit(f"    * {name:<20} - {description}")
        ports.emit("")

    ports.emit(f"  {ports.translate('prompts.total_tools', count=len(tools))}")
    ports.emit("")


def handle_session_status_command(
    request: ParsedCliCommand,
    *,
    ports: SessionStatusDisplayPorts,
) -> None:
    """Project session, subagent, and autonomous status through read ports."""
    del request
    metadata = ports.session_metadata()
    session_start = ports.session_start()
    created_at = _timestamp_or_default(metadata.get("started_at"), session_start)
    updated_at = created_at
    for field in ("updated_at", "last_updated_at", "last_activity_at"):
        value = metadata.get(field)
        if value:
            parsed = _timestamp_or_default(value, updated_at)
            if parsed is not updated_at:
                updated_at = parsed
                break

    title = str(metadata.get("title") or "").strip()
    subagent = ports.subagent_snapshot()
    lines = [
        "Voidcube CLI Status",
        "",
        f"Session ID: {ports.session_id()}",
        f"Path: {ports.home_path()}",
    ]
    if title:
        lines.append(f"Title: {title}")
    lines.extend(
        [
            f"Model: {ports.model() or '(unknown)'} ({ports.provider() or 'unknown'})",
            f"Created: {created_at.strftime('%Y-%m-%d %H:%M')}",
            f"Last Activity: {updated_at.strftime('%Y-%m-%d %H:%M')}",
            f"Tokens: {ports.total_tokens():,}",
            f"Agent Running: {'Yes' if ports.agent_running() else 'No'}",
        ]
    )
    if subagent.get("active"):
        lines.append(
            "Subagents: "
            f"{subagent.get('foreground_count', 0)} foreground"
            f", {subagent.get('background_count', 0)} background"
        )
        focus_preview = str(subagent.get("focus_preview") or "").strip()
        if focus_preview:
            lines.append(f"Subagent Focus: {focus_preview}")
    else:
        lines.append("Subagents: idle")
    lines.extend(ports.autonomous_sections())
    ports.emit("\n".join(lines))


def _timestamp_or_default(value: object, default: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OverflowError, OSError):
        return default
