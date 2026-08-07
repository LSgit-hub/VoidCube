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
class HelpDisplayText:
    header: str
    skill_commands_header: str
    tip_chat: str
    tip_multiline: str
    tip_paste: str


@dataclass(frozen=True, slots=True)
class HelpDisplayPorts:
    command_categories: Callable[[], Mapping[str, Mapping[str, str]]]
    command_available: Callable[[str], bool]
    skill_commands: Callable[[], Mapping[str, Mapping[str, str]]]
    text: HelpDisplayText
    is_termux: Callable[[], bool]
    termux_example_path: Callable[[], str]
    render_header: Callable[[str], None]
    render_category: Callable[[str], None]
    render_command: Callable[[str, str], None]
    render_skill_header: Callable[[str, int], None]
    render_skill: Callable[[str, str], None]
    render_tip: Callable[[str, bool], None]


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
    goal_snapshot: Callable[[], Mapping[str, Any]] = lambda: {}


@dataclass(frozen=True, slots=True)
class ProviderDisplaySnapshot:
    active_provider: str
    configured_providers: Sequence[Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ProviderDisplayPorts:
    snapshot: Callable[[], ProviderDisplaySnapshot]
    current_model: Callable[[], str]
    translate: Callable[..., str]
    emit: Callable[[str], None]
    emit_usage: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class MemoryDisplayPorts:
    database_path: Callable[[], str]
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


def handle_help_display_command(
    request: ParsedCliCommand,
    *,
    ports: HelpDisplayPorts,
) -> None:
    """Render the CLI help catalog through read-only discovery and display ports."""
    del request
    ports.render_header(ports.text.header)
    for category, commands in ports.command_categories().items():
        ports.render_category(category)
        for command, description in commands.items():
            if ports.command_available(command):
                ports.render_command(command, description)

    skill_commands = ports.skill_commands()
    if skill_commands:
        ports.render_skill_header(ports.text.skill_commands_header, len(skill_commands))
        for command, info in sorted(skill_commands.items()):
            ports.render_skill(command, info["description"])

    ports.render_tip(ports.text.tip_chat, False)
    ports.render_tip(ports.text.tip_multiline, False)
    if ports.is_termux():
        ports.render_tip(
            "Attach image: /image "
            f"{ports.termux_example_path()} or start your prompt with a local image path",
            True,
        )
    else:
        ports.render_tip(ports.text.tip_paste, True)


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
    goal = ports.goal_snapshot()
    if goal:
        objective = str(goal.get("objective") or "").strip()
        status = str(goal.get("status") or "active")
        if objective:
            lines.append(f"Goal: {status} — {objective}")
        reason = str(goal.get("reason") or "").strip()
        if reason:
            lines.append(f"Goal Reason: {reason}")
    lines.extend(ports.autonomous_sections())
    ports.emit("\n".join(lines))


def handle_provider_display_command(
    request: ParsedCliCommand,
    *,
    ports: ProviderDisplayPorts,
) -> None:
    """Render configured-provider status without mutating configuration."""
    if request.arguments:
        for line in (
            ports.translate("  Usage: /provider"),
            ports.translate("         /provider list"),
            "",
            ports.translate("  Use /model to switch providers or models:"),
            ports.translate("    /model <model-name>              — switch model"),
            ports.translate("    /model --provider <provider-name> — switch provider"),
            ports.translate("    /model <provider>:<model>         — switch provider and model"),
            "",
            ports.translate("  Run /api to configure provider credentials"),
        ):
            ports.emit_usage(line)
        return

    snapshot = ports.snapshot()
    current_model = ports.current_model()
    current_provider = snapshot.active_provider
    ports.emit(
        f"\n  Current: {current_model or 'not set'} via "
        f"{current_provider or 'not configured'}"
    )
    ports.emit("")

    if snapshot.configured_providers:
        ports.emit("  Configured providers:")
        for provider in snapshot.configured_providers:
            slug = str(provider["slug"])
            marker = " ← active" if provider.get("is_current") else ""
            ports.emit(f"    [{slug}] {provider['name']}{marker}")
            api_url = str(provider.get("api_url") or "")
            if api_url:
                ports.emit(f"      endpoint: {api_url}")
            models = provider.get("models") or ()
            for model in models:
                current_marker = (
                    " ← current"
                    if provider.get("is_current") and model == current_model
                    else ""
                )
                ports.emit(f"      {model}{current_marker}")
            if not models:
                ports.emit("      no model selected")
            ports.emit("")
    else:
        ports.emit("  No configured providers.")
        ports.emit("  Run /api to configure providers.")
        ports.emit("")

    ports.emit(
        ports.translate(
            "prompts.use_model_to_switch_providers_or_models",
            default="  Use /model to switch providers or models:",
        )
    )
    ports.emit("    /model <model-name>               — switch model")
    ports.emit("    /model --provider <provider-name> — switch provider")
    ports.emit("    /model <name> --provider <provider-name> — switch provider and model")


def handle_memory_display_command(
    request: ParsedCliCommand,
    *,
    ports: MemoryDisplayPorts,
) -> None:
    """Render the unified Mem status without configuring or migrating it."""
    del request
    ports.emit("\n  统一记忆系统: Mem（始终启用）")
    ports.emit(f"  数据库: {ports.database_path()}")
    ports.emit("  工具: mem_search, mem_timeline, mem_remember")
    ports.emit("  审计: Memory Service /recall/traces\n")


def _timestamp_or_default(value: object, default: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OverflowError, OSError):
        return default
