"""Session goal command handler."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..router import ParsedCliCommand
from .....domain.goals import classify_goal_backend


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
    update_objective: Callable[[str], bool] | None = None
    bind_backend: Callable[[str], Mapping[str, Any] | None] | None = None
    get_backend_status: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None
    get_update_error: Callable[[], str | None] | None = None


def handle_goal_command(request: ParsedCliCommand, *, ports: GoalCommandPorts) -> None:
    """Create, inspect, or finish the current session's single goal."""
    arguments = request.arguments.strip()
    if not arguments or arguments.casefold() == "status":
        _show_goal(ports)
        return

    parts = arguments.split(maxsplit=1)
    action = parts[0].casefold()
    remainder = parts[1] if len(parts) > 1 else ""
    use_goal_manager = action in {"glq", "--glq"}
    if use_goal_manager:
        if not remainder.strip():
            ports.emit(ports.translate("goal_command.glq_usage"))
            return
        arguments = remainder
    reason = remainder.strip() or None
    current = ports.get_goal()

    if action == "complete":
        if not current or current.get("status") != "active":
            ports.emit(ports.translate("goal_command.no_active"))
            return
        if ports.update_goal("completed", reason):
            ports.reset_agent()
            ports.emit(ports.translate("goal_command.completed"))
        else:
            detail = ports.get_update_error() if ports.get_update_error is not None else None
            if detail:
                ports.emit(ports.translate("goal_command.complete_blocked_reason", reason=detail))
            else:
                ports.emit(ports.translate("goal_command.complete_blocked"))
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

    if action == "pause":
        if not current or current.get("status") != "active":
            ports.emit(ports.translate("goal_command.no_active"))
            return
        if ports.update_goal("paused", reason):
            ports.reset_agent()
            ports.emit(ports.translate("goal_command.paused"))
        return

    if action == "resume":
        if not current or current.get("status") not in {"blocked", "paused"}:
            ports.emit(ports.translate("goal_command.no_resumable"))
            return
        if ports.update_goal("active", reason):
            ports.reset_agent()
            ports.emit(ports.translate("goal_command.resumed"))
            if ports.start_goal is not None:
                ports.start_goal(str(current.get("objective") or ""))
        return

    if action in {"edit", "set"}:
        if not current or current.get("status") == "completed":
            ports.emit(ports.translate("goal_command.no_editable"))
            return
        objective = " ".join(remainder.split())
        if not objective:
            ports.emit(ports.translate("goal_command.edit_usage"))
            return
        if len(objective) > MAX_GOAL_LENGTH:
            ports.emit(ports.translate("goal_command.too_long", limit=MAX_GOAL_LENGTH))
            return
        if ports.update_objective is None or not ports.update_objective(objective):
            ports.emit(ports.translate("goal_command.edit_failed"))
            return
        ports.emit(ports.translate("goal_command.edited", objective=objective))
        if ports.start_goal is not None:
            ports.start_goal(objective)
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
    routing = classify_goal_backend(objective, explicit=use_goal_manager)
    use_goal_manager = routing.backend == "goal_manager"
    if use_goal_manager:
        binding = ports.bind_backend(objective) if ports.bind_backend is not None else None
        if not binding or binding.get("backend_status") != "available":
            ports.emit(ports.translate("goal_command.backend_unavailable"))
        elif not action in {"glq", "--glq"}:
            ports.emit(ports.translate("goal_command.backend_auto_enabled"))
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
    if goal.get("backend") == "goal_manager" and ports.get_backend_status is not None:
        remote = ports.get_backend_status(goal)
        if remote:
            goal = dict(goal)
            goal.update(remote)
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
