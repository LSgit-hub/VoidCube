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


def bind_goal_backend(host: Any, objective: str) -> dict[str, Any] | None:
    """Best-effort bind to Goal Manager; session goal remains usable on failure."""
    try:
        from plugins.goal_manager.tools.client import GoalClient
        client = GoalClient()
        if not client.health():
            return {"backend": "goal_manager", "backend_status": "unavailable"}
        session_id = str(getattr(host, "session_id", "") or "")
        payload = client.create_session_project(objective, session_id)
        project = payload.get("project") or {}
        root = payload.get("root") or {}
        binding = {
            "backend": "goal_manager",
            "project_id": project.get("id"),
            "root_node_id": root.get("id"),
            "backend_status": "available",
        }
        repository = getattr(host, "_session_db", None)
        if repository is not None and hasattr(repository, "bind_session_goal_backend"):
            repository.bind_session_goal_backend(session_id, **binding)
        else:
            goal = _memory_goals(host).get(session_id)
            if goal:
                goal.update(binding)
        return binding
    except Exception:
        return {"backend": "goal_manager", "backend_status": "unavailable"}


def backend_status(host: Any, goal: Mapping[str, Any]) -> dict[str, Any] | None:
    project_id = str(goal.get("project_id") or "").strip()
    if not project_id or goal.get("backend") != "goal_manager":
        return None
    try:
        from plugins.goal_manager.tools.client import GoalClient
        project = GoalClient().project(project_id)
        return {"backend_status": "available", "backend_project": project}
    except Exception:
        return {"backend_status": "unavailable"}


def goal_prompt(goal: Mapping[str, Any] | None) -> str:
    """Render an active goal as a bounded instruction for the agent."""
    if not goal or goal.get("status") != ACTIVE:
        return ""
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return ""
    binding = ""
    if goal.get("project_id"):
        binding = (
            f"\nGoal Manager project_id: {goal['project_id']}"
            f"\nGoal Manager root_node_id: {goal.get('root_node_id') or 'unknown'}"
            "\nUse the project context and next_actions, create verifiable child goals when needed, "
            "attach evidence for completed work, and never mark completion without validation."
        )
    return (
        "## Active Session Goal\n"
        f"Objective: {objective}{binding}\n"
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
    "bind_goal_backend",
    "backend_status",
    "goal_prompt",
]
