"""Deployment-preset catalog command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class PresetCommandPorts:
    list_presets: Callable[[], list[Mapping[str, Any]]]
    load_preset: Callable[[str], Mapping[str, Any] | None]
    apply_preset: Callable[[str], Mapping[str, Any]]
    emit: Callable[[str], None]
    text: "PresetCommandText"


@dataclass(frozen=True, slots=True)
class PresetCommandText:
    dim: str
    accent: str
    bold: str
    reset: str


def handle_preset_command(
    request: ParsedCliCommand,
    *,
    ports: PresetCommandPorts,
) -> None:
    """Render or request a deployment preset without executing host actions."""
    parts = request.arguments.split(maxsplit=1)
    subcommand = parts[0].lower() if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""
    if subcommand == "list":
        _render_preset_list(ports)
    elif subcommand == "show":
        if not name:
            ports.emit("  Usage: /preset show <name>")
            return
        _render_preset(ports.load_preset(name), name, ports)
    elif subcommand == "apply":
        if not name:
            ports.emit("  Usage: /preset apply <name>")
            return
        _render_apply_result(name, ports.apply_preset(name), ports)
    else:
        ports.emit(f"  Unknown subcommand: {subcommand}")
        ports.emit("  Usage: /preset [list|apply|show] [name]")


def _render_preset_list(ports: PresetCommandPorts) -> None:
    presets = ports.list_presets()
    if not presets:
        ports.emit(f"  {ports.text.dim}No presets available.{ports.text.reset}")
        return
    ports.emit(f"\n  {ports.text.bold}Available Presets:{ports.text.reset}")
    for preset in presets:
        file_name = str(preset.get("file", ""))
        ports.emit(
            f"    {ports.text.accent}{file_name:<20}{ports.text.reset} "
            f"{preset.get('name', '')}"
        )
        ports.emit(
            f"    {'':20} {preset.get('description', '')} "
            f"({preset.get('steps_count', 0)} steps)"
        )


def _render_preset(
    preset: Mapping[str, Any] | None,
    requested_name: str,
    ports: PresetCommandPorts,
) -> None:
    if preset is None:
        ports.emit(f"  Preset not found: {requested_name}")
        return
    ports.emit(
        f"\n  {ports.text.bold}Preset: {preset.get('name', requested_name)}"
        f"{ports.text.reset}"
    )
    ports.emit(f"  {preset.get('description', '')}")
    ports.emit(f"\n  {ports.text.bold}Steps:{ports.text.reset}")
    raw_steps = preset.get("steps")
    if isinstance(raw_steps, list):
        for index, step in enumerate(raw_steps, 1):
            if isinstance(step, Mapping):
                ports.emit(f"    {index}. {step.get('action', '?')} -> {dict(step)}")


def _render_apply_result(
    name: str, result: Mapping[str, Any], ports: PresetCommandPorts
) -> None:
    if result.get("reason") == "preset_not_found":
        ports.emit(f"  Preset not found: {name}")
        return
    if result.get("reason") == "execution_not_available":
        ports.emit(
            "  Preset execution is unavailable: deployment actions require an "
            "approved execution runtime."
        )
        return
    ports.emit(
        f"  {ports.text.accent}Preset applied successfully!{ports.text.reset}"
        if result.get("success")
        else "  Preset apply had errors:"
    )
