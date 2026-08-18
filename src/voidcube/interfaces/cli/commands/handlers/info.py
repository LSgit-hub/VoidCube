"""Informational CLI command handlers and text projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class ProfileCommandPorts:
    home: Callable[[], Path]
    display_home: Callable[[], str]
    profiles_parent: Callable[[], Path]
    emit: Callable[[str], None]
    default_profile_message: str


@dataclass(frozen=True, slots=True)
class PluginsCommandPorts:
    discover: Callable[[], object]
    list_plugins: Callable[[], Sequence[Mapping[str, object]]]
    plugins_home: Callable[[], str]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class UsageDisplaySnapshot:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    api_calls: int
    session_duration: str
    cost_status: str
    cost_source: str
    cost_amount_usd: float | None
    context_tokens: int
    context_length: int
    context_percent: float
    message_count: int
    compressions: int


@dataclass(frozen=True, slots=True)
class UsageCommandPorts:
    agent_available: Callable[[], bool]
    api_calls: Callable[[], int]
    rate_limit_display: Callable[[], str | None]
    snapshot: Callable[[], UsageDisplaySnapshot]
    emit: Callable[[str], None]
    no_agent_message: str
    no_calls_message: str


def handle_profile_command(
    request: ParsedCliCommand,
    *,
    ports: ProfileCommandPorts,
) -> None:
    del request
    profile_name = _profile_name(ports.home(), ports.profiles_parent())
    ports.emit("")
    ports.emit(
        f"  Profile: {profile_name}"
        if profile_name
        else ports.default_profile_message
    )
    ports.emit(f"  Home:    {ports.display_home()}")
    ports.emit("")


def handle_plugins_command(
    request: ParsedCliCommand,
    *,
    ports: PluginsCommandPorts,
) -> None:
    del request
    try:
        ports.discover()
        plugins = ports.list_plugins()
        if not plugins:
            ports.emit("No plugins installed.")
            ports.emit(
                f"Drop plugin directories into {ports.plugins_home()}/plugins/ "
                "to get started."
            )
            return
        ports.emit(f"Plugins ({len(plugins)}):")
        for plugin in plugins:
            ports.emit(_project_plugin(plugin))
    except Exception as exc:
        ports.emit(f"Plugin system error: {exc}")


def handle_usage_command(
    request: ParsedCliCommand,
    *,
    ports: UsageCommandPorts,
) -> None:
    """Project rate-limit and session usage snapshots without changing runtime state."""
    del request
    if not ports.agent_available():
        ports.emit(ports.no_agent_message)
        return

    if ports.api_calls() == 0:
        ports.emit(ports.no_calls_message)
        return

    rate_limits = ports.rate_limit_display()
    if rate_limits:
        ports.emit("")
        ports.emit(rate_limits)
        ports.emit("")

    snapshot = ports.snapshot()
    ports.emit("  📊 Session Token Usage")
    ports.emit(f"  {'─' * 40}")
    ports.emit(f"  Model:                     {snapshot.model}")
    ports.emit(f"  Input tokens:              {snapshot.input_tokens:>10,}")
    ports.emit(f"  Cache read tokens:         {snapshot.cache_read_tokens:>10,}")
    ports.emit(f"  Cache write tokens:        {snapshot.cache_write_tokens:>10,}")
    ports.emit(f"  Output tokens:             {snapshot.output_tokens:>10,}")
    ports.emit(f"  Prompt tokens (total):     {snapshot.prompt_tokens:>10,}")
    ports.emit(f"  Completion tokens:         {snapshot.completion_tokens:>10,}")
    ports.emit(f"  Total tokens:              {snapshot.total_tokens:>10,}")
    ports.emit(f"  API calls:                 {snapshot.api_calls:>10,}")
    ports.emit(f"  Session duration:          {snapshot.session_duration:>10}")
    ports.emit(f"  Cost status:              {snapshot.cost_status:>10}")
    ports.emit(f"  Cost source:              {snapshot.cost_source:>10}")
    if snapshot.cost_amount_usd is not None:
        prefix = "~" if snapshot.cost_status == "estimated" else ""
        ports.emit(f"  Total cost:              {prefix}${snapshot.cost_amount_usd:>10.4f}")
    elif snapshot.cost_status == "included":
        ports.emit(f"  Total cost:              {'included':>10}")
    else:
        ports.emit(f"  Total cost:              {'n/a':>10}")
    ports.emit(f"  {'─' * 40}")
    ports.emit(
        "  Current context:  "
        f"{snapshot.context_tokens:,} / {snapshot.context_length:,} "
        f"({snapshot.context_percent:.0f}%)"
    )
    ports.emit(f"  Messages:         {snapshot.message_count}")
    ports.emit(f"  Compressions:     {snapshot.compressions}")
    if snapshot.cost_status == "unknown":
        ports.emit(f"  Note:             Pricing unknown for {snapshot.model}")


def _profile_name(home: Path, profiles_parent: Path) -> str | None:
    try:
        relative = home.relative_to(profiles_parent)
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def _project_plugin(plugin: Mapping[str, object]) -> str:
    status = "✓" if plugin.get("enabled") else "✗"
    version_value = plugin.get("version")
    version = f" v{version_value}" if version_value else ""
    detail_parts = []
    if plugin.get("tools"):
        detail_parts.append(f"{plugin['tools']} tools")
    if plugin.get("hooks"):
        detail_parts.append(f"{plugin['hooks']} hooks")
    detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
    error_value = plugin.get("error")
    error = f" — {error_value}" if error_value else ""
    return f"  {status} {plugin.get('name', '')}{version}{detail}{error}"
