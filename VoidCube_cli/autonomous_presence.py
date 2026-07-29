from __future__ import annotations

from typing import Any, Dict

from VoidCube_app.gateway import push_agent_scene


def current_cli_agent_role(host: Any) -> str:
    active_turn_role = str(getattr(host, "_active_chat_agent_role", "") or "").strip()
    if active_turn_role in {"supervisor_task", "user_chat"}:
        return active_turn_role
    if getattr(host, "_current_autonomous_task", None):
        return "supervisor_task"
    return "user_chat"


def ensure_supervisor_task_session(host: Any, *, logger_debug: Any) -> None:
    session_id = str(getattr(host, "session_id", "") or "").strip()
    if not session_id:
        return
    session_db = getattr(host, "_session_db", None)
    if session_db is None:
        return
    try:
        existing = session_db.get_session(session_id)
        if existing is None:
            session_db.create_session(
                session_id=session_id,
                source="cli_supervisor_task_lane",
                model=getattr(host, "model", None),
            )
            return
        if existing.get("source") == "cli":
            cursor = session_db._conn.execute(
                "SELECT COUNT(*) AS count FROM messages WHERE session_id = ?",
                (session_id,),
            )
            message_count = int((cursor.fetchone() or {"count": 0})["count"] or 0)
            if message_count == 0:
                session_db._conn.execute(
                    "UPDATE sessions SET source = ? WHERE id = ?",
                    ("cli_supervisor_task_lane", session_id),
                )
                session_db._conn.commit()
    except Exception as exc:
        logger_debug("Could not persist supervisor_task lane session: %s", exc)


def push_cli_agent_scene(scene: str, **kwargs: Any) -> bool:
    """Report a CLI agent scene through the shared Gateway client."""
    return push_agent_scene(scene, source_service="cli_agent", **kwargs)


def current_gateway_presence_snapshot(host: Any) -> tuple[str, str | None, str | None]:
    active_turn_role = str(getattr(host, "_active_chat_agent_role", "") or "").strip()
    current = getattr(host, "_current_autonomous_task", None) or {}
    if active_turn_role != "user_chat" and current:
        task_id = str(current.get("task_id") or "").strip() or None
        execution_kind = str(
            current.get("execution_kind") or current.get("task_type") or ""
        ).strip().lower() or None
        if execution_kind == "body_improvement":
            return "code_editing", task_id, execution_kind
        if task_id:
            return "learning", task_id, execution_kind
        return "executing", None, None
    stream_state = getattr(host, "_stream_render_state", None)
    if (
        getattr(host, "_agent_running", False)
        or getattr(host, "_command_running", False)
        or bool(stream_state and stream_state.started)
        or host._get_subagent_observability_snapshot().get("active")
    ):
        return "executing", None, None
    return "idle", None, None


def refresh_gateway_cli_presence(
    host: Any,
    *,
    force: bool,
    is_gateway_running: Any,
    register_with_gateway: Any,
    push_cli_agent_scene: Any,
    monotonic_time: Any,
) -> None:
    session_id = str(getattr(host, "session_id", "") or "").strip()
    if not session_id or not is_gateway_running():
        return

    now = monotonic_time()
    refresh_interval = float(
        getattr(host, "_gateway_presence_refresh_interval_seconds", 30.0) or 30.0
    )
    last_refresh = float(getattr(host, "_last_gateway_presence_refresh_at", 0.0) or 0.0)
    if not force and now - last_refresh < refresh_interval:
        return

    model = str(getattr(host, "model", "") or "")
    provider = str(getattr(host, "provider", "") or "")
    registered = register_with_gateway(session_id, model, provider)

    scene, task_id, execution_kind = current_gateway_presence_snapshot(host)
    subagent_summary = host._get_subagent_observability_snapshot()
    agent_role = current_cli_agent_role(host)
    scene_pushed = push_cli_agent_scene(
        scene,
        session_id=session_id,
        task_id=task_id,
        execution_kind=execution_kind,
        subagent_summary=subagent_summary,
        agent_role=agent_role,
    )
    if registered and scene_pushed:
        host._last_gateway_presence_refresh_at = now
        return

    host._last_gateway_presence_refresh_at = max(0.0, now - refresh_interval + 2.0)
