"""Typed HTTP client for the Goal Service."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from voidcube.infrastructure.config.runtime_paths import get_config_path


class GoalServiceError(RuntimeError):
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload))


def _service_url() -> str:
    try:
        import yaml
        raw = yaml.safe_load(get_config_path().read_text(encoding="utf-8")) or {}
        value = (raw.get("goal_manager") or {}).get("service_url")
        if value:
            return str(value).rstrip("/")
    except Exception:
        pass
    return os.getenv("GOAL_SERVICE_URL", "http://127.0.0.1:6003").rstrip("/")


def _service_token() -> str:
    try:
        import yaml
        raw = yaml.safe_load(get_config_path().read_text(encoding="utf-8")) or {}
        value = (raw.get("goal_manager") or {}).get("service_token")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return os.getenv("GOAL_SERVICE_TOKEN", "").strip()


class GoalClient:
    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self.base_url = (base_url or _service_url()).rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, *,
                query: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            from urllib.parse import urlencode
            url += "?" + urlencode([(key, value) for key, value in query.items() if value is not None], doseq=True)
        body = None
        headers = {"Accept": "application/json"}
        token = _service_token()
        if token:
            headers["X-Goal-Service-Token"] = token
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method), timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {"detail": raw.decode("utf-8", errors="replace")}
            raise GoalServiceError(exc.code, payload) from exc
        except URLError as exc:
            raise GoalServiceError(503, {"detail": "goal_service_unavailable"}) from exc

    def projects(self) -> dict[str, Any]:
        return self.request("GET", "/api/goals/projects")

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        actor = {
            key: args[key] for key in ("actor_type", "actor_id", "session_id")
            if args.get(key) not in (None, "")
        }
        if tool_name == "goal_project_get":
            return self.request("GET", f"/api/goals/projects/{args['projectId']}")
        if tool_name == "goal_project_create":
            return self.request("POST", "/api/goals/projects", {
                "name": args["name"], "description": args.get("description", ""),
                "created_by": args.get("createdBy", "agent"), "reason": args["reason"], **actor,
            })
        if tool_name == "goal_get_context":
            return self.request("GET", f"/api/goals/nodes/{args['nodeId']}/context")
        if tool_name == "goal_graph_query":
            return self.request("GET", f"/api/goals/projects/{args['projectId']}/graph", query={
                "start_node": args["startNode"], "depth": args.get("depth", 3),
                "edge_types": args.get("edgeTypes"),
            })
        if tool_name == "goal_node_create":
            payload = dict(args)
            payload.update({"project_id": args["projectId"], "node_type": args["type"], "reason": args["reason"]})
            payload.pop("projectId", None)
            payload.pop("type", None)
            payload["created_by"] = args.get("createdBy", "agent")
            return self.request("POST", "/api/goals/nodes", payload)
        if tool_name == "goal_node_update":
            return self.request("PATCH", f"/api/goals/nodes/{args['nodeId']}", {
                "expected_version": args["expectedVersion"], "patch": args["patch"],
                "reason": args["reason"], **actor,
            })
        if tool_name == "goal_node_delete":
            return self.request("DELETE", f"/api/goals/nodes/{args['nodeId']}", query={
                "reason": args["reason"], "cascade": args.get("cascade", False),
                "confirm_token": args.get("confirmToken"), **actor,
            })
        if tool_name == "goal_edge_create":
            return self.request("POST", "/api/goals/edges", {
                "source_id": args["sourceId"], "target_id": args["targetId"],
                "edge_type": args["edgeType"], "progress_weight": args.get("progressWeight", 1),
                "required": args.get("required", True), "reason": args["reason"], **actor,
            })
        if tool_name == "goal_edge_delete":
            return self.request("DELETE", f"/api/goals/edges/{args['edgeId']}", query={
                "reason": args["reason"], **actor,
            })
        if tool_name == "goal_batch_apply":
            return self.request("POST", "/api/goals/batch", {
                "project_id": args["projectId"], "reason": args["reason"],
                "operations": args["operations"], "created_by": args.get("createdBy", "agent"),
                "confirm_token": args.get("confirmToken"), **actor,
            })
        if tool_name == "goal_rollback":
            return self.request("POST", "/api/goals/rollback", {
                "batch_id": args["batchId"], "reason": args.get("reason", "rollback batch"),
                "confirm": args.get("confirm", False), **actor,
            })
        if tool_name == "goal_redo":
            return self.request("POST", "/api/goals/redo", {
                "project_id": args.get("projectId"), "batch_id": args.get("batchId"),
                "reason": args.get("reason", "redo batch"), **actor,
            })
        if tool_name == "goal_next_actions":
            return self.request("GET", f"/api/goals/projects/{args['projectId']}/next-actions", query={
                "limit": args.get("limit", 10),
                "filters": json.dumps(args.get("filters") or {}, ensure_ascii=False),
            })
        if tool_name == "goal_attach_evidence":
            return self.request("POST", f"/api/goals/nodes/{args['nodeId']}/evidence", {
                "evidence_type": args["evidenceType"], "title": args.get("title"),
                "content": args.get("content"), "uri": args.get("uri"),
                "created_by": args.get("createdBy", "agent"), "reason": args["reason"], **actor,
            })
        raise ValueError(f"unknown goal tool: {tool_name}")
