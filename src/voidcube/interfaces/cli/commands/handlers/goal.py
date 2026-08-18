"""Session goal command handler."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..router import ParsedCliCommand


MAX_GOAL_LENGTH = 4000


@dataclass(frozen=True, slots=True)
class GoalCommandPorts:
    get_goal: Callable[[], Mapping[str, Any] | None]
    create_goal: Callable[[str], Mapping[str, Any]]
    update_goal: Callable[[str, str | None], bool]
    clear_goal: Callable[[], bool]
    start_goal: Callable[[str], None] | None
    reset_agent: Callable[[], None]
    emit: Callable[[str], None]
    translate: Callable[..., str]


def handle_goal_command(request: ParsedCliCommand, *, ports: GoalCommandPorts) -> None:
    """Create, inspect, or finish the current session's single goal."""
    arguments = request.arguments.strip()
    if not arguments or arguments.casefold() == "status":
        _show_goal(ports)
        return

    action, _, remainder = arguments.partition(" ")
    action = action.casefold()
    reason = remainder.strip() or None
    current = ports.get_goal()

    if action == "complete":
        if not current or current.get("status") != "active":
            ports.emit(ports.translate("goal_command.no_active"))
            return
        if ports.update_goal("completed", reason):
            ports.reset_agent()
            ports.emit(ports.translate("goal_command.completed"))
        return

    if action == "blocked":
        if not current or current.get("status") != "active":
            ports.emit(ports.translate("goal_command.no_active"))
            return
        if not reason:
            ports.emit(ports.translate("goal_command.blocked_usage"))
            return
        if ports.update_goal("blocked", reason):
            ports.reset_agent()
            ports.emit(ports.translate("goal_command.blocked", reason=reason))
        return

    if action == "clear" and not remainder:
        if not current:
            ports.emit(ports.translate("goal_command.none"))
            return
        if current.get("status") == "active":
            ports.emit(ports.translate("goal_command.clear_active"))
            return
        if ports.clear_goal():
            ports.emit(ports.translate("goal_command.cleared"))
        return

    objective = " ".join(arguments.split())
    if len(objective) > MAX_GOAL_LENGTH:
        ports.emit(ports.translate("goal_command.too_long", limit=MAX_GOAL_LENGTH))
        return
    if current and current.get("status") == "active":
        ports.emit(ports.translate("goal_command.already_active"))
        return
    if current:
        ports.clear_goal()
    ports.create_goal(objective)
    ports.reset_agent()
    ports.emit(ports.translate("goal_command.created", objective=objective))
    if ports.start_goal is not None:
        ports.start_goal(objective)


def _show_goal(ports: GoalCommandPorts) -> None:
    goal = ports.get_goal()
    if not goal:
        ports.emit(ports.translate("goal_command.none"))
        return
    status = str(goal.get("status") or "active")
    objective = str(goal.get("objective") or "")
    lines = [
        ports.translate("goal_command.header"),
        ports.translate(f"goal_command.status_{status}"),
        ports.translate("goal_command.objective", objective=objective),
    ]
    reason = str(goal.get("reason") or "").strip()
    if reason:
        lines.append(ports.translate("goal_command.reason", reason=reason))
    ports.emit("\n".join(lines))


__all__ = ["GoalCommandPorts", "MAX_GOAL_LENGTH", "handle_goal_command"]
