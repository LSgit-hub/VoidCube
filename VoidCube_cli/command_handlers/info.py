"""Informational CLI command handlers and text projections."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


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
