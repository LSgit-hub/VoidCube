"""Bundled plan-skill command handler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class PlanCommandPorts:
    build_plan_path: Callable[[str], Path]
    build_skill_message: Callable[[str, str, str], str | None]
    enqueue: Callable[[str], None] | None
    emit: Callable[[str], None]
    render_error: Callable[[str], None]


def handle_plan_command(request: ParsedCliCommand, *, ports: PlanCommandPorts) -> None:
    """Queue the bundled `/plan` skill with its workspace-relative target path."""
    instruction = request.arguments
    plan_path = ports.build_plan_path(instruction)
    runtime_note = (
        "Save the markdown plan with write_file to this exact relative path "
        f"inside the active workspace/backend cwd: {plan_path}"
    )
    message = ports.build_skill_message("/plan", instruction, runtime_note)
    if not message:
        ports.render_error("Failed to load the bundled /plan skill")
        return

    ports.emit(f"  📝 Plan mode queued via skill. Markdown plan target: {plan_path}")
    if ports.enqueue is None:
        ports.render_error("Plan mode unavailable: input queue not initialized")
        return
    ports.enqueue(message)
