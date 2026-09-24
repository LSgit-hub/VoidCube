"""Agent tools for managing the current session goal."""

from __future__ import annotations

import json
from typing import Any

from .registry import registry, tool_error


SESSION_GOAL_SCHEMA = {
    "name": "session_goal",
    "description": (
        "Manage the goal owned by the current session. Create a goal only when "
        "the user explicitly requests one; do not infer a goal from an ordinary task. "
        "Pause only when explicitly requested. Complete only after verifying the "
        "requested outcome. Use audit_blocker to report unresolved blockers."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "create", "update", "audit_blocker"],
            },
            "objective": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["paused", "completed", "complete"],
            },
            "reason": {"type": "string"},
        },
        "required": ["action"],
    },
}

GET_GOAL_SCHEMA = {
    "name": "get_goal",
    "description": "Get the current goal owned by this session, including its objective and status.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

CREATE_GOAL_SCHEMA = {
    "name": "create_goal",
    "description": (
        "Create a goal only when explicitly requested by the user or system. "
        "Fail when an unfinished goal already exists."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "objective": {"type": "string"},
        },
        "required": ["objective"],
    },
}

UPDATE_GOAL_SCHEMA = {
    "name": "update_goal",
    "description": (
        "Update the existing goal. Use paused only after an explicit user "
        "request, complete only after the completion audit passes, and blocked "
        "only after the same blocker has recurred for three consecutive turns."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["complete", "blocked", "paused"],
            },
        },
        "required": ["status"],
    },
}


def dispatch_session_goal(
    action: str,
    *,
    host: Any,
    objective: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    turn_id: str = "",
    allow_blocked: bool = False,
) -> str:
    from ...interfaces.cli.session_goal_runtime import (
        audit_blocked_goal,
        clear_goal,
        create_goal,
        get_goal,
        update_goal,
    )

    if action == "create":
        normalized_objective = " ".join(str(objective or "").split())
        if not normalized_objective:
            return tool_error("objective is required")
        if len(normalized_objective) > 4000:
            return tool_error("objective must be 4000 characters or fewer")
        current = get_goal(host)
        if current and current.get("status") != "completed":
            return tool_error(
                "an unfinished goal already exists; complete it before creating another",
                goal=current,
            )
        if current and not clear_goal(host):
            return tool_error("the completed goal could not be replaced", goal=current)
        if getattr(host, "_session_db", None) is None and not str(
            getattr(host, "session_id", "") or ""
        ).strip():
            return tool_error("current session identity is unavailable")
        try:
            goal = create_goal(host, normalized_objective)
        except Exception as exc:
            return tool_error(f"goal creation failed: {exc}")
        return json.dumps({"success": True, "goal": goal}, ensure_ascii=False)
    if action == "get":
        goal = get_goal(host)
        return json.dumps(
            {"success": True, "goal": goal},
            ensure_ascii=False,
        )
    if action == "audit_blocker":
        if not str(reason or "").strip():
            return tool_error("reason is required")
        if not turn_id:
            return tool_error("Current turn identity is unavailable.")
        result = audit_blocked_goal(host, str(reason), turn_id=turn_id)
        return json.dumps(
            {"success": result is not None, "goal": result},
            ensure_ascii=False,
        )
    if action == "update":
        if status == "complete":
            status = "completed"
        allowed_statuses = {"paused", "completed"}
        if allow_blocked:
            allowed_statuses.add("blocked")
        if status not in allowed_statuses:
            return tool_error("status must be paused, completed, or blocked")
        if not update_goal(host, status, reason):
            return tool_error("goal update rejected", goal=get_goal(host))
        return json.dumps(
            {"success": True, "goal": get_goal(host)},
            ensure_ascii=False,
        )
    return tool_error("action must be get, create, update, or audit_blocker")


def _handle_session_goal(args: dict[str, Any], **kwargs: Any) -> str:
    host = kwargs.get("host")
    if host is None:
        return tool_error("Current session goal is unavailable.")
    return dispatch_session_goal(
        str(args.get("action") or ""),
        host=host,
        objective=args.get("objective"),
        status=args.get("status"),
        reason=args.get("reason"),
        turn_id=str(kwargs.get("turn_id") or ""),
    )


def _individual_goal_dispatch(action: str, args: dict[str, Any], **kwargs: Any) -> str:
    host = kwargs.get("host")
    if host is None:
        return tool_error("Current session goal is unavailable.")
    if action == "get":
        return dispatch_session_goal("get", host=host)
    if action == "create":
        return dispatch_session_goal(
            "create",
            host=host,
            objective=args.get("objective"),
        )
    status = str(args.get("status") or "").strip().casefold()
    if status not in {"complete", "blocked", "paused"}:
        return tool_error("status must be complete, blocked, or paused")
    if status == "complete":
        status = "completed"
    return dispatch_session_goal(
        "update",
        host=host,
        status=status,
        reason=args.get("reason"),
        allow_blocked=True,
    )


def _handle_get_goal(args: dict[str, Any], **kwargs: Any) -> str:
    del args
    return _individual_goal_dispatch("get", {}, **kwargs)


def _handle_create_goal(args: dict[str, Any], **kwargs: Any) -> str:
    return _individual_goal_dispatch("create", args, **kwargs)


def _handle_update_goal(args: dict[str, Any], **kwargs: Any) -> str:
    return _individual_goal_dispatch("update", args, **kwargs)


registry.register(
    name="session_goal",
    toolset="session_goal",
    schema=SESSION_GOAL_SCHEMA,
    handler=_handle_session_goal,
    effect="idempotent_write",
)
registry.register(
    name="get_goal",
    toolset="session_goal",
    schema=GET_GOAL_SCHEMA,
    handler=_handle_get_goal,
    effect="read_only",
)
registry.register(
    name="create_goal",
    toolset="session_goal",
    schema=CREATE_GOAL_SCHEMA,
    handler=_handle_create_goal,
    effect="idempotent_write",
)
registry.register(
    name="update_goal",
    toolset="session_goal",
    schema=UPDATE_GOAL_SCHEMA,
    handler=_handle_update_goal,
    effect="idempotent_write",
)
