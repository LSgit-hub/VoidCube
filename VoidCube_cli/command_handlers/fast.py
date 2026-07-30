"""Priority-processing command state changes through explicit CLI ports."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from VoidCube_cli.command_router import ParsedCliCommand


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FastCommandPorts:
    available: Callable[[], bool]
    service_tier: Callable[[], str | None]
    set_service_tier: Callable[[str | None], None]
    save_service_tier: Callable[[str], bool]
    emit: Callable[[str], None]
    accent: str
    dim: str
    reset: str


def parse_service_tier_config(raw: str) -> str | None:
    """Parse a persisted service-tier preference into a Responses API value."""
    value = str(raw or "").strip().lower()
    if not value or value in {"normal", "default", "standard", "off", "none"}:
        return None
    if value in {"fast", "priority", "on"}:
        return "priority"
    logger.warning("Unknown service_tier '%s', ignoring", raw)
    return None


def handle_fast_command(
    request: ParsedCliCommand,
    *,
    ports: FastCommandPorts,
) -> None:
    """Display or update priority processing without owning the CLI host."""
    if not ports.available():
        ports.emit(
            "  (._.) /fast is only available for models that support priority processing."
        )
        return

    argument = request.arguments.strip().lower()
    if not argument or argument == "status":
        status = "fast" if ports.service_tier() == "priority" else "normal"
        ports.emit(f"  {ports.accent}Priority Processing: {status}{ports.reset}")
        ports.emit(f"  {ports.dim}Usage: /fast [normal|fast|status]{ports.reset}")
        return

    if argument in {"fast", "on"}:
        service_tier, saved_value, label = "priority", "fast", "FAST"
    elif argument in {"normal", "off"}:
        service_tier, saved_value, label = None, "normal", "NORMAL"
    else:
        ports.emit(f"  {ports.dim}(._.) Unknown argument: {argument}{ports.reset}")
        ports.emit(f"  {ports.dim}Usage: /fast [normal|fast|status]{ports.reset}")
        return

    ports.set_service_tier(service_tier)
    if ports.save_service_tier(saved_value):
        ports.emit(
            f"  {ports.accent}✓ Priority Processing set to {label} (saved to config){ports.reset}"
        )
    else:
        ports.emit(
            f"  {ports.accent}✓ Priority Processing set to {label} (session only){ports.reset}"
        )
