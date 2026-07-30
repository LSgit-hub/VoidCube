"""Reasoning command state changes through explicit CLI runtime ports."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Mapping

from VoidCube_cli.command_router import ParsedCliCommand
from VoidCube_core.constants import parse_reasoning_effort


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReasoningCommandPorts:
    reasoning_config: Callable[[], Mapping[str, object] | None]
    show_reasoning: Callable[[], bool]
    set_reasoning_config: Callable[[dict | None], None]
    set_show_reasoning: Callable[[bool], None]
    refresh_agent_reasoning_callback: Callable[[], None]
    parse_config: Callable[[str], dict | None]
    save_display: Callable[[bool], bool]
    save_effort: Callable[[str], bool]
    emit: Callable[[str], None]
    accent: str
    dim: str
    reset: str


def parse_reasoning_config(effort: str) -> dict | None:
    """Parse a persisted or requested reasoning effort configuration."""
    result = parse_reasoning_effort(effort)
    if effort and effort.strip() and result is None:
        logger.warning("Unknown reasoning_effort '%s', using default (medium)", effort)
    return result


def handle_reasoning_command(
    request: ParsedCliCommand,
    *,
    ports: ReasoningCommandPorts,
) -> None:
    """Display or update effort and reasoning output without owning the host."""
    argument = request.arguments.strip().lower()
    if not argument:
        _render_current_state(ports)
        return

    if argument in {"show", "on"}:
        ports.set_show_reasoning(True)
        ports.refresh_agent_reasoning_callback()
        ports.save_display(True)
        ports.emit(f"  {ports.accent}✓ Reasoning display: ON (saved){ports.reset}")
        ports.emit(
            f"  {ports.dim}  Model thinking will be shown during and after each response.{ports.reset}"
        )
        return
    if argument in {"hide", "off"}:
        ports.set_show_reasoning(False)
        ports.refresh_agent_reasoning_callback()
        ports.save_display(False)
        ports.emit(f"  {ports.accent}✓ Reasoning display: OFF (saved){ports.reset}")
        return

    parsed = ports.parse_config(argument)
    if parsed is None:
        ports.emit(f"  {ports.dim}(._.) Unknown argument: {argument}{ports.reset}")
        ports.emit(
            f"  {ports.dim}Valid levels: none, minimal, low, medium, high, xhigh{ports.reset}"
        )
        ports.emit(f"  {ports.dim}Display:      show, hide{ports.reset}")
        return

    ports.set_reasoning_config(parsed)
    if ports.save_effort(argument):
        ports.emit(
            f"  {ports.accent}✓ Reasoning effort set to '{argument}' (saved to config){ports.reset}"
        )
    else:
        ports.emit(
            f"  {ports.accent}✓ Reasoning effort set to '{argument}' (session only){ports.reset}"
        )


def _render_current_state(ports: ReasoningCommandPorts) -> None:
    config = ports.reasoning_config()
    if config is None:
        level = "medium (default)"
    elif config.get("enabled") is False:
        level = "none (disabled)"
    else:
        level = str(config.get("effort", "medium"))
    display_state = "on ✓" if ports.show_reasoning() else "off"
    ports.emit(f"  {ports.accent}Reasoning effort:  {level}{ports.reset}")
    ports.emit(f"  {ports.accent}Reasoning display: {display_state}{ports.reset}")
    ports.emit(
        f"  {ports.dim}Usage: /reasoning <none|minimal|low|medium|high|xhigh|show|hide>{ports.reset}"
    )
