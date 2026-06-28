import aiohttp
import asyncio
import json
import logging
import os
import uuid
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from systems.runtime_task_profile import derive_runtime_task_profile

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


class InternalGateway:
    def __init__(self, config: GatewayConfig = None):
        self.config = config or GatewayConfig()
        self.app = FastAPI(title="VoidCube Internal Gateway", version="1.0")
        self._services: Dict[str, ServiceInfo] = {}
        self._routes: Dict[str, RouteEntry] = {}
        self._active_cli_session_id: str | None = None
        self._governor_mode_active: bool = False
        self._request_counter = 0
        # Tier 1 memory service URL (lazy-resolved from registered services)
        self._memory_service_url: str | None = None
        # NOTE(SB-03): Session cache is body-runtime state, not gateway operations
        # state.  Long-term session ownership should belong to the agent body
        # instances.  The gateway should only hold routing metadata.  TTL eviction
        # (SB-04) mitigates unbounded growth for now.
        self._agent_session_cache: Dict[str, Dict[str, Any]] = {}
        self._session_ttl_seconds: int = 3600  # evict sessions idle >1 hour
        self._active_cli_stale_after_seconds: int = 90
        self._activity_log: Deque[Dict[str, Any]] = deque(
            maxlen=max(int(self.config.activity_log_limit), 1)
        )
        self._activity_state: Dict[str, Any] = {
            "last_user_request_at": None,
            "last_agent_work_at": None,
            "last_memory_task_at": None,
            "last_self_learning_activity_at": None,
            "last_self_evolution_activity_at": None,
            "last_self_evolution_plan_at": None,
            "last_self_evolution_execute_at": None,
            "user_request_count": 0,
            "agent_work_count": 0,
            "memory_task_count": 0,
            "self_learning_activity_count": 0,
            "self_evolution_activity_count": 0,
            "self_evolution_plan_count": 0,
            "self_evolution_execute_count": 0,
            "error_count": 0,
            "uncertainty_high_count": 0,
            "recent_metadata": {
                "user_request": None,
                "agent_work": None,
                "memory_task": None,
                "self_learning": None,
                "self_evolution": None,
                "self_evolution_plan": None,
                "self_evolution_execute": None,
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
        self.app.add_api_route("/admin/governor-mode", self.set_governor_mode, methods=["POST"])
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
        self.app.add_api_route("/admin/traces/{trace_id}", self.get_trace, methods=["GET"])

    async def health_check(self):
        self._evict_stale_sessions()
        active_cli_executor = self._build_active_cli_executor_snapshot()
        registered_services = {
            "total": len(self._services),
            "agents": len([s for s in self._services.values() if s.service_type == "agent"]),
            "memory": 1 if any(s.service_type == "memory" for s in self._services.values()) else 0,
            "self_learning": 1 if any(s.service_type == "self_learning" for s in self._services.values()) else 0,
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
        if agent_scene.get("source_service") == "cli_agent":
            snapshot["scene"] = agent_scene.get("scene")
            snapshot["scene_task_id"] = agent_scene.get("scene_task_id")
            snapshot["scene_changed_at"] = agent_scene.get("scene_changed_at")
            snapshot["subagent_foreground_count"] = agent_scene.get("subagent_foreground_count", 0)
            snapshot["subagent_background_count"] = agent_scene.get("subagent_background_count", 0)
            snapshot["subagent_total_count"] = agent_scene.get("subagent_total_count", 0)
            snapshot["subagent_focus_task_id"] = agent_scene.get("subagent_focus_task_id")
            snapshot["subagent_focus_tool"] = agent_scene.get("subagent_focus_tool")
            snapshot["subagent_focus_preview"] = agent_scene.get("subagent_focus_preview")
        snapshot.update(self._build_session_lease_snapshot(session_id))
        return snapshot

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
            "last_self_evolution_activity_at": (
                self._activity_state["last_self_evolution_activity_at"].isoformat()
                if self._activity_state["last_self_evolution_activity_at"]
                else None
            ),
            "last_self_evolution_plan_at": (
                self._activity_state["last_self_evolution_plan_at"].isoformat()
                if self._activity_state["last_self_evolution_plan_at"]
                else None
            ),
            "last_self_evolution_execute_at": (
                self._activity_state["last_self_evolution_execute_at"].isoformat()
                if self._activity_state["last_self_evolution_execute_at"]
                else None
            ),
            "counts": {
                "user_request_count": self._activity_state["user_request_count"],
                "agent_work_count": self._activity_state["agent_work_count"],
                "memory_task_count": self._activity_state["memory_task_count"],
                "self_learning_activity_count": self._activity_state["self_learning_activity_count"],
                "self_evolution_activity_count": self._activity_state["self_evolution_activity_count"],
                "self_evolution_plan_count": self._activity_state["self_evolution_plan_count"],
                "self_evolution_execute_count": self._activity_state["self_evolution_execute_count"],
                "error_count": self._activity_state["error_count"],
                "uncertainty_high_count": self._activity_state["uncertainty_high_count"],
            },
            "recent_metadata": dict(self._activity_state["recent_metadata"]),
            "active_sessions": len(self._agent_session_cache),
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
            self._activity_state["last_agent_work_at"] = now
            self._activity_state["agent_work_count"] += 1
            self._activity_state["recent_metadata"]["user_request"] = activity_metadata
            self._activity_state["recent_metadata"]["agent_work"] = activity_metadata
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
        elif normalized == "agent_work":
            self._activity_state["last_agent_work_at"] = now
            self._activity_state["agent_work_count"] += 1
            self._activity_state["recent_metadata"]["agent_work"] = activity_metadata
        elif normalized == "memory_task":
            self._activity_state["last_memory_task_at"] = now
            self._activity_state["memory_task_count"] += 1
            self._activity_state["recent_metadata"]["memory_task"] = activity_metadata
        elif normalized == "self_learning":
            self._activity_state["last_self_learning_activity_at"] = now
            self._activity_state["self_learning_activity_count"] += 1
            self._activity_state["recent_metadata"]["self_learning"] = activity_metadata
        elif normalized == "self_evolution":
            self._activity_state["last_self_evolution_activity_at"] = now
            self._activity_state["self_evolution_activity_count"] += 1
            self._activity_state["recent_metadata"]["self_evolution"] = activity_metadata
        elif normalized == "self_evolution_plan":
            self._activity_state["last_self_evolution_activity_at"] = now
            self._activity_state["last_self_evolution_plan_at"] = now
            self._activity_state["self_evolution_activity_count"] += 1
            self._activity_state["self_evolution_plan_count"] += 1
            self._activity_state["recent_metadata"]["self_evolution"] = activity_metadata
            self._activity_state["recent_metadata"]["self_evolution_plan"] = activity_metadata
        elif normalized == "self_evolution_execute":
            self._activity_state["last_self_evolution_activity_at"] = now
            self._activity_state["last_self_evolution_execute_at"] = now
            self._activity_state["self_evolution_activity_count"] += 1
            self._activity_state["self_evolution_execute_count"] += 1
            self._activity_state["recent_metadata"]["self_evolution"] = activity_metadata
            self._activity_state["recent_metadata"]["self_evolution_execute"] = activity_metadata
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
            # Timestamp fields are stored as ISO strings; restore to datetime
            _ts_fields = {k for k in self._activity_state if k.startswith("last_")}
            for key in self._activity_state:
                if key in saved_state:
                    value = saved_state[key]
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
                    self._activity_log.appendleft(event)
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
        if source_service:
            payload.setdefault("source_service", source_service)
        if session_id:
            payload.setdefault("session_id", session_id)
        return payload or None

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

        label_parts = [part for part in (execution_kind, task_family, governance_task_type, task_type) if part]
        display_kind = requested_kind or execution_kind or task_family or governance_task_type or task_type
        if title:
            summary = f"{title} ({display_kind})" if display_kind else title
        elif task_id:
            summary = f"{task_id} ({display_kind})" if display_kind else task_id
        elif display_kind:
            summary = display_kind
        else:
            summary = None

        if not any((title, task_id, task_type, governance_task_type, task_family, execution_kind, summary)):
            return None

        return {
            "task_id": task_id,
            "title": title,
            "task_type": task_type,
            "governance_task_type": governance_task_type,
            "task_family": task_family,
            "execution_kind": execution_kind,
            "requested_kind": requested_kind,
            "display_kind": display_kind,
            "summary": summary,
            "labels": label_parts,
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
        return metadata

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
        self._activity_state["last_self_evolution_activity_at"] = None
        self._activity_state["last_self_evolution_plan_at"] = None
        self._activity_state["last_self_evolution_execute_at"] = None
        self._activity_state["user_request_count"] = 0
        self._activity_state["agent_work_count"] = 0
        self._activity_state["memory_task_count"] = 0
        self._activity_state["self_learning_activity_count"] = 0
        self._activity_state["self_evolution_activity_count"] = 0
        self._activity_state["self_evolution_plan_count"] = 0
        self._activity_state["self_evolution_execute_count"] = 0
        self._activity_state["error_count"] = 0
        self._activity_state["uncertainty_high_count"] = 0
        self._activity_state["recent_metadata"] = {
            "user_request": None,
            "agent_work": None,
            "memory_task": None,
            "self_learning": None,
            "self_evolution": None,
            "self_evolution_plan": None,
            "self_evolution_execute": None,
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
            payload = SessionRegisterRequest.model_validate(await request.json())
            self._touch_session(payload.session_id, source=payload.source)
            existing = dict(self._agent_session_cache.get(payload.session_id) or {})
            self._agent_session_cache[payload.session_id] = {
                **existing,
                "model": payload.model,
                "provider": payload.provider,
                "source": payload.source,
            }
            if payload.source == "cli" and not self._active_cli_session_id:
                self._active_cli_session_id = payload.session_id
            return {
                "status": "registered",
                "session_id": payload.session_id,
                "active_cli_session_id": self._active_cli_session_id,
            }
        except Exception as e:
            logger.error(f"Error registering session: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def set_governor_mode(self, request: Request):
        """Receive governor mode state from supervisor."""
        try:
            data = await request.json()
            active = bool(data.get("active", False))
            self._governor_mode_active = active
            logger.info("Gateway governor mode set to: %s", active)
            return {"governor_mode_active": active}
        except Exception as e:
            logger.error(f"Error setting governor mode: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def register_service(self, request: Request):
        try:
            data = await request.json()
            service_id = data.get("service_id", str(uuid.uuid4()))
            service_name = data.get("service_name")
            service_type = data.get("service_type")
            address = data.get("address")
            health_endpoint = data.get("health_endpoint", "/health")
            
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
            
            self._services[service_id] = service_info
            await self._auto_configure_route(service_type, service_id, address)
            
            logger.info(f"Service registered: {service_name} ({service_id}) at {address}")
            return JSONResponse(content={"service_id": service_id, "status": "registered"}, status_code=201)
            
        except Exception as e:
            logger.error(f"Error registering service: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def get_body_status(self):
        self._evict_stale_sessions()
        return {
            "active_cli_executor": self._build_active_cli_executor_snapshot(),
            "body_slots": self._list_body_slots(),
        }

    async def _auto_configure_route(self, service_type: str, service_id: str, address: str):
        route_map = {
            "memory": "/mem/",
            "self_learning": "/self-learning/",
            "supervisor": "/supervisor/",
            "executor": "/executor/",
        }
        
        path_prefix = route_map.get(service_type)
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
        if service_id in self._services:
            service = self._services.pop(service_id)
            # Invalidate cached memory service URL
            if service.service_type == "memory":
                self._memory_service_url = None
            
            for prefix, route in list(self._routes.items()):
                if route.target_instance == service_id:
                    del self._routes[prefix]
            
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
        {"idle", "planning", "drive", "memory", "maintenance", "dispatch"}
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
        """Three-segment headline view for the CLI status bar."""
        scenes = self._scenes_cache
        return {
            "supervisor": scenes["supervisor"].get("scene") or "idle",
            "agent": scenes["agent"].get("scene") or "idle",
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

            if target_service.service_type == "memory":
                self._touch_activity("memory_task", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "agent":
                self._touch_activity("agent_work", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "self_learning":
                self._touch_activity("self_learning", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "supervisor":
                self._touch_activity("self_evolution_plan", source_service="gateway", metadata=activity_metadata)
            elif target_service.service_type == "executor":
                self._touch_activity("self_evolution_execute", metadata=activity_metadata)
            
            url = f"{target_service.address}/{path}"
            logger.debug(f"Routing request {request_id}: {path} -> {url}")
            headers = dict(request.headers)
            
            async with asyncio.timeout(30):
                async with aiohttp.ClientSession() as session:
                    method = request.method
                    async with session.request(method, url, data=body, headers=headers) as response:
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
                # Ensure session exists (lazy-create with caller's session_id)
                await s.post(
                    f"{memory_url}/sessions",
                    json={"session_id": session_id, "metadata": {"source": "gateway"}},
                    timeout=aiohttp.ClientTimeout(total=2),
                )
                # Record the turn
                await s.post(
                    f"{memory_url}/sessions/{session_id}/turns",
                    json={
                        "speaker": speaker,
                        "text": text,
                        "metadata": metadata or {},
                    },
                    timeout=aiohttp.ClientTimeout(total=2),
                )
        except Exception:
            pass  # Tier 1 recording failure must never block the user

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

        url = f"{supervisor_service.address}/self-evolution/tasks"
        params = {"status": "approved"}
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
        except Exception as e:
            logger.error(f"Failed to fetch approved tasks: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to fetch tasks: {e}")

    async def complete_task(self, task_id: str, request: Request):
        return await self._forward_task_decision(
            task_id,
            request,
            default_decision="completed",
            default_reason="Task completed by agent",
            default_actor="agent",
        )

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

        url = f"{supervisor_service.address}/self-evolution/tasks/{task_id}/decision"

        try:
            body = await request.body()
            data = json.loads(body.decode("utf-8")) if body else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        decision = str(data.get("decision") or default_decision).strip().lower()
        payload = {
            "decision": decision,
            "reason": data.get("reason", default_reason),
            "actor": data.get("actor", default_actor),
            "context": data.get("context", {}),
            "metadata": data.get("metadata", {}),
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=resp.status, detail=f"Supervisor returned {resp.status}")
                    result = await resp.json()
            self._touch_activity(
                "agent_work",
                source_service="gateway",
                metadata={"task_id": task_id, "decision": decision},
            )
            return result
        except Exception as e:
            logger.error(f"Failed to forward task decision: {e}")
            raise HTTPException(status_code=502, detail=f"Failed to forward task decision: {e}")

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

            if self._governor_mode_active:
                raise HTTPException(
                    status_code=503,
                    detail="系统处于全自动模式，agent 正在自主规划并探索学习。请稍后再试。",
                )
            
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
                    "AUTO and task execution must run on the current CLI/API-A executor."
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
            "active_cli_session_id": self._active_cli_session_id,
            "active_cli_executor": self._build_active_cli_executor_snapshot(),
        }

    async def delete_session(self, session_id: str):
        if session_id not in self._agent_session_cache:
            raise HTTPException(status_code=404, detail="Session not found")
        if session_id == self._active_cli_session_id:
            self._active_cli_session_id = None
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
