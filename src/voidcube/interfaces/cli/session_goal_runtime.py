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
    setattr(host, "_goal_update_error", None)
    if status == COMPLETED and not _complete_backend(host, reason):
        return False
    repository = getattr(host, "_session_db", None)
    if repository is not None and hasattr(repository, "update_session_goal"):
        updated = bool(repository.update_session_goal(session_id, status, reason))
        if updated and status == BLOCKED:
            _sync_blocked_backend(host, reason)
        return updated
    goal = _memory_goals(host).get(session_id)
    if not goal or goal.get("status") != ACTIVE:
        return False
    from time import time

    goal["status"] = status
    goal["reason"] = reason
    goal["updated_at"] = time()
    if status == BLOCKED:
        _sync_blocked_backend(host, reason)
    return True


def _complete_backend(host: Any, reason: str | None) -> bool:
    """Complete the bound Goal Manager root before committing local state."""
    session_id = str(getattr(host, "session_id", "") or "").strip()
    goal = get_goal(host)
    project_id = str((goal or {}).get("project_id") or "").strip()
    root_node_id = str((goal or {}).get("root_node_id") or "").strip()
    if not project_id or not root_node_id or (goal or {}).get("backend") != "goal_manager":
        return True
    try:
        from plugins.goal_manager.tools.client import GoalClient, GoalServiceError

        GoalClient().complete_node(
            root_node_id, reason or "session goal completed", session_id=session_id,
        )
        _set_backend_status(host, session_id, goal, "available")
        return True
    except GoalServiceError as exc:
        detail = exc.payload if isinstance(exc.payload, Mapping) else {}
        setattr(host, "_goal_update_error", _format_completion_error(detail, exc.status_code))
        _set_backend_status(
            host, session_id, goal, "available" if exc.status_code < 500 else "unavailable",
        )
        return False
    except Exception:
        setattr(host, "_goal_update_error", "Goal Manager 服务暂时不可用")
        _set_backend_status(host, session_id, goal, "unavailable")
        return False


def _format_completion_error(payload: Mapping[str, Any], status_code: int) -> str:
    if status_code >= 500:
        return "Goal Manager 服务暂时不可用"
    blockers = payload.get("blockers") or []
    details: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, Mapping):
            continue
        if blocker.get("code") == "child_incomplete":
            details.append(
                f"子目标未完成：{blocker.get('title') or blocker.get('node_id') or '未命名'}"
            )
        elif blocker.get("code") == "acceptance_criteria_unmet":
            criterion = blocker.get("criterion")
            if isinstance(criterion, Mapping):
                text = criterion.get("text") or criterion.get("title")
            else:
                text = None
            index = int(blocker.get("index", 0)) + 1
            details.append(f"验收条件未满足：{text or f'第 {index} 项'}")
    if details:
        return "；".join(details)
    detail = str(payload.get("detail") or "")
    if detail.casefold() == "goal completion blocked":
        return "Goal Manager 未通过完成校验"
    return detail or "Goal Manager 未通过完成校验"


def goal_update_error(host: Any) -> str | None:
    return str(getattr(host, "_goal_update_error", "") or "").strip() or None


def _sync_blocked_backend(host: Any, reason: str | None) -> None:
    """Best-effort mirror of a local block onto the Goal Manager root node."""
    session_id = str(getattr(host, "session_id", "") or "").strip()
    goal = get_goal(host)
    project_id = str((goal or {}).get("project_id") or "").strip()
    root_node_id = str((goal or {}).get("root_node_id") or "").strip()
    if not project_id or not root_node_id or (goal or {}).get("backend") != "goal_manager":
        return
    try:
        from plugins.goal_manager.tools.client import GoalClient

        client = GoalClient()
        project = client.project(project_id)
        root = project.get("root") or {}
        if str(root.get("id") or root_node_id) != root_node_id:
            raise RuntimeError("goal manager root node mismatch")
        if root.get("status") == BLOCKED:
            _set_backend_status(host, session_id, goal, "available")
            return
        version = root.get("version")
        if version is None:
            raise RuntimeError("goal manager root node version missing")
        client.update_node_status(root_node_id, int(version), BLOCKED, reason or "session goal blocked", session_id=session_id)
        _set_backend_status(host, session_id, goal, "available")
    except Exception:
        _set_backend_status(host, session_id, goal, "unavailable")


def _set_backend_status(
    host: Any, session_id: str, goal: Mapping[str, Any] | None, status: str,
) -> None:
    if not goal:
        return
    repository = getattr(host, "_session_db", None)
    if repository is not None and hasattr(repository, "bind_session_goal_backend"):
        try:
            repository.bind_session_goal_backend(
                session_id,
                backend=str(goal.get("backend") or "goal_manager"),
                project_id=str(goal.get("project_id") or "") or None,
                root_node_id=str(goal.get("root_node_id") or "") or None,
                backend_status=status,
            )
            return
        except Exception:
            pass
    current = _memory_goals(host).get(session_id)
    if current:
        current["backend_status"] = status


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
        client = GoalClient()
        project = client.project(project_id)
        if goal.get("status") == BLOCKED:
            root = project.get("root") or {}
            if root.get("status") != BLOCKED:
                version = root.get("version")
                if version is None or str(root.get("id") or "") != str(goal.get("root_node_id") or ""):
                    raise RuntimeError("goal manager root node cannot be reconciled")
                result = client.update_node_status(
                    str(goal["root_node_id"]), int(version), BLOCKED,
                    str(goal.get("reason") or "session goal blocked"),
                    session_id=str(getattr(host, "session_id", "") or "").strip(),
                )
                if result.get("node"):
                    project["root"] = result["node"]
            _set_backend_status(
                host, str(getattr(host, "session_id", "") or "").strip(), goal, "available",
            )
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
    "goal_update_error",
    "clear_goal",
    "bind_goal_backend",
    "backend_status",
    "goal_prompt",
]
