from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from ...infrastructure.gateway.executor import default_gateway_url
from .registry import registry


SCHEDULED_TASK_SCHEMA = {
    "name": "scheduled_task",
    "description": (
        "Manage the user's shared scheduled-task list. Use this for listing, creating, updating, "
        "pausing, resuming, or deleting tasks that must later be executed by the main CLI API-A "
        "scheduled runner. This tool manages plans only and never executes the task immediately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "update", "pause", "resume", "delete"],
            },
            "schedule_id": {"type": "string"},
            "title": {"type": "string"},
            "instruction": {"type": "string"},
            "schedule_type": {
                "type": "string",
                "enum": ["once", "daily", "weekly"],
            },
            "run_at": {
                "type": "string",
                "description": "ISO-8601 datetime with timezone for a one-time task.",
            },
            "time_of_day": {
                "type": "string",
                "description": "Local HH:MM time for daily or weekly tasks.",
            },
            "weekdays": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0, "maximum": 6},
                "description": "Weekly days where Monday is 0 and Sunday is 6.",
            },
            "timezone": {
                "type": "string",
                "description": "IANA timezone such as Asia/Shanghai.",
            },
            "include_completed": {"type": "boolean"},
        },
        "required": ["action"],
    },
}


def _request_json(path: str, *, method: str = "GET", payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    url = f"{default_gateway_url().rstrip('/')}/api/supervisor{path}"
    body = None
    headers: Dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            decoded = json.loads(response.read().decode("utf-8"))
            return dict(decoded) if isinstance(decoded, dict) else {"result": decoded}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail")
        except Exception:
            detail = None
        return {"status": "error", "error": detail or f"HTTP {exc.code}"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": str(exc)}


def scheduled_task_tool(**args: Any) -> str:
    action = str(args.get("action") or "").strip().lower()
    schedule_id = str(args.get("schedule_id") or "").strip()
    if action == "list":
        include_completed = "true" if args.get("include_completed", True) else "false"
        result = _request_json(f"/scheduled-tasks?include_completed={include_completed}")
    elif action == "create":
        payload = {
            key: args.get(key)
            for key in (
                "title", "instruction", "schedule_type", "run_at",
                "time_of_day", "weekdays", "timezone",
            )
            if args.get(key) not in (None, "", [])
        }
        payload.update({"created_by": "api_a", "requested_via": "cli_agent"})
        result = _request_json("/scheduled-tasks", method="POST", payload=payload)
    elif action in {"update", "pause", "resume", "delete"}:
        if not schedule_id:
            result = {"status": "error", "error": "schedule_id is required"}
        elif action == "update":
            changes = {
                key: args.get(key)
                for key in (
                    "title", "instruction", "schedule_type", "run_at",
                    "time_of_day", "weekdays", "timezone",
                )
                if args.get(key) not in (None, "", [])
            }
            result = _request_json(
                f"/scheduled-tasks/{urllib.parse.quote(schedule_id)}",
                method="PUT",
                payload=changes,
            )
        elif action == "delete":
            result = _request_json(
                f"/scheduled-tasks/{urllib.parse.quote(schedule_id)}",
                method="DELETE",
            )
        else:
            result = _request_json(
                f"/scheduled-tasks/{urllib.parse.quote(schedule_id)}/{action}",
                method="POST",
                payload={},
            )
    else:
        result = {"status": "error", "error": f"unsupported action: {action}"}
    return json.dumps(result, ensure_ascii=False)


def _handle_scheduled_task(args: Dict[str, Any], **_: Any) -> str:
    return scheduled_task_tool(**args)


registry.register(
    name="scheduled_task",
    toolset="scheduling",
    schema=SCHEDULED_TASK_SCHEMA,
    handler=_handle_scheduled_task,
    emoji="⏱",
)
