import aiohttp
import asyncio
import hmac
import json
import logging
import os
import secrets
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from systems.runtime_task_profile import derive_runtime_task_profile
from systems.supervisor.autonomous_chain_contract import (
    AUTONOMOUS_CHAIN_TASKS_ROUTE,
    autonomous_chain_task_decision_route,
    autonomous_chain_task_lease_validation_route,
    autonomous_chain_task_route,
)
from systems.runtime_thresholds import (
    DEFAULT_ACTIVE_CLI_STALE_AFTER_SECONDS,
    DEFAULT_CLI_SESSION_TTL_SECONDS,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("internal_gateway")


class ServiceInfo(BaseModel):
    service_id: str
    service_name: str
    service_type: str
    address: str
    health_endpoint: str
    metadata: Dict[str, Any] = {}
    registered_at: datetime = None
    last_health_check: datetime = None
    healthy: bool = True


class RouteEntry(BaseModel):
    path_prefix: str
    target_service: str
    target_instance: str = None
    weight: int = 100
    enabled: bool = True


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6000
    auth_token: Optional[str] = None
    log_level: str = "INFO"
    activity_log_limit: int = 200
    activity_log_path: str = ""  # disk path; "" = auto-derive from VoidCube home
    session_ttl_seconds: int = DEFAULT_CLI_SESSION_TTL_SECONDS
    active_cli_stale_after_seconds: int = DEFAULT_ACTIVE_CLI_STALE_AFTER_SECONDS


class AgentRequest(BaseModel):
    session_id: Optional[str] = None
    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


class AgentResponse(BaseModel):
    session_id: str
    response: Dict[str, Any]
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, Any]] = None


class ActivityTouchRequest(BaseModel):
    activity_kind: str
    source_service: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = {}


class SessionRegisterRequest(BaseModel):
    session_id: str
    model: Optional[str] = None
    provider: Optional[str] = None
    source: str = "cli"
    owner_id: str = "local-user"
    workspace_id: str = "default"


class InternalGateway:
    SERVICE_ID_HEADER = "x-voidcube-service-id"
    SERVICE_TOKEN_HEADER = "x-voidcube-service-token"
    SESSION_ID_HEADER = "x-voidcube-session-id"
    SESSION_TOKEN_HEADER = "x-voidcube-session-token"
    MEMORY_ACTOR_HEADER = "x-voidcube-memory-actor"
    GATEWAY_TOKEN_HEADER = "x-voidcube-gateway-token"
    SERVICE_MEMORY_ACTORS = {
        "agent": ("api_a", frozenset({"api_a"})),
        "supervisor": (
            "stellar_companion",
            frozenset({"stellar_companion", "stellar_auto", "governor"}),
        ),
        "stellar_companion": (
            "stellar_companion",
            frozenset({"stellar_companion"}),
        ),
        "stellar_auto": ("stellar_auto", frozenset({"stellar_auto"})),
        "governor": ("governor", frozenset({"governor"})),
        "executor": ("execution", frozenset({"execution"})),
        "memory": (
            "memory_maintenance",
            frozenset({"memory_maintenance"}),
        ),
    }
    ROUTE_PREFIX_BY_SERVICE_TYPE = {
        "memory": "/mem/",
        "supervisor": "/supervisor/",
        "executor": "/executor/",
    }
    UPSTREAM_PREFIX_BY_SERVICE_TYPE = {
        "memory": "/",
        "supervisor": "/",
        "executor": "/executor/",
    }
    ROUTED_SINGLETON_SERVICE_TYPES = frozenset(ROUTE_PREFIX_BY_SERVICE_TYPE)

    def __init__(self, config: GatewayConfig = None):
        self.config = config or GatewayConfig()
        self.app = FastAPI(title="VoidCube Internal Gateway", version="1.0")
        self._services: Dict[str, ServiceInfo] = {}
        self._service_credentials: Dict[str, str] = {}
        self._session_credentials: Dict[str, str] = {}
        self._routes: Dict[str, RouteEntry] = {}
        self._active_cli_session_id: str | None = None
        # Maps a reporting CLI session_id to the agent lane it last wrote
        # ("supervisor_task" | "user_chat"), so an idle push from that session
        # clears the correct lane instead of leaving stale subagent counts.
        self._agent_session_lane: Dict[str, str] = {}
        self._autonomous_chain_gate_active: bool = False
        self._request_counter = 0
        # Tier 1 memory service URL (lazy-resolved from registered services)
        self._memory_service_url: str | None = None
        # NOTE(SB-03): Session cache is body-runtime state, not gateway operations
        # state.  Long-term session ownership should belong to the agent body
        # instances.  The gateway should only hold routing metadata.  TTL eviction
        # (SB-04) mitigates unbounded growth for now.
        self._agent_session_cache: Dict[str, Dict[str, Any]] = {}
        self._session_ttl_seconds: int = max(1, int(self.config.session_ttl_seconds))
        self._active_cli_stale_after_seconds: int = max(
            1,
            int(self.config.active_cli_stale_after_seconds),
        )
        self._activity_log: Deque[Dict[str, Any]] = deque(
            maxlen=max(int(self.config.activity_log_limit), 1)
        )
        self._activity_state: Dict[str, Any] = {
            "last_user_request_at": None,
            "last_agent_work_at": None,
            "last_memory_task_at": None,
            "last_self_learning_activity_at": None,
            "last_autonomous_chain_activity_at": None,
            "last_autonomous_chain_plan_at": None,
            "last_autonomous_chain_execute_at": None,
            "user_request_count": 0,
            "agent_work_count": 0,
            "memory_task_count": 0,
            "self_learning_activity_count": 0,
            "autonomous_chain_activity_count": 0,
            "autonomous_chain_plan_count": 0,
            "autonomous_chain_execute_count": 0,
            "memory_write_failure_count": 0,
            "last_memory_write_failure_at": None,
            "error_count": 0,
            "uncertainty_high_count": 0,
            "recent_metadata": {
                "user_request": None,
                "agent_work": None,
                "memory_task": None,
                "self_learning": None,
                "autonomous_chain": None,
                "autonomous_chain_plan": None,
                "autonomous_chain_execute": None,
                "memory_write_failure": None,
            },
        }
        # ── Scene cache (baseline §8.1 — per-reporter) ──
        # The gateway never decides a reporter's scene; it only relays
        # what each registered service reports.  Cache is best-effort
        # and re-validated on every /admin/scenes fetch.
        self._scenes_cache: Dict[str, Dict[str, Any]] = {
            "supervisor": {
                "scene": "idle",
                "title": None,
                "summary": None,
                "service_id": None,
                "address": None,
                "reachable": False,
                "last_fetched_at": None,
            },
            "agent": {
                "scene": "idle",
                "scene_projection_scope": "agent_top_level_projection",
                "canonical_lanes": ["supervisor_task", "user_chat"],
                "lane_contract": {
                    "supervisor_task": "autonomous_chain_observation",
                    "user_chat": "user_chain_status",
                },
                "scene_task_id": None,
                "subagent_foreground_count": 0,
                "subagent_background_count": 0,
                "subagent_total_count": 0,
                "subagent_focus_task_id": None,
                "subagent_focus_tool": None,
                "subagent_focus_preview": None,
                "service_id": None,
                "address": None,
                "slot_id": None,
                "source_service": None,
                "reachable": False,
                "last_fetched_at": None,
                # Per-role lanes keep user-chat and supervisor-task activity
                # separated even when they share the same API-A process.
                "lanes": {
                    "supervisor_task": self._empty_agent_lane(),
                    "user_chat": self._empty_agent_lane(),
                },
            },
            "executor": {
                "scene": "idle",
                "service_id": None,
                "address": None,
                "reachable": False,
                "last_fetched_at": None,
            },
        }
        # Auto-derive persistence path when not explicitly configured.
        # Skip under pytest to avoid test-isolation issues from shared
        # persisted state across gateway test cases.
        if not self.config.activity_log_path and not os.environ.get("PYTEST_CURRENT_TEST"):
            from VoidCube_core.constants import get_VoidCube_home
            run_dir = get_VoidCube_home() / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            self.config.activity_log_path = str(run_dir / "gateway-activity.json")
        self._setup_routes()
        self._load_activity_state()

    @classmethod
    def _normalize_gateway_activity_kind(cls, activity_kind: str) -> str:
        normalized = str(activity_kind or "").strip().lower()
        return {
            "self_evolution": "autonomous_chain",
            "self_evolution_plan": "autonomous_chain_plan",
            "self_evolution_execute": "autonomous_chain_execute",
        }.get(normalized, normalized)

    @staticmethod
    def _bearer_token(request: Request) -> str:
        authorization = str(request.headers.get("authorization") or "").strip()
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            return token.strip()
        return ""

    def _authorize_registration(self, request: Request) -> None:
        expected = str(self.config.auth_token or "").strip()
        if not expected:
            return
        supplied = (
            self._bearer_token(request)
            or str(request.headers.get(self.GATEWAY_TOKEN_HEADER) or "").strip()
        )
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=401,
                detail="Gateway registration authentication failed",
            )

    @staticmethod
    def _new_credential() -> str:
        return secrets.token_urlsafe(32)

    def _authenticate_memory_caller(self, request: Request) -> str:
        service_id = str(request.headers.get(self.SERVICE_ID_HEADER) or "").strip()
        service_token = str(
            request.headers.get(self.SERVICE_TOKEN_HEADER) or ""
        ).strip()
        if service_id or service_token:
            service = self._services.get(service_id)
            expected = self._service_credentials.get(service_id, "")
            if (
                service is None
                or not service_token
                or not expected
                or not hmac.compare_digest(service_token, expected)
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid Gateway service credential",
                )
            policy = self.SERVICE_MEMORY_ACTORS.get(service.service_type)
            if policy is None:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Service type {service.service_type} has no Memory capability"
                    ),
                )
            default_actor, allowed_actors = policy
            requested_actor = str(
                request.headers.get(self.MEMORY_ACTOR_HEADER) or default_actor
            ).strip()
            if requested_actor not in allowed_actors:
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"Service type {service.service_type} cannot assume "
                        f"Memory actor {requested_actor}"
                    ),
                )
            return requested_actor

        session_id = str(request.headers.get(self.SESSION_ID_HEADER) or "").strip()
        session_token = str(
            request.headers.get(self.SESSION_TOKEN_HEADER) or ""
        ).strip()
        if session_id or session_token:
            expected = self._session_credentials.get(session_id, "")
            if (
                session_id not in self._agent_session_cache
                or not session_token
                or not expected
                or not hmac.compare_digest(session_token, expected)
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid Gateway session credential",
                )
            return "api_a"

        raise HTTPException(
            status_code=401,
            detail="Authenticated service or session identity is required for Memory",
        )

    def _session_memory_scope(self, request: Request) -> dict[str, str] | None:
        """Return the scope bound during session registration, if session-authenticated."""
        session_id = str(request.headers.get(self.SESSION_ID_HEADER) or "").strip()
        if not session_id or session_id not in self._agent_session_cache:
            return None
        session = self._agent_session_cache[session_id]
        return {
            "owner_id": str(session.get("owner_id") or "local-user"),
            "workspace_id": str(session.get("workspace_id") or "default"),
        }

    @staticmethod
    def _inject_memory_actor(
        body: bytes,
        query_params: list[tuple[str, str]],
        *,
        method: str,
        memory_actor: str,
        memory_scope: dict[str, str] | None = None,
    ) -> tuple[bytes, list[tuple[str, str]]]:
        query_params = [
            (key, value)
            for key, value in query_params
            if str(key).lower()
            not in {"memory_actor", "owner_id", "workspace_id"}
        ]
        query_params.append(("memory_actor", memory_actor))
        if memory_scope:
            query_params.extend(memory_scope.items())

        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Memory requests with a body must use JSON",
                ) from exc
            if not isinstance(payload, dict):
                raise HTTPException(
                    status_code=400,
                    detail="Memory request JSON must be an object",
                )
            payload["memory_actor"] = memory_actor
            if memory_scope:
                payload.update(memory_scope)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        elif method.upper() in {"POST", "PUT", "PATCH"}:
            body = json.dumps(
                {"memory_actor": memory_actor, **(memory_scope or {})}
            ).encode("utf-8")
        return body, query_params

    def _setup_routes(self):
        self.app.add_api_route("/", self.health_check, methods=["GET"])
        self.app.add_api_route("/api/{path:path}", self.route_request, methods=["GET", "POST", "PUT", "DELETE"])
        
        self.app.add_api_route("/admin/services", self.list_services, methods=["GET"])
        self.app.add_api_route("/admin/services/{service_id}", self.get_service, methods=["GET"])
        self.app.add_api_route("/admin/services/{service_id}", self.remove_service, methods=["DELETE"])
        
        self.app.add_api_route("/admin/routes", self.list_routes, methods=["GET"])
        self.app.add_api_route("/admin/routes", self.add_route, methods=["POST"])
        self.app.add_api_route("/admin/routes/{path_prefix}", self.update_route, methods=["PUT"])
        self.app.add_api_route("/admin/routes/{path_prefix}", self.delete_route, methods=["DELETE"])
        
        self.app.add_api_route("/admin/body/status", self.get_body_status, methods=["GET"])
        self.app.add_api_route("/admin/activity", self.get_activity_status, methods=["GET"])
        self.app.add_api_route("/admin/activity/log", self.get_activity_log, methods=["GET"])
        self.app.add_api_route("/admin/activity/clear", self.clear_activity_state, methods=["POST"])
        self.app.add_api_route("/admin/activity/touch", self.touch_activity, methods=["POST"])
        self.app.add_api_route("/admin/autonomous-chain-gate", self.set_autonomous_chain_gate, methods=["POST"])
        # ── Scene aggregation (baseline §8.1) ──
        # Scene is per-reporter, not global.  The gateway collects the
        # supervisor / active-agent / executor scenes and presents a
        # three-segment view for the CLI status bar.  Each reporter
        # declares its own scene only; the gateway never reinterprets.
        self.app.add_api_route("/admin/scenes", self.get_scenes, methods=["GET"])
        self.app.add_api_route("/admin/scenes/refresh", self.refresh_scenes, methods=["POST"])

        self.app.add_api_route("/register", self.register_service, methods=["POST"])
        self.app.add_api_route("/health/{service_id}", self.update_health, methods=["POST"])
        
        self.app.add_api_route("/v1/chat/completions", self.chat_completions_proxy, methods=["POST"])
        self.app.add_api_route("/v1/agent/query", self.agent_query_proxy, methods=["POST"])
        self.app.add_api_route("/v1/sessions/register", self.register_session, methods=["POST"])
        self.app.add_api_route("/v1/sessions/{session_id}", self.get_session_info, methods=["GET"])
        self.app.add_api_route("/v1/sessions/{session_id}", self.delete_session, methods=["DELETE"])
        self.app.add_api_route("/v1/tasks", self.get_approved_tasks, methods=["GET"])
        self.app.add_api_route("/v1/tasks/{task_id}/decision", self.decide_task, methods=["POST"])
        self.app.add_api_route("/v1/tasks/{task_id}/complete", self.complete_task, methods=["POST"])
        self.app.add_api_route(
            "/v1/tasks/{task_id}/lease/validate",
            self.validate_task_execution_lease,
            methods=["POST"],
        )
        self.app.add_api_route("/v1/body/improvement-report", self.forward_improvement_report, methods=["POST"])
        self.app.add_api_route("/admin/traces/{trace_id}", self.get_trace, methods=["GET"])

    async def health_check(self):
        self._evict_stale_sessions()
        active_cli_executor = self._build_active_cli_executor_snapshot()
        registered_services = {
            "total": len(self._services),
            "agents": len([s for s in self._services.values() if s.service_type == "agent"]),
            "memory": 1 if any(s.service_type == "memory" for s in self._services.values()) else 0,
            "supervisor": 1 if any(s.service_type == "supervisor" for s in self._services.values()) else 0,
            "executor": 1 if any(s.service_type == "executor" for s in self._services.values()) else 0,
        }

        return {
            "status": "healthy",
            "gateway_id": "voidcube-internal-gateway",
            "timestamp": datetime.now().isoformat(),
            "request_count": self._request_counter,
            "active_cli_executor": active_cli_executor,
            "body_slots": self._list_body_slots(),
            "registered_services": registered_services,
            "executor_access_policy": self._build_executor_access_policy(),
            "activity": self._build_activity_snapshot(),
            "routes": [self._serialize_route(route) for route in self._routes.values()]
        }

    def _serialize_body_service(self, service: ServiceInfo) -> Dict[str, Any]:
        return {
            "service_id": service.service_id,
            "slot_id": service.metadata.get("slot_id"),
            "body_version": service.metadata.get("body_version"),
            "service_name": service.service_name,
            "address": service.address,
            "healthy": service.healthy,
            "registered_at": service.registered_at.isoformat() if service.registered_at else None,
        }

    def _list_body_slots(self) -> List[Dict[str, Any]]:
        return [
            self._serialize_body_service(service)
            for service in self._services.values()
            if service.service_type == "agent"
        ]

    def _build_active_cli_executor_snapshot(self) -> Optional[Dict[str, Any]]:
        session_id = self._active_cli_session_id
        if not session_id:
            return None
        if session_id not in self._agent_session_cache:
            return None
        snapshot = self._serialize_agent_session_metadata(session_id)
        agent_scene = self._scenes_cache.get("agent") or {}
        lanes = agent_scene.get("lanes") if isinstance(agent_scene.get("lanes"), dict) else {}
        lane_key = self._agent_session_lane.get(session_id)
        lane = lanes.get(lane_key) if lane_key else None
        if isinstance(lane, dict):
            self._apply_agent_scene_projection_to_snapshot(
                snapshot,
                lane,
                projection_scope="agent_lane",
                agent_lane=lane_key,
            )
        snapshot.update(self._build_session_lease_snapshot(session_id))
        return snapshot

    @staticmethod
    def _apply_agent_scene_projection_to_snapshot(
        snapshot: Dict[str, Any],
        source: Dict[str, Any],
        *,
        projection_scope: str,
        agent_lane: Optional[str],
    ) -> None:
        snapshot["scene"] = source.get("scene")
        snapshot["scene_task_id"] = source.get("scene_task_id")
        snapshot["scene_changed_at"] = source.get("scene_changed_at") or source.get("last_fetched_at")
        snapshot["subagent_foreground_count"] = source.get("subagent_foreground_count", 0)
        snapshot["subagent_background_count"] = source.get("subagent_background_count", 0)
        snapshot["subagent_total_count"] = source.get("subagent_total_count", 0)
        snapshot["subagent_focus_task_id"] = source.get("subagent_focus_task_id")
        snapshot["subagent_focus_tool"] = source.get("subagent_focus_tool")
        snapshot["subagent_focus_preview"] = source.get("subagent_focus_preview")
        snapshot["scene_projection_scope"] = projection_scope
        snapshot["agent_lane"] = agent_lane

    def _build_session_lease_snapshot(self, session_id: str) -> Dict[str, Any]:
        session = dict(self._agent_session_cache.get(session_id) or {})
        last_used_at = session.get("last_used_at")
        if isinstance(last_used_at, datetime):
            idle_seconds = max(0, int((datetime.now() - last_used_at).total_seconds()))
        else:
            idle_seconds = self._session_ttl_seconds
        is_stale = idle_seconds >= self._active_cli_stale_after_seconds
        return {
            "idle_seconds": idle_seconds,
            "stale_after_seconds": self._active_cli_stale_after_seconds,
            "is_stale": is_stale,
            "lease_status": "stale" if is_stale else "healthy",
        }

    def _build_executor_access_policy(self) -> Dict[str, Any]:
        return {
            "preferred_gateway_prefix": "/api/executor",
            "direct_executor_prefix": "/executor",
            "failure_mode": "executor_required",
        }

    def _serialize_route(self, route: RouteEntry) -> Dict[str, Any]:
        payload = route.dict()
        if route.path_prefix == "/executor/":
            payload["route_policy"] = {
                "status": "preferred_execution_surface",
                "preferred_gateway_prefix": "/api/executor",
                "direct_executor_prefix": "/executor",
            }
        elif route.path_prefix == "/supervisor/":
            payload["route_policy"] = {
                "status": "governance_runtime_surface",
                "service_role": "planning_governance_runtime",
            }
        return payload

    def _serialize_service(self, service: ServiceInfo) -> Dict[str, Any]:
        payload = service.dict()
        if service.service_type == "executor":
            payload["executor_access_policy"] = self._build_executor_access_policy()
        return payload

    @staticmethod
    def _empty_agent_lane() -> Dict[str, Any]:
        """A blank per-role agent lane (subagent counts zeroed, scene idle)."""
        return {
            "scene": "idle",
            "scene_task_id": None,
            "execution_kind": None,
            "subagent_foreground_count": 0,
            "subagent_background_count": 0,
            "subagent_total_count": 0,
            "subagent_focus_task_id": None,
            "subagent_focus_tool": None,
            "subagent_focus_preview": None,
            "session_id": None,
            "reachable": False,
            "last_fetched_at": None,
        }

    @staticmethod
    def _resolve_agent_lane(agent_role: Any, scene: str) -> str:
        """Decide which lane an agent_scene report belongs to.

        Prefers the explicit ``agent_role`` tag from the reporter; falls back to
        a scene heuristic for older reporters that don't send it yet
        (learning/code_editing => supervisor_task, executing => user_chat).
        Defaults to user_chat when nothing is decisive.
        """
        role = str(agent_role or "").strip().lower()
        if role in ("supervisor_task", "user_chat"):
            return role
        if scene in ("learning", "code_editing"):
            return "supervisor_task"
        return "user_chat"

    def _update_agent_lane(
        self,
        *,
        session_id: Optional[str],
        agent_role: Any,
        scene: str,
        metadata: Dict[str, Any],
        now: datetime,
    ) -> None:
        """Route an agent_scene report into its per-role lane (additive).

        Keeps the two reporters (supervisor-task CLI vs user-chat CLI) from
        overwriting each other's subagent view. The top-level ``agent`` slot
        still carries the most recent aggregate snapshot for coarse scene-bar
        consumers, while ``lanes`` preserves the real separation.
        """
        lanes = self._scenes_cache["agent"].setdefault(
            "lanes",
            {
                "supervisor_task": self._empty_agent_lane(),
                "user_chat": self._empty_agent_lane(),
            },
        )
        sid = str(session_id or "").strip()

        # An idle report clears whichever lane this session last owned, so stale
        # subagent counts don't linger after the session goes quiet.
        if scene == "idle":
            owned = self._agent_session_lane.get(sid) if sid else None
            if owned and owned in lanes:
                blank = self._empty_agent_lane()
                blank["last_fetched_at"] = now.isoformat()
                blank["session_id"] = sid or None
                lanes[owned] = blank
            return

        lane_key = self._resolve_agent_lane(agent_role, scene)
        fg = max(0, int(metadata.get("subagent_foreground_count") or 0))
        bg = max(0, int(metadata.get("subagent_background_count") or 0))
        total = max(fg + bg, int(metadata.get("subagent_total_count") or 0))
        lanes[lane_key] = {
            "scene": scene,
            "scene_task_id": metadata.get("task_id"),
            "execution_kind": metadata.get("execution_kind"),
            "subagent_foreground_count": fg,
            "subagent_background_count": bg,
            "subagent_total_count": total,
            "subagent_focus_task_id": metadata.get("subagent_focus_task_id"),
            "subagent_focus_tool": metadata.get("subagent_focus_tool"),
            "subagent_focus_preview": metadata.get("subagent_focus_preview"),
            "session_id": sid or None,
            "reachable": True,
            "last_fetched_at": now.isoformat(),
        }
        if sid:
            self._agent_session_lane[sid] = lane_key

    def _clear_agent_session_lane(self, session_id: Optional[str]) -> None:
        """Drop a session from the lane map and blank the lane it owned.

        Called when a session is deleted or reaped, so a departed session's
        subagent counts don't linger in its lane and the map doesn't leak.
        """
        sid = str(session_id or "").strip()
        if not sid:
            return
        owned = self._agent_session_lane.pop(sid, None)
        if not owned:
            return
        lanes = (self._scenes_cache.get("agent") or {}).get("lanes")
        if isinstance(lanes, dict) and owned in lanes:
            lanes[owned] = self._empty_agent_lane()

    def _build_activity_snapshot(self) -> Dict[str, Any]:
        return {
            "last_user_request_at": (
                self._activity_state["last_user_request_at"].isoformat()
                if self._activity_state["last_user_request_at"]
                else None
            ),
            "last_agent_work_at": (
                self._activity_state["last_agent_work_at"].isoformat()
                if self._activity_state["last_agent_work_at"]
                else None
            ),
            "last_memory_task_at": (
                self._activity_state["last_memory_task_at"].isoformat()
                if self._activity_state["last_memory_task_at"]
                else None
            ),
            "last_self_learning_activity_at": (
                self._activity_state["last_self_learning_activity_at"].isoformat()
                if self._activity_state["last_self_learning_activity_at"]
                else None
            ),
            "last_autonomous_chain_activity_at": (
                self._activity_state["last_autonomous_chain_activity_at"].isoformat()
                if self._activity_state["last_autonomous_chain_activity_at"]
                else None
            ),
            "last_autonomous_chain_plan_at": (
                self._activity_state["last_autonomous_chain_plan_at"].isoformat()
                if self._activity_state["last_autonomous_chain_plan_at"]
                else None
            ),
            "last_autonomous_chain_execute_at": (
                self._activity_state["last_autonomous_chain_execute_at"].isoformat()
                if self._activity_state["last_autonomous_chain_execute_at"]
                else None
            ),
            "last_memory_write_failure_at": (
                self._activity_state["last_memory_write_failure_at"].isoformat()
                if self._activity_state["last_memory_write_failure_at"]
                else None
            ),
            "counts": {
                "user_request_count": self._activity_state["user_request_count"],
                "agent_work_count": self._activity_state["agent_work_count"],
                "memory_task_count": self._activity_state["memory_task_count"],
                "self_learning_activity_count": self._activity_state["self_learning_activity_count"],
                "autonomous_chain_activity_count": self._activity_state["autonomous_chain_activity_count"],
                "autonomous_chain_plan_count": self._activity_state["autonomous_chain_plan_count"],
                "autonomous_chain_execute_count": self._activity_state["autonomous_chain_execute_count"],
                "memory_write_failure_count": self._activity_state["memory_write_failure_count"],
                "error_count": self._activity_state["error_count"],
                "uncertainty_high_count": self._activity_state["uncertainty_high_count"],
            },
            "recent_metadata": dict(self._activity_state["recent_metadata"]),
            "active_sessions": len(self._agent_session_cache),
            "active_cli_executor": self._build_active_cli_executor_snapshot(),
        }

    def _serialize_agent_session_metadata(self, session_id: str) -> Dict[str, Any]:
        session = dict(self._agent_session_cache.get(session_id) or {})
        for key in ("created_at", "last_used_at"):
            value = session.get(key)
            if isinstance(value, datetime):
                session[key] = value.isoformat()
        session["session_id"] = session_id
        session["is_active_cli_executor"] = session_id == self._active_cli_session_id
        session.update(self._build_session_lease_snapshot(session_id))
        return session

    def _touch_session(self, session_id: Optional[str], *, source: str = "cli") -> None:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return
        now = datetime.now()
        existing = dict(self._agent_session_cache.get(normalized_session_id) or {})
        self._agent_session_cache[normalized_session_id] = {
            **existing,
            "created_at": existing.get("created_at") or now,
            "last_used_at": now,
            "message_count": int(existing.get("message_count") or 0),
            "source": existing.get("source") or source,
        }

    def _touch_activity(
        self,
        activity_kind: str,
        *,
        source_service: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.utcnow()
        normalized = (activity_kind or "").strip().lower()
        activity_metadata = self._build_activity_metadata(
            source_service=source_service,
            session_id=session_id,
            metadata=metadata,
        )
        self._touch_session(session_id, source=str(source_service or "cli"))
        supported = True

        if normalized == "user_request":
            self._activity_state["last_user_request_at"] = now
            self._activity_state["user_request_count"] += 1
            self._activity_state["recent_metadata"]["user_request"] = activity_metadata
        elif normalized == "agent_scene":
            scene = self._validate_scene(
                (activity_metadata or {}).get("scene"),
                self.AGENT_LEGAL_SCENES,
            )
            if session_id and scene != "idle":
                self._active_cli_session_id = session_id
            active_session_id = str(self._active_cli_session_id or "").strip()
            if session_id and active_session_id and session_id != active_session_id and scene == "idle":
                pass
            else:
                cache = self._scenes_cache["agent"]
                cache["scene"] = scene
                cache["scene_task_id"] = (activity_metadata or {}).get("task_id")
                cache["subagent_foreground_count"] = max(
                    0,
                    int((activity_metadata or {}).get("subagent_foreground_count") or 0),
                )
                cache["subagent_background_count"] = max(
                    0,
                    int((activity_metadata or {}).get("subagent_background_count") or 0),
                )
                cache["subagent_total_count"] = max(
                    cache["subagent_foreground_count"] + cache["subagent_background_count"],
                    int((activity_metadata or {}).get("subagent_total_count") or 0),
                )
                cache["subagent_focus_task_id"] = (activity_metadata or {}).get("subagent_focus_task_id")
                cache["subagent_focus_tool"] = (activity_metadata or {}).get("subagent_focus_tool")
                cache["subagent_focus_preview"] = (activity_metadata or {}).get("subagent_focus_preview")
                cache["scene_changed_at"] = now.isoformat()
                cache["source_service"] = source_service or (activity_metadata or {}).get("source_service")
                cache["reachable"] = True
                cache["last_fetched_at"] = now.isoformat()
            # Additive per-role lane routing (does not affect the top-level slot
            # above). Runs for idle too, so a session's lane gets cleared.
            self._update_agent_lane(
                session_id=session_id,
                agent_role=(activity_metadata or {}).get("agent_role"),
                scene=scene,
                metadata=dict(activity_metadata or {}),
                now=now,
            )
        elif normalized == "agent_work":
            self._activity_state["last_agent_work_at"] = now
            self._activity_state["agent_work_count"] += 1
            self._activity_state["recent_metadata"]["agent_work"] = activity_metadata
        elif normalized == "memory_task":
            self._activity_state["last_memory_task_at"] = now
            self._activity_state["memory_task_count"] += 1
            self._activity_state["recent_metadata"]["memory_task"] = activity_metadata
        elif normalized == "memory_write_failure":
            self._activity_state["last_memory_write_failure_at"] = now
            self._activity_state["memory_write_failure_count"] += 1
            self._activity_state["recent_metadata"]["memory_write_failure"] = activity_metadata
        elif normalized == "self_learning":
            self._activity_state["last_self_learning_activity_at"] = now
            self._activity_state["self_learning_activity_count"] += 1
            self._activity_state["recent_metadata"]["self_learning"] = activity_metadata
        elif normalized == "autonomous_chain":
            self._activity_state["last_autonomous_chain_activity_at"] = now
            self._activity_state["autonomous_chain_activity_count"] += 1
            self._activity_state["recent_metadata"]["autonomous_chain"] = activity_metadata
        elif normalized == "autonomous_chain_plan":
            self._activity_state["last_autonomous_chain_activity_at"] = now
            self._activity_state["last_autonomous_chain_plan_at"] = now
            self._activity_state["autonomous_chain_activity_count"] += 1
            self._activity_state["autonomous_chain_plan_count"] += 1
            self._activity_state["recent_metadata"]["autonomous_chain"] = activity_metadata
            self._activity_state["recent_metadata"]["autonomous_chain_plan"] = activity_metadata
        elif normalized == "autonomous_chain_execute":
            self._activity_state["last_autonomous_chain_activity_at"] = now
            self._activity_state["last_autonomous_chain_execute_at"] = now
            self._activity_state["autonomous_chain_activity_count"] += 1
            self._activity_state["autonomous_chain_execute_count"] += 1
            self._activity_state["recent_metadata"]["autonomous_chain"] = activity_metadata
            self._activity_state["recent_metadata"]["autonomous_chain_execute"] = activity_metadata
        elif normalized == "agent_error":
            self._activity_state["error_count"] += 1
            self._activity_state["recent_metadata"]["agent_error"] = activity_metadata
        elif normalized == "uncertainty_high":
            self._activity_state["uncertainty_high_count"] += 1
            self._activity_state["recent_metadata"]["uncertainty_high"] = activity_metadata
        else:
            supported = False
            logger.debug(
                "Ignoring unsupported gateway activity kind=%s source=%s session=%s",
                activity_kind,
                source_service,
                session_id,
            )
        if supported:
            self._record_activity_log_event(
                activity_kind=normalized,
                recorded_at=now,
                source_service=source_service,
                session_id=session_id,
                metadata=activity_metadata,
            )
            self._persist_activity_state()

    def _load_activity_state(self) -> None:
        """Restore activity state + log from disk (best-effort)."""
        path = self.config.activity_log_path
        if not path:
            return
        try:
            raw = json.loads(open(path, encoding="utf-8").read())
        except Exception:
            return
        if not isinstance(raw, dict):
            return
        saved_state = raw.get("state")
        if isinstance(saved_state, dict):
            normalized_state = dict(saved_state)
            # Timestamp fields are stored as ISO strings; restore to datetime
            _ts_fields = {k for k in self._activity_state if k.startswith("last_")}
            for key in self._activity_state:
                if key in normalized_state:
                    value = normalized_state[key]
                    if key in _ts_fields and isinstance(value, str):
                        try:
                            value = datetime.fromisoformat(value)
                        except ValueError:
                            value = None
                    self._activity_state[key] = value
        saved_events = raw.get("events")
        if isinstance(saved_events, list):
            # Truncate to configured limit (G-05: rotation)
            limit = max(int(self.config.activity_log_limit), 1)
            for event in reversed(saved_events[-limit:]):
                if isinstance(event, dict):
                    normalized_event = dict(event)
                    normalized_event["activity_kind"] = self._normalize_gateway_activity_kind(
                        normalized_event.get("activity_kind") or ""
                    )
                    self._activity_log.appendleft(normalized_event)
        # Counters are runtime-only — reset on restart to avoid stale
        # accumulation.  Timestamps and log events survive for idle-window
        # continuity after a quick restart.
        for key in self._activity_state:
            if key.endswith("_count"):
                self._activity_state[key] = 0

    def _persist_activity_state(self, *, force: bool = False) -> None:
        """Write activity state + log to disk (debounced, fire-and-forget)."""
        path = self.config.activity_log_path
        if not path:
            return
        # Debounce: skip writes within 2s of the last persist to avoid
        # high-frequency disk I/O on every activity touch (G-02).
        import time as _time
        now = _time.monotonic()
        last = getattr(self, '_last_persist_ts', 0.0)
        if not force and now - last < 2.0:
            return
        self._last_persist_ts = now
        try:
            payload = {
                "state": self._activity_state,
                "events": list(self._activity_log),
                "written_at": datetime.utcnow().isoformat(),
            }
            # Atomic write: temp file + rename
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
            os.replace(tmp, path)
        except Exception:
            pass  # best-effort; never block the request for persistence

    def _record_activity_log_event(
        self,
        *,
        activity_kind: str,
        recorded_at: datetime,
        source_service: Optional[str],
        session_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        event = {
            "activity_id": str(uuid.uuid4()),
            "activity_kind": activity_kind,
            "recorded_at": recorded_at.isoformat(),
            "source_service": source_service,
            "session_id": session_id,
            "metadata": dict(metadata or {}),
        }
        self._activity_log.appendleft(event)
        return event

    def _build_activity_metadata(
        self,
        *,
        source_service: Optional[str],
        session_id: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        payload = dict(metadata or {})
        extracted = self._extract_activity_metadata_from_payload(payload)
        payload.update(
            {
                key: value
                for key, value in extracted.items()
                if value is not None and key not in payload
            }
        )
        task_identity = self._build_task_identity_summary(payload)
        if task_identity:
            payload["task_identity"] = task_identity
        payload.pop("task_type", None)
        payload.pop("task_type_label", None)
        if source_service:
            payload.setdefault("source_service", source_service)
        if session_id:
            payload.setdefault("session_id", session_id)
        return payload or None

    def _runtime_activity_label(self, value: Any) -> Optional[str]:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        return {
            "self_learning": "自主学习",
            "self_evolution": "自主改进",
            "general_self_evolution": "通用自主改进",
            "memory_maintenance": "记忆维护",
            "body_upgrade": "替身升级",
            "body_switch": "身体切换",
            "body_improvement": "替身改进",
            "autonomous_chain": "自主链路",
            "autonomous_chain_plan": "自主链路规划",
            "autonomous_chain_execute": "自主链路执行",
        }.get(normalized, str(value or "").strip() or None)

    def _build_task_identity_summary(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return None

        title = str(payload.get("title") or payload.get("task_title") or "").strip() or None
        task_id = str(payload.get("task_id") or "").strip() or None
        task_type = str(payload.get("task_type") or "").strip() or None
        governance_task_type = str(payload.get("governance_task_type") or "").strip() or None
        task_family = str(payload.get("task_family") or "").strip() or None
        execution_kind = str(payload.get("execution_kind") or "").strip() or None
        requested_kind = str(payload.get("kind") or "").strip() or None
        task_type_label = self._runtime_activity_label(task_type)
        governance_task_type_label = self._runtime_activity_label(governance_task_type)
        task_family_label = self._runtime_activity_label(task_family)
        execution_kind_label = self._runtime_activity_label(execution_kind)
        requested_kind_label = self._runtime_activity_label(requested_kind)

        display_kind = requested_kind or execution_kind or task_family or governance_task_type or task_type
        display_label = (
            requested_kind_label
            or execution_kind_label
            or task_family_label
            or governance_task_type_label
            or task_type_label
        )
        if title:
            summary = f"{title} ({display_label or display_kind})" if (display_label or display_kind) else title
        elif task_id:
            summary = f"{task_id} ({display_label or display_kind})" if (display_label or display_kind) else task_id
        elif display_label or display_kind:
            summary = display_label or display_kind
        else:
            summary = None

        if not any((title, task_id, task_type, governance_task_type, task_family, execution_kind, summary)):
            return None

        return {
            "task_id": task_id,
            "title": title,
            "governance_task_type": governance_task_type,
            "task_family": task_family,
            "execution_kind": execution_kind,
            "requested_kind": requested_kind,
            "display_kind": display_kind,
            "governance_task_type_label": governance_task_type_label,
            "task_family_label": task_family_label,
            "execution_kind_label": execution_kind_label,
            "requested_kind_label": requested_kind_label,
            "display_label": display_label,
            "summary": summary,
        }

    def _extract_activity_metadata_from_payload(self, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}

        metadata: Dict[str, Any] = {}
        candidates = [payload]
        for key in ("execution_request", "metadata"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
        runtime_task_profile = payload.get("runtime_task_profile")
        if isinstance(runtime_task_profile, dict):
            candidates.append(runtime_task_profile)

        for candidate in candidates:
            for key in (
                "trace_id",
                "task_type",
                "governance_task_type",
                "task_family",
                "execution_kind",
                "decision_id",
                "task_id",
                "request_id",
                "kind",
                "source_actor",
                "conclusion_id",
            ):
                value = candidate.get(key)
                if value is not None and key not in metadata:
                    metadata[key] = value

        derived_profile = derive_runtime_task_profile(
            task_type=metadata.get("task_type"),
            governance_task_type=metadata.get("governance_task_type"),
            task_family=metadata.get("task_family"),
            execution_kind=metadata.get("execution_kind"),
            kind=metadata.get("kind"),
            default_task_family=None,
        )
        metadata.update(
            {
                key: value
                for key, value in derived_profile.items()
                if value is not None and key not in metadata
            }
        )
        # Ensure every activity has a trace_id for end-to-end observability.
        # When the caller provided one, keep it as-is (trace_id is an opaque
        # correlation identifier, not required to be a UUID).
        if "trace_id" not in metadata:
            metadata["trace_id"] = str(uuid.uuid4())
        for key in ("task_type", "governance_task_type", "task_family", "execution_kind", "kind"):
            label = self._runtime_activity_label(metadata.get(key))
            if label:
                metadata[f"{key}_label"] = label
        metadata.pop("task_type", None)
        metadata.pop("task_type_label", None)
        return metadata

    def _is_memory_write_activity(self, path: str, method: str) -> bool:
        normalized_method = str(method or "").upper()
        if normalized_method in {"GET", "HEAD", "OPTIONS"}:
            return False
        if normalized_method in {"PUT", "PATCH", "DELETE"}:
            return True

        normalized_path = "/" + str(path or "").strip("/").lower()
        read_suffixes = (
            "/recall",
            "/search",
            "/query",
            "/timeline",
            "/stats",
            "/status",
            "/health",
            "/usage",
        )
        if any(normalized_path.endswith(suffix) for suffix in read_suffixes):
            return False
        return True

    async def get_activity_status(self):
        return self._build_activity_snapshot()

    async def get_activity_log(self, limit: int = 50, trace_id: Optional[str] = None):
        events = list(self._activity_log)
        normalized_trace_id = str(trace_id).strip() if trace_id is not None else None
        if normalized_trace_id:
            events = [
                event
                for event in events
                if isinstance(event.get("metadata"), dict)
                and event["metadata"].get("trace_id") == normalized_trace_id
            ]
        bounded_limit = max(int(limit), 0)
        if bounded_limit:
            events = events[:bounded_limit]
        return {
            "status": "ok",
            "count": len(events),
            "limit": bounded_limit,
            "activity_log_limit": self._activity_log.maxlen,
            "events": events,
        }

    def _reset_activity_state(self) -> None:
        self._activity_state["last_user_request_at"] = None
        self._activity_state["last_agent_work_at"] = None
        self._activity_state["last_memory_task_at"] = None
        self._activity_state["last_self_learning_activity_at"] = None
        self._activity_state["last_autonomous_chain_activity_at"] = None
        self._activity_state["last_autonomous_chain_plan_at"] = None
        self._activity_state["last_autonomous_chain_execute_at"] = None
        self._activity_state["last_memory_write_failure_at"] = None
        self._activity_state["user_request_count"] = 0
        self._activity_state["agent_work_count"] = 0
        self._activity_state["memory_task_count"] = 0
        self._activity_state["self_learning_activity_count"] = 0
        self._activity_state["autonomous_chain_activity_count"] = 0
        self._activity_state["autonomous_chain_plan_count"] = 0
        self._activity_state["autonomous_chain_execute_count"] = 0
        self._activity_state["memory_write_failure_count"] = 0
        self._activity_state["error_count"] = 0
        self._activity_state["uncertainty_high_count"] = 0
        self._activity_state["recent_metadata"] = {
            "user_request": None,
            "agent_work": None,
            "memory_task": None,
            "self_learning": None,
            "autonomous_chain": None,
            "autonomous_chain_plan": None,
            "autonomous_chain_execute": None,
            "memory_write_failure": None,
        }
        self._activity_log.clear()
        self._persist_activity_state(force=True)

    async def clear_activity_state(self):
        self._reset_activity_state()
        return {
            "status": "cleared",
            "activity": self._build_activity_snapshot(),
            "log_count": 0,
        }

    async def get_trace(self, trace_id: str) -> Dict[str, Any]:
        """Aggregate trace events by trace_id from the gateway activity log (O-03).

        Provides a single gateway-centric trace view that complements the
        supervisor's multi-source trace aggregation.
        """
        events = [
            event for event in self._activity_log
            if isinstance(event.get("metadata"), dict)
            and event["metadata"].get("trace_id") == trace_id
        ]
        return {
            "trace_id": trace_id,
            "source": "gateway_activity_log",
            "count": len(events),
            "events": events,
            "activity_snapshot": self._build_activity_snapshot(),
        }

    async def touch_activity(self, request: Request):
        try:
            payload = ActivityTouchRequest.model_validate(await request.json())
            self._touch_activity(
                payload.activity_kind,
                source_service=payload.source_service,
                session_id=payload.session_id,
                metadata=payload.metadata,
            )
            return {
                "status": "updated",
                "activity": self._build_activity_snapshot(),
            }
        except Exception as e:
            logger.error(f"Error updating activity state: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def register_session(self, request: Request):
        try:
            self._authorize_registration(request)
            payload = SessionRegisterRequest.model_validate(await request.json())
            existing = dict(self._agent_session_cache.get(payload.session_id) or {})
            existing_owner = existing.get("owner_id")
            existing_workspace = existing.get("workspace_id")
            if (
                existing_owner is not None
                and str(existing_owner) != payload.owner_id
            ) or (
                existing_workspace is not None
                and str(existing_workspace) != payload.workspace_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Gateway session scope cannot be changed",
                )
            self._touch_session(payload.session_id, source=payload.source)
            existing = dict(self._agent_session_cache.get(payload.session_id) or {})
            self._agent_session_cache[payload.session_id] = {
                **existing,
                "model": payload.model,
                "provider": payload.provider,
                "source": payload.source,
                "owner_id": payload.owner_id,
                "workspace_id": payload.workspace_id,
            }
            if payload.source == "cli" and not self._active_cli_session_id:
                self._active_cli_session_id = payload.session_id
            session_token = self._session_credentials.setdefault(
                payload.session_id,
                self._new_credential(),
            )
            return {
                "status": "registered",
                "session_id": payload.session_id,
                "session_token": session_token,
                "active_cli_session_id": self._active_cli_session_id,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error registering session: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def set_autonomous_chain_gate(self, request: Request):
        """Receive the supervisor autonomous-chain gate state.

        This is the canonical supervisor-owned switch for the autonomous chain.
        """
        try:
            data = await request.json()
            active = bool(data.get("active", False))
            self._autonomous_chain_gate_active = active
            logger.info("Gateway autonomous chain gate set to: %s", active)
            return {"autonomous_chain_gate_active": active}
        except Exception as e:
            logger.error(f"Error setting autonomous chain gate: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def register_service(self, request: Request):
        try:
            self._authorize_registration(request)
            data = await request.json()
            service_id = str(data.get("service_id") or uuid.uuid4()).strip()
            service_name = str(data.get("service_name") or "").strip()
            service_type = str(data.get("service_type") or "").strip()
            address = str(data.get("address") or "").strip().rstrip("/")
            health_endpoint = str(data.get("health_endpoint") or "/health").strip()
            
            if not all([service_name, service_type, address]):
                raise HTTPException(status_code=400, detail="Missing required fields")
            
            service_info = ServiceInfo(
                service_id=service_id,
                service_name=service_name,
                service_type=service_type,
                address=address,
                health_endpoint=health_endpoint,
                metadata=data.get("metadata", {}),
                registered_at=datetime.now(),
                last_health_check=datetime.now(),
                healthy=True
            )

            replaced_service_ids = self._remove_superseded_service_registrations(
                service_info
            )
            self._services[service_id] = service_info
            service_token = self._new_credential()
            self._service_credentials[service_id] = service_token
            self._auto_configure_route(service_type, service_id)

            if replaced_service_ids:
                logger.info(
                    "Replaced stale %s gateway registrations: %s",
                    service_type,
                    ", ".join(replaced_service_ids),
                )
            logger.info(f"Service registered: {service_name} ({service_id}) at {address}")
            return JSONResponse(
                content={
                    "service_id": service_id,
                    "service_token": service_token,
                    "status": "registered",
                },
                status_code=201,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error registering service: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def get_body_status(self):
        self._evict_stale_sessions()
        return {
            "active_cli_executor": self._build_active_cli_executor_snapshot(),
            "body_slots": self._list_body_slots(),
        }

    def _auto_configure_route(self, service_type: str, service_id: str):
        path_prefix = self.ROUTE_PREFIX_BY_SERVICE_TYPE.get(service_type)
        if path_prefix:
            existing_route = self._routes.get(path_prefix)
            if existing_route:
                existing_route.target_instance = service_id
            else:
                self._routes[path_prefix] = RouteEntry(
                    path_prefix=path_prefix,
                    target_service=service_type,
                    target_instance=service_id,
                    weight=100,
                    enabled=True
                )

    @classmethod
    def _upstream_route_path(cls, service_type: str, gateway_path: str) -> str:
        gateway_prefix = cls.ROUTE_PREFIX_BY_SERVICE_TYPE.get(service_type)
        upstream_prefix = cls.UPSTREAM_PREFIX_BY_SERVICE_TYPE.get(service_type)
        normalized_path = "/" + str(gateway_path or "").lstrip("/")
        if not gateway_prefix or upstream_prefix is None:
            return normalized_path
        if not normalized_path.startswith(gateway_prefix):
            return normalized_path
        suffix = normalized_path[len(gateway_prefix) :]
        return upstream_prefix.rstrip("/") + "/" + suffix

    def _invalidate_service_registration_cache(self, service_type: str) -> None:
        if service_type == "memory":
            self._memory_service_url = None

        if service_type not in {"supervisor", "executor"}:
            return
        scene_cache = self._scenes_cache.get(service_type)
        if isinstance(scene_cache, dict):
            scene_cache.update(
                {
                    "service_id": None,
                    "address": None,
                    "reachable": False,
                    "last_fetched_at": None,
                }
            )

    def _remove_service_registration(self, service_id: str) -> Optional[ServiceInfo]:
        service = self._services.pop(service_id, None)
        self._service_credentials.pop(service_id, None)
        if service is None:
            return None

        self._invalidate_service_registration_cache(service.service_type)
        for prefix, route in list(self._routes.items()):
            if route.target_instance == service_id:
                del self._routes[prefix]
        return service

    def _remove_superseded_service_registrations(
        self,
        service: ServiceInfo,
    ) -> List[str]:
        replaced_service_ids: List[str] = []
        existing = self._remove_service_registration(service.service_id)
        if existing is not None:
            replaced_service_ids.append(existing.service_id)

        if service.service_type in self.ROUTED_SINGLETON_SERVICE_TYPES:
            for registered in list(self._services.values()):
                if registered.service_type != service.service_type:
                    continue
                removed = self._remove_service_registration(registered.service_id)
                if removed is not None:
                    replaced_service_ids.append(removed.service_id)

        self._invalidate_service_registration_cache(service.service_type)
        return replaced_service_ids

    async def update_health(self, service_id: str, request: Request):
        try:
            data = await request.json()
            healthy = data.get("healthy", True)
            
            if service_id in self._services:
                self._services[service_id].healthy = healthy
                self._services[service_id].last_health_check = datetime.now()
                logger.debug(f"Health updated for {service_id}: {healthy}")
                return {"status": "updated"}
            else:
                raise HTTPException(status_code=404, detail="Service not found")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating health: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def list_services(self):
        services = [self._serialize_service(s) for s in self._services.values()]
        return {"services": services, "count": len(services)}

    async def get_service(self, service_id: str):
        service = self._services.get(service_id)
        if service:
            return self._serialize_service(service)
        raise HTTPException(status_code=404, detail="Service not found")

    async def remove_service(self, service_id: str):
        service = self._remove_service_registration(service_id)
        if service is not None:
            logger.info(f"Service removed: {service.service_name} ({service_id})")
            return {"status": "removed"}
        raise HTTPException(status_code=404, detail="Service not found")

    async def list_routes(self):
        routes = [self._serialize_route(r) for r in self._routes.values()]
        return {
            "routes": routes,
            "count": len(routes),
            "executor_access_policy": self._build_executor_access_policy(),
        }

    async def add_route(self, request: Request):
        try:
            data = await request.json()
            path_prefix = data.get("path_prefix")
            target_service = data.get("target_service")
            target_instance = data.get("target_instance")
            
            if not path_prefix or not target_service:
                raise HTTPException(status_code=400, detail="Missing required fields")
            
            self._routes[path_prefix] = RouteEntry(
                path_prefix=path_prefix,
                target_service=target_service,
                target_instance=target_instance,
                weight=data.get("weight", 100),
                enabled=data.get("enabled", True)
            )
            
            logger.info(f"Route added: {path_prefix} -> {target_service}")
            return {"status": "added"}
        except Exception as e:
            logger.error(f"Error adding route: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def update_route(self, path_prefix: str, request: Request):
        if path_prefix not in self._routes:
            raise HTTPException(status_code=404, detail="Route not found")
        
        try:
            data = await request.json()
            route = self._routes[path_prefix]
            
            if "target_instance" in data:
                route.target_instance = data["target_instance"]
            if "weight" in data:
                route.weight = data["weight"]
            if "enabled" in data:
                route.enabled = data["enabled"]
            
            logger.info(f"Route updated: {path_prefix}")
            return {"status": "updated"}
        except Exception as e:
            logger.error(f"Error updating route: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def delete_route(self, path_prefix: str):
        if path_prefix in self._routes:
            del self._routes[path_prefix]
            logger.info(f"Route deleted: {path_prefix}")
            return {"status": "deleted"}
        raise HTTPException(status_code=404, detail="Route not found")

    # ── Scene aggregation (baseline §8.1) ──
    #
    # Scene is per-reporter.  Each of the three reporters (supervisor,
    # active agent, executor) declares its own scene.  The gateway
    # merely relays those declarations and presents them in a
    # three-segment view for the CLI status bar.  The gateway itself
    # never reinterprets or rewrites a reporter's scene.

    SUPERVISOR_LEGAL_SCENES: frozenset = frozenset(
        {"idle", "planning", "drive", "memory", "maintenance", "handoff"}
    )
    AGENT_LEGAL_SCENES: frozenset = frozenset(
        {"idle", "learning", "code_editing", "executing"}
    )
    EXECUTOR_LEGAL_SCENES: frozenset = frozenset({"idle", "body_switch"})

    async def get_scenes(self, refresh: bool = False):
        """Return the aggregated per-reporter scene view.

        Optional ``refresh=true`` forces re-fetch from every reachable
        service before returning.  Otherwise the cache from the last
        refresh is returned (and refreshed on a short cadence by
        ``refresh_scenes``).
        """
        if refresh:
            await self.refresh_scenes()
        return {
            "status": "ok",
            "scenes": self._scenes_cache,
            "summary": self._build_scene_summary(),
            "generated_at": datetime.now().isoformat(),
        }

    async def refresh_scenes(self):
        """Force a fresh scene fetch from every reachable service."""
        await self._refresh_supervisor_scene()
        await self._refresh_agent_scene()
        await self._refresh_executor_scene()
        return {"status": "refreshed", "scenes": self._scenes_cache}

    def _build_scene_summary(self) -> Dict[str, str]:
        """Compact scene summary with user/user-chat and autonomous lanes split."""
        scenes = self._scenes_cache
        agent_scene = dict(scenes.get("agent") or {})
        lanes = agent_scene.get("lanes") if isinstance(agent_scene.get("lanes"), dict) else {}
        user_chat_scene = str(
            dict(lanes.get("user_chat") or {}).get("scene") or "idle"
        ).strip() or "idle"
        supervisor_task_scene = str(
            dict(lanes.get("supervisor_task") or {}).get("scene") or "idle"
        ).strip() or "idle"
        return {
            "supervisor": scenes["supervisor"].get("scene") or "idle",
            # Keep `agent` as a compact user-chain alias so summary consumers
            # do not accidentally treat top-level agent aggregation as the
            # canonical API-A autonomous-execution fact source.
            "agent": user_chat_scene,
            "agent_user_chat": user_chat_scene,
            "agent_supervisor_task": supervisor_task_scene,
            "executor": scenes["executor"].get("scene") or "idle",
        }

    def _find_services(self, service_type: str) -> List[ServiceInfo]:
        return [s for s in self._services.values() if s.service_type == service_type]

    async def _http_get_json(self, url: str, timeout: float = 2.0) -> Optional[Dict[str, Any]]:
        """Best-effort JSON GET.  Returns None on any failure."""
        try:
            import aiohttp
            timeout_obj = aiohttp.ClientTimeout(total=max(timeout, 0.1))
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout_obj) as resp:
                    if resp.status >= 400:
                        return None
                    return await resp.json()
        except Exception as exc:
            logger.debug("Gateway scene fetch %s failed: %s", url, exc)
            return None

    def _validate_scene(self, scene: Optional[str], allowed: frozenset, default: str = "idle") -> str:
        candidate = str(scene or "").strip()
        if candidate in allowed:
            return candidate
        return default

    async def _refresh_supervisor_scene(self) -> None:
        supervisors = self._find_services("supervisor")
        cache = self._scenes_cache["supervisor"]
        cache["reachable"] = False
        if not supervisors:
            cache["last_fetched_at"] = datetime.now().isoformat()
            return
        # Use the first healthy supervisor; a real deployment typically
        # has exactly one supervisor instance.
        target = next((s for s in supervisors if s.healthy), supervisors[0])
        address = (target.address or "").rstrip("/")
        url = f"{address}/ui/state" if address else None
        if not url:
            cache["last_fetched_at"] = datetime.now().isoformat()
            return
        payload = await self._http_get_json(url, timeout=2.0)
        cache["service_id"] = target.service_id
        cache["address"] = address
        cache["last_fetched_at"] = datetime.now().isoformat()
        if not isinstance(payload, dict):
            return
        cache["reachable"] = True
        cache["scene"] = self._validate_scene(
            payload.get("scene"),
            self.SUPERVISOR_LEGAL_SCENES,
        )
        cache["title"] = payload.get("title")
        cache["summary"] = payload.get("summary")
        # Carry across the supervisor's activity touch point (best-effort).
        try:
            cache["scene_changed_at"] = payload.get("generated_at")
        except Exception:
            pass

    async def _refresh_agent_scene(self) -> None:
        """Refresh the CLI-reported agent scene only.

        The gateway no longer elects or probes background agent services
        as default executors. Agent scene is sourced from the active CLI
        executor's activity touch stream.
        """
        cache = self._scenes_cache["agent"]
        if cache.get("source_service") == "cli_agent" and cache.get("reachable"):
            cache["last_fetched_at"] = datetime.now().isoformat()
            return
        cache["reachable"] = False
        cache["last_fetched_at"] = datetime.now().isoformat()

    async def _refresh_executor_scene(self) -> None:
        cache = self._scenes_cache["executor"]
        cache["reachable"] = False
        executors = self._find_services("executor")
        if not executors:
            cache["last_fetched_at"] = datetime.now().isoformat()
            return
        target = next((s for s in executors if s.healthy), executors[0])
        address = (target.address or "").rstrip("/")
        url = f"{address}/executor/scene" if address else None
        if not url:
            cache["last_fetched_at"] = datetime.now().isoformat()
            return
        payload = await self._http_get_json(url, timeout=2.0)
        cache["service_id"] = target.service_id
        cache["address"] = address
        cache["last_fetched_at"] = datetime.now().isoformat()
        if not isinstance(payload, dict):
            return
        cache["reachable"] = True
        cache["scene"] = self._validate_scene(
            payload.get("scene"),
            self.EXECUTOR_LEGAL_SCENES,
        )
        cache["scene_changed_at"] = payload.get("scene_changed_at")

    async def route_request(self, path: str, request: Request):
        self._request_counter += 1
        request_id = str(uuid.uuid4())
        
        try:
            matched_route = None
            for prefix, route in self._routes.items():
                if path.startswith(prefix[1:]) and route.enabled:
                    matched_route = route
                    break
            
            if not matched_route:
                raise HTTPException(status_code=404, detail="No route found for path")
            
            target_service = self._services.get(matched_route.target_instance)
            if not target_service:
                raise HTTPException(status_code=503, detail="Target service not available")
            
            if not target_service.healthy:
                raise HTTPException(status_code=503, detail="Target service unhealthy")

            body = await request.body()
            activity_metadata: Dict[str, Any] = {}
            if body:
                try:
                    activity_metadata = self._extract_activity_metadata_from_payload(
                        json.loads(body.decode("utf-8"))
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    activity_metadata = {}

            upstream_path = self._upstream_route_path(
                target_service.service_type,
                path,
            )
            headers = dict(request.headers)
            query_params = list(request.query_params.multi_items())
            if (
                target_service.service_type == "memory"
                and upstream_path.rstrip("/") != ""
            ):
                memory_actor = self._authenticate_memory_caller(request)
                memory_scope = self._session_memory_scope(request)
                body, query_params = self._inject_memory_actor(
                    body,
                    query_params,
                    method=request.method,
                    memory_actor=memory_actor,
                    memory_scope=memory_scope,
                )
                stripped_headers = {
                    self.SERVICE_ID_HEADER,
                    self.SERVICE_TOKEN_HEADER,
                    self.SESSION_ID_HEADER,
                    self.SESSION_TOKEN_HEADER,
                    self.MEMORY_ACTOR_HEADER,
                    "authorization",
                    self.GATEWAY_TOKEN_HEADER,
                    "content-length",
                }
                headers = {
                    key: value
                    for key, value in headers.items()
                    if key.lower() not in stripped_headers
                }
                if body and "content-type" not in headers:
                    headers["content-type"] = "application/json"

            if target_service.service_type == "memory":
                if self._is_memory_write_activity(path, request.method):
                    self._touch_activity("memory_task", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "agent":
                self._touch_activity("agent_work", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "supervisor":
                self._touch_activity("autonomous_chain_plan", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "executor":
                self._touch_activity("autonomous_chain_execute", metadata=activity_metadata)
            
            url = f"{target_service.address}{upstream_path}"
            logger.debug(f"Routing request {request_id}: {path} -> {url}")
            
            async with asyncio.timeout(30):
                async with aiohttp.ClientSession() as session:
                    method = request.method
                    async with session.request(
                        method,
                        url,
                        params=query_params or None,
                        data=body,
                        headers=headers,
                    ) as response:
                        response_body = await response.read()
                        response_headers = dict(response.headers)
                        
                        return Response(
                            content=response_body,
                            status_code=response.status,
                            headers=response_headers
                        )
        
        except asyncio.TimeoutError:
            logger.error(f"Request {request_id} timed out")
            self._touch_activity("agent_error", metadata={
                "request_id": request_id, "error_type": "timeout", "path": path,
            })
            raise HTTPException(status_code=504, detail="Gateway timeout")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error routing request {request_id}: {e}")
            self._touch_activity("agent_error", metadata={
                "request_id": request_id, "error_type": type(e).__name__, "path": path,
            })
            raise HTTPException(status_code=500, detail=str(e))

    def _evict_stale_sessions(self) -> None:
        """Remove sessions idle longer than TTL (SB-04)."""
        cutoff = datetime.now().timestamp() - self._session_ttl_seconds
        stale = []
        for sid, session in self._agent_session_cache.items():
            last_used_at = session.get("last_used_at")
            if isinstance(last_used_at, datetime):
                last_seen = last_used_at.timestamp()
            else:
                last_seen = 0.0
            if last_seen < cutoff:
                stale.append(sid)
        for sid in stale:
            if sid == self._active_cli_session_id:
                self._active_cli_session_id = None
            self._clear_agent_session_lane(sid)
            self._session_credentials.pop(sid, None)
            del self._agent_session_cache[sid]

    # ── Tier 1 Conversation Recording ──────────────────────────────

    def _resolve_memory_service_url(self) -> str | None:
        """Resolve the memory-service URL from registered services."""
        if self._memory_service_url:
            return self._memory_service_url
        for svc in self._services.values():
            if svc.service_type == "memory" and svc.healthy:
                self._memory_service_url = svc.address
                return self._memory_service_url
        return None

    async def _record_turn_to_tier1(
        self, session_id: str, speaker: str, text: str, metadata: Dict[str, Any] = None
    ) -> None:
        """Non-blocking best-effort recording of a conversation turn to Tier 1."""
        memory_url = self._resolve_memory_service_url()
        if not memory_url:
            return
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(
                    f"{memory_url}/sessions/{session_id}/turns",
                    json={
                        "speaker": speaker,
                        "text": text,
                        "metadata": metadata or {},
                    },
                    timeout=aiohttp.ClientTimeout(total=2),
                )
        except Exception as exc:
            logger.warning(
                "Tier1 turn record failed for session %s speaker %s: %s",
                session_id,
                speaker,
                exc,
            )
            self._touch_activity(
                "memory_write_failure",
                source_service="gateway",
                session_id=session_id,
                metadata={
                    "speaker": speaker,
                    "error": str(exc),
                    **dict(metadata or {}),
                },
            )

    async def _record_agent_interaction_to_tier1(
        self, session_id: str, messages: List[Dict[str, Any]], response_text: str
    ) -> None:
        """Record a full user+agent interaction to Tier 1."""
        # Record the last user message
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = str(msg.get("content", ""))
                break
        if user_text:
            await self._record_turn_to_tier1(session_id, "user", user_text)
        # Record the agent response
        if response_text:
            await self._record_turn_to_tier1(session_id, "agent", response_text)

    @staticmethod
    def _extract_response_text(response_data: Any) -> str:
        """Extract text content from various LLM response formats."""
        if isinstance(response_data, str):
            return response_data
        if not isinstance(response_data, dict):
            return ""
        # OpenAI-style: choices[0].message.content
        choices = response_data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
            content = msg.get("content", "")
            if content:
                return str(content)
        # Direct content field
        for key in ("content", "text", "summary", "result"):
            val = response_data.get(key)
            if isinstance(val, str) and val:
                return val
        return ""

    async def get_approved_tasks(
        self,
        status: Optional[str] = "approved",
        task_type: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ):
        self._evict_stale_sessions()
        supervisor_service = None
        all_supervisors = [s for s in self._services.values() if s.service_type == "supervisor"]
        for service in all_supervisors:
            if service.healthy:
                supervisor_service = service
                break
        if not supervisor_service:
            detail = (
                f"Supervisor unavailable. "
                f"Found {len(all_supervisors)} supervisor(s), "
                f"healthy={[s.healthy for s in all_supervisors]}, "
                f"total services={len(self._services)}"
            )
            logger.warning(detail)
            raise HTTPException(status_code=503, detail=detail)

        url = f"{supervisor_service.address}{AUTONOMOUS_CHAIN_TASKS_ROUTE}"
        requested_status = str(status or "approved").strip().lower() or "approved"
        if requested_status not in {"approved", "running"}:
            raise HTTPException(status_code=400, detail="Unsupported task status for gateway task pull")
        params = {"status": requested_status}
        if task_type:
            params["task_type"] = task_type
        if execution_kind:
            params["execution_kind"] = execution_kind

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=resp.status, detail=f"Supervisor returned {resp.status}")
                    result = await resp.json()
            self._touch_activity(
                "self_learning",
                source_service="gateway",
                metadata={"task_type": task_type, "execution_kind": execution_kind},
            )
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch handoff-ready tasks: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch tasks: {e}")

    @staticmethod
    def _request_agent_session_id(data: Dict[str, Any]) -> str:
        session_id = str(data.get("session_id") or "").strip()
        if session_id:
            return session_id
        context = data.get("context")
        if isinstance(context, dict):
            return str(context.get("session_id") or "").strip()
        return ""

    @staticmethod
    def _is_agent_pull_task_payload(task: Dict[str, Any]) -> bool:
        metadata = dict(task.get("metadata") or {})
        governance_type = str(
            task.get("governance_task_type")
            or metadata.get("governance_task_type")
            or task.get("task_type")
            or ""
        ).strip().lower()
        execution_kind = str(
            task.get("execution_kind")
            or metadata.get("execution_kind")
            or ""
        ).strip().lower()
        return governance_type == "self_learning" or execution_kind == "body_improvement"

    def _validate_agent_pull_session(
        self,
        *,
        task_id: str,
        task: Dict[str, Any],
        data: Dict[str, Any],
        decision: str,
        actor: str,
    ) -> str:
        if decision not in {"running", "completed", "failed"}:
            return ""
        if str(actor or "").strip().lower() not in {"agent", "cli_agent", "gateway"}:
            return ""
        if not self._is_agent_pull_task_payload(task):
            return ""

        session_id = self._request_agent_session_id(data)
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="Agent pull 链路项裁决需要提供 session_id 或 context.session_id。",
            )
        session = self._agent_session_cache.get(session_id)
        if session is not None:
            return session_id

        task_lease = dict(task.get("execution_lease") or {})
        request_lease = dict(data.get("execution_lease") or {})
        lease_recovers_session = (
            str(task_lease.get("owner_session_id") or "").strip() == session_id
            and bool(str(task_lease.get("generation") or "").strip())
            and str(task_lease.get("generation") or "").strip()
            == str(request_lease.get("generation") or "").strip()
            and bool(str(task_lease.get("attempt_id") or "").strip())
            and str(task_lease.get("attempt_id") or "").strip()
            == str(request_lease.get("attempt_id") or "").strip()
        )
        if not lease_recovers_session:
            raise HTTPException(
                status_code=409,
                detail=f"无法识别该链路写回对应的 CLI 会话: {session_id}",
            )

        return session_id

    async def complete_task(self, task_id: str, request: Request):
        return await self._forward_task_decision(
            task_id,
            request,
            default_decision="completed",
            default_reason="Task completed by agent",
            default_actor="agent",
        )

    async def validate_task_execution_lease(self, task_id: str, request: Request):
        supervisor_service = next(
            (
                service
                for service in self._services.values()
                if service.service_type == "supervisor" and service.healthy
            ),
            None,
        )
        if supervisor_service is None:
            raise HTTPException(
                status_code=503,
                detail="Supervisor unavailable for execution lease validation",
            )
        try:
            body = await request.body()
            payload = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON") from exc

        url = (
            f"{supervisor_service.address}"
            f"{autonomous_chain_task_lease_validation_route(task_id)}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    result = await response.json()
                    if response.status != 200:
                        detail = result.get("detail", result) if isinstance(result, dict) else result
                        raise HTTPException(status_code=response.status, detail=detail)
                    return result
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to validate task execution lease: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to validate task execution lease: {exc}",
            ) from exc

    async def decide_task(self, task_id: str, request: Request):
        return await self._forward_task_decision(
            task_id,
            request,
            default_decision="running",
            default_reason="Task decision forwarded by gateway",
            default_actor="agent",
        )

    async def _forward_task_decision(
        self,
        task_id: str,
        request: Request,
        *,
        default_decision: str,
        default_reason: str,
        default_actor: str,
    ):
        self._evict_stale_sessions()
        supervisor_service = None
        all_supervisors = [s for s in self._services.values() if s.service_type == "supervisor"]
        for service in all_supervisors:
            if service.healthy:
                supervisor_service = service
                break
        if not supervisor_service:
            detail = (
                f"Supervisor unavailable. "
                f"Found {len(all_supervisors)} supervisor(s), "
                f"healthy={[s.healthy for s in all_supervisors]}, "
                f"total services={len(self._services)}"
            )
            logger.warning(detail)
            raise HTTPException(status_code=503, detail=detail)

        url = f"{supervisor_service.address}{autonomous_chain_task_decision_route(task_id)}"

        try:
            body = await request.body()
            data = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        decision = str(data.get("decision") or default_decision).strip().lower()
        actor = data.get("actor", default_actor)
        payload = {
            "decision": decision,
            "reason": data.get("reason", default_reason),
            "actor": actor,
            "context": data.get("context", {}),
            "metadata": data.get("metadata", {}),
        }
        if isinstance(data.get("execution_lease"), dict):
            payload["execution_lease"] = dict(data["execution_lease"])
        if data.get("lease_seconds") is not None:
            payload["lease_seconds"] = data["lease_seconds"]
        final_response = str(data.get("final_response") or "").strip()
        if final_response:
            payload["final_response"] = final_response[:4000]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{supervisor_service.address}{autonomous_chain_task_route(task_id)}",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as task_resp:
                    if task_resp.status != 200:
                        raise HTTPException(
                            status_code=task_resp.status,
                            detail=f"Supervisor returned {task_resp.status}",
                        )
                    task_result = await task_resp.json()
                task_payload = (
                    dict(task_result.get("task") or {})
                    if isinstance(task_result, dict) and isinstance(task_result.get("task"), dict)
                    else dict(task_result or {})
                )
                session_id = self._validate_agent_pull_session(
                    task_id=task_id,
                    task=task_payload,
                    data=data,
                    decision=decision,
                    actor=str(actor),
                )
                if session_id:
                    payload["session_id"] = session_id
                    context = dict(payload.get("context") or {})
                    context.setdefault("session_id", session_id)
                    payload["context"] = context
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    result = await resp.json()
                    if resp.status != 200:
                        detail = (
                            result.get("detail", result)
                            if isinstance(result, dict)
                            else result
                        )
                        raise HTTPException(status_code=resp.status, detail=detail)
                if session_id in self._agent_session_cache:
                    self._agent_session_cache[session_id]["last_used_at"] = datetime.now()
            self._touch_activity(
                "agent_work",
                source_service="gateway",
                metadata={"task_id": task_id, "decision": decision},
            )
            # P0-2 成果回流: on successful completion, flow the executor finding
            # text into Mem Tier1 so learning/improvement output is not stranded
            # in the CLI conversation history. Best-effort: a Mem write failure
            # must never turn a successful task writeback into an error.
            if decision == "completed":
                session_id = str(data.get("session_id") or "").strip()
                if final_response and session_id:
                    try:
                        await self._record_turn_to_tier1(
                            session_id,
                            "agent",
                            final_response,
                            metadata={
                                "task_id": task_id,
                                "source": "autonomous_task_finding",
                                "execution_kind": str(
                                    (data.get("context") or {}).get("execution_kind") or ""
                                ),
                            },
                        )
                    except Exception as mem_exc:
                        logger.warning(f"Tier1 finding record failed for task {task_id}: {mem_exc}")
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to forward task decision: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to forward task decision: {e}")

    async def forward_improvement_report(self, request: Request):
        """Forward an Agent's body-improvement report to the supervisor.

        P0-2 成果回流 (body path): the CLI cannot reach the supervisor directly
        (it only knows the gateway), and the generic /api/{prefix} proxy would
        double the route prefix. So, mirroring _forward_task_decision, the
        gateway resolves a healthy supervisor and forwards to its
        /body/improvement-report endpoint for health scoring.
        """
        supervisor_service = None
        all_supervisors = [s for s in self._services.values() if s.service_type == "supervisor"]
        for service in all_supervisors:
            if service.healthy:
                supervisor_service = service
                break
        if not supervisor_service:
            raise HTTPException(status_code=503, detail="Supervisor unavailable for improvement report")

        try:
            body = await request.body()
            report = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        url = f"{supervisor_service.address}/body/improvement-report"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=report, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=resp.status, detail=f"Supervisor returned {resp.status}")
                    result = await resp.json()
            self._touch_activity(
                "autonomous_chain",
                source_service="gateway",
                metadata={"task_id": report.get("task_id"), "slot_id": report.get("slot_id")},
            )
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to forward improvement report: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to forward improvement report: {e}")

    async def chat_completions_proxy(self, request: Request):
        self._evict_stale_sessions()
        self._request_counter += 1
        request_id = str(uuid.uuid4())
        
        try:
            body = await request.body()
            activity_metadata: Dict[str, Any] = {}
            if body:
                try:
                    activity_metadata = self._extract_activity_metadata_from_payload(
                        json.loads(body.decode("utf-8"))
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    activity_metadata = {}

            self._touch_activity("user_request", metadata=activity_metadata)
            raise HTTPException(
                status_code=410,
                detail=(
                    "Gateway agent proxy has been removed. "
                    "Use the current CLI/API-A executor directly for chat completions."
                ),
            )
        
        except asyncio.TimeoutError:
            logger.error(f"Chat completion {request_id} timed out")
            self._touch_activity("agent_error", metadata={
                "request_id": request_id, "error_type": "timeout", "path": "chat/completions",
            })
            raise HTTPException(status_code=504, detail="Gateway timeout")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error proxying chat completion {request_id}: {e}")
            self._touch_activity("agent_error", metadata={
                "request_id": request_id, "error_type": type(e).__name__, "path": "chat/completions",
            })
            raise HTTPException(status_code=500, detail=str(e))

    async def agent_query_proxy(self, request: Request):
        self._evict_stale_sessions()
        self._request_counter += 1
        request_id = str(uuid.uuid4())
        
        try:
            data = await request.json()

            session_id = data.get("session_id") or str(uuid.uuid4())
            activity_metadata = self._extract_activity_metadata_from_payload(data)
            self._touch_activity("user_request", session_id=session_id, metadata=activity_metadata)
            
            if session_id not in self._agent_session_cache:
                self._agent_session_cache[session_id] = {
                    "created_at": datetime.now(),
                    "last_used_at": datetime.now(),
                    "message_count": 0,
                    "source": "gateway_proxy",
                }
            
            self._agent_session_cache[session_id]["last_used_at"] = datetime.now()
            self._agent_session_cache[session_id]["message_count"] += 1

            # Fire-and-forget: record user turn to Tier 1
            asyncio.create_task(
                self._record_agent_interaction_to_tier1(
                    session_id, data.get("messages", []), ""
                )
            )
            raise HTTPException(
                status_code=410,
                detail=(
                    "Gateway agent proxy has been removed. "
                    "Autonomous tasks must run on the current CLI/API-A executor."
                ),
            )
        
        except asyncio.TimeoutError:
            logger.error(f"Agent query {request_id} timed out")
            self._touch_activity("agent_error", metadata={
                "request_id": request_id, "error_type": "timeout", "path": "agent/query",
            })
            raise HTTPException(status_code=504, detail="Gateway timeout")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error proxying agent query {request_id}: {e}")
            self._touch_activity("agent_error", metadata={
                "request_id": request_id, "error_type": type(e).__name__, "path": "agent/query",
            })
            raise HTTPException(status_code=500, detail=str(e))

    async def get_session_info(self, session_id: str):
        self._evict_stale_sessions()
        session_data = self._agent_session_cache.get(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "session_id": session_id,
            **session_data,
            **self._build_session_lease_snapshot(session_id),
            "active_cli_session_id": self._active_cli_session_id,
            "active_cli_executor": self._build_active_cli_executor_snapshot(),
        }

    async def delete_session(self, session_id: str):
        if session_id not in self._agent_session_cache:
            raise HTTPException(status_code=404, detail="Session not found")
        if session_id == self._active_cli_session_id:
            self._active_cli_session_id = None
        self._clear_agent_session_lane(session_id)
        self._session_credentials.pop(session_id, None)
        del self._agent_session_cache[session_id]
        logger.info(f"Session deleted: {session_id}")
        return {"status": "deleted"}

    async def start(self):
        import uvicorn
        logger.info(f"Starting internal gateway on {self.config.host}:{self.config.port}")
        await uvicorn.Server(
            uvicorn.Config(
                self.app,
                host=self.config.host,
                port=self.config.port,
                log_level=self.config.log_level.lower()
            )
        ).serve()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="VoidCube Internal Gateway")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway host")
    parser.add_argument("--port", type=int, default=6000, help="Gateway port")
    args = parser.parse_args()
    
    config = GatewayConfig(host=args.host, port=args.port)
    gateway = InternalGateway(config)
    
    import asyncio
    asyncio.run(gateway.start())
