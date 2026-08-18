"""Session-scoped goal state shared by the CLI command and agent adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ACTIVE = "active"
COMPLETED = "completed"
BLOCKED = "blocked"


def _memory_goals(host: Any) -> dict[str, dict[str, Any]]:
    return getattr(host, "_session_goals", {})


def get_goal(host: Any) -> dict[str, Any] | None:
    session_id = str(getattr(host, "session_id", "") or "").strip()
    if not session_id:
        return None
    repository = getattr(host, "_session_db", None)
    if repository is not None and hasattr(repository, "get_session_goal"):
        try:
            return repository.get_session_goal(session_id)
        except Exception:
            pass
    goal = _memory_goals(host).get(session_id)
    return dict(goal) if goal else None


def create_goal(host: Any, objective: str) -> dict[str, Any]:
    session_id = str(getattr(host, "session_id", "") or "").strip()
    repository = getattr(host, "_session_db", None)
    if repository is not None and hasattr(repository, "create_session_goal"):
        return repository.create_session_goal(session_id, objective)
    from time import time

    now = time()
    goal = {
        "session_id": session_id,
        "objective": objective,
        "status": ACTIVE,
        "reason": None,
        "created_at": now,
        "updated_at": now,
    }
    goals = _memory_goals(host)
    goals[session_id] = goal
    setattr(host, "_session_goals", goals)
    return dict(goal)


def update_goal(host: Any, status: str, reason: str | None = None) -> bool:
    session_id = str(getattr(host, "session_id", "") or "").strip()
    repository = getattr(host, "_session_db", None)
    if repository is not None and hasattr(repository, "update_session_goal"):
        return bool(repository.update_session_goal(session_id, status, reason))
    goal = _memory_goals(host).get(session_id)
    if not goal or goal.get("status") != ACTIVE:
        return False
    from time import time

    goal["status"] = status
    goal["reason"] = reason
    goal["updated_at"] = time()
    return True


def clear_goal(host: Any) -> bool:
    session_id = str(getattr(host, "session_id", "") or "").strip()
    repository = getattr(host, "_session_db", None)
    if repository is not None and hasattr(repository, "clear_session_goal"):
        return bool(repository.clear_session_goal(session_id))
    goal = _memory_goals(host).get(session_id)
    if not goal or goal.get("status") == ACTIVE:
        return False
    del _memory_goals(host)[session_id]
    return True


def goal_prompt(goal: Mapping[str, Any] | None) -> str:
    """Render an active goal as a bounded instruction for the agent."""
    if not goal or goal.get("status") != ACTIVE:
        return ""
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return ""
    return (
        "## Active Session Goal\n"
        f"Objective: {objective}\n"
        "Treat this as the governing objective for the current session. "
        "Make measurable progress toward it, keep the user informed, and "
        "do not claim completion without evidence."
    )


__all__ = [
    "ACTIVE",
    "COMPLETED",
    "BLOCKED",
    "get_goal",
    "create_goal",
    "update_goal",
    "clear_goal",
    "goal_prompt",
]
