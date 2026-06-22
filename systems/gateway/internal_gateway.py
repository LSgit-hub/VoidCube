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
    lifecycle_state: str = "active"


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


class InternalGateway:
    def __init__(self, config: GatewayConfig = None):
        self.config = config or GatewayConfig()
        self.app = FastAPI(title="VoidCube Internal Gateway", version="1.0")
        self._services: Dict[str, ServiceInfo] = {}
        self._routes: Dict[str, RouteEntry] = {}
        self._active_body_service_id: str = None
        self._request_counter = 0
        # NOTE(SB-03): Session cache is body-runtime state, not gateway operations
        # state.  Long-term session ownership should belong to the agent body
        # instances.  The gateway should only hold routing metadata.  TTL eviction
        # (SB-04) mitigates unbounded growth for now.
        self._agent_session_cache: Dict[str, Dict[str, Any]] = {}
        self._session_ttl_seconds: int = 3600  # evict sessions idle >1 hour
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
        
        self.app.add_api_route("/admin/body/activate", self.activate_body, methods=["POST"])
        self.app.add_api_route("/admin/body/status", self.get_body_status, methods=["GET"])
        self.app.add_api_route("/admin/activity", self.get_activity_status, methods=["GET"])
        self.app.add_api_route("/admin/activity/log", self.get_activity_log, methods=["GET"])
        self.app.add_api_route("/admin/activity/touch", self.touch_activity, methods=["POST"])
        
        self.app.add_api_route("/register", self.register_service, methods=["POST"])
        self.app.add_api_route("/health/{service_id}", self.update_health, methods=["POST"])
        
        self.app.add_api_route("/v1/chat/completions", self.chat_completions_proxy, methods=["POST"])
        self.app.add_api_route("/v1/agent/query", self.agent_query_proxy, methods=["POST"])
        self.app.add_api_route("/v1/sessions/{session_id}", self.get_session_info, methods=["GET"])
        self.app.add_api_route("/v1/sessions/{session_id}", self.delete_session, methods=["DELETE"])
        self.app.add_api_route("/admin/traces/{trace_id}", self.get_trace, methods=["GET"])

    async def health_check(self):
        active_body = self._build_active_body_snapshot()
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
            "active_body": active_body,
            "body_slots": self._list_body_slots(),
            "body_routing": self._build_body_routing_snapshot(active_body=active_body),
            "registered_services": registered_services,
            "executor_access_policy": self._build_executor_access_policy(),
            "activity": self._build_activity_snapshot(),
            "routes": [self._serialize_route(route) for route in self._routes.values()]
        }

    def _get_active_body_service(self) -> Optional[ServiceInfo]:
        if not self._active_body_service_id:
            return None
        return self._services.get(self._active_body_service_id)

    def _serialize_body_service(self, service: ServiceInfo) -> Dict[str, Any]:
        return {
            "service_id": service.service_id,
            "slot_id": service.metadata.get("slot_id"),
            "body_version": service.metadata.get("body_version"),
            "service_name": service.service_name,
            "address": service.address,
            "healthy": service.healthy,
            "lifecycle_state": service.lifecycle_state,
            "is_active_body": service.service_id == self._active_body_service_id,
            "registered_at": service.registered_at.isoformat() if service.registered_at else None,
        }

    def _list_body_slots(self) -> List[Dict[str, Any]]:
        return [
            self._serialize_body_service(service)
            for service in self._services.values()
            if service.service_type == "agent"
        ]

    def _build_active_body_snapshot(self) -> Optional[Dict[str, Any]]:
        active_service = self._get_active_body_service()
        if active_service is None:
            return None
        return self._serialize_body_service(active_service)

    def _build_body_routing_snapshot(
        self,
        *,
        active_body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        active_body = active_body if active_body is not None else self._build_active_body_snapshot()
        api_route = self._routes.get("/api/")
        return {
            "api_route_target_instance": api_route.target_instance if api_route else None,
            "active_service_id": active_body["service_id"] if active_body else None,
            "active_slot_id": active_body["slot_id"] if active_body else None,
        }

    def _activate_body_service(self, target_service: ServiceInfo) -> Dict[str, Any]:
        self._active_body_service_id = target_service.service_id
        for service in self._services.values():
            if service.service_type == "agent":
                service.lifecycle_state = (
                    "active" if service.service_id == target_service.service_id else "draining"
                )

        route = self._routes.get("/api/")
        if route:
            route.target_instance = target_service.service_id
        else:
            self._routes["/api/"] = RouteEntry(
                path_prefix="/api/",
                target_service="agent",
                target_instance=target_service.service_id,
                weight=100,
                enabled=True,
            )

        return self._build_body_routing_snapshot(
            active_body=self._serialize_body_service(target_service)
        )

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
        return session

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
        supported = True

        if normalized == "user_request":
            self._activity_state["last_user_request_at"] = now
            self._activity_state["user_request_count"] += 1
            self._activity_state["last_agent_work_at"] = now
            self._activity_state["agent_work_count"] += 1
            self._activity_state["recent_metadata"]["user_request"] = activity_metadata
            self._activity_state["recent_metadata"]["agent_work"] = activity_metadata
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

    def _persist_activity_state(self) -> None:
        """Write activity state + log to disk (debounced, fire-and-forget)."""
        path = self.config.activity_log_path
        if not path:
            return
        # Debounce: skip writes within 2s of the last persist to avoid
        # high-frequency disk I/O on every activity touch (G-02).
        import time as _time
        now = _time.monotonic()
        last = getattr(self, '_last_persist_ts', 0.0)
        if now - last < 2.0:
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
        if source_service:
            payload.setdefault("source_service", source_service)
        if session_id:
            payload.setdefault("session_id", session_id)
        return payload or None

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
            
            if service_type == "agent":
                if not self._active_body_service_id:
                    self._activate_body_service(service_info)
            
            await self._auto_configure_route(service_type, service_id, address)
            
            logger.info(f"Service registered: {service_name} ({service_id}) at {address}")
            return JSONResponse(content={"service_id": service_id, "status": "registered"}, status_code=201)
            
        except Exception as e:
            logger.error(f"Error registering service: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def activate_body(self, request: Request):
        try:
            data = await request.json()
            service_id = data.get("service_id")
            slot_id = data.get("slot_id")

            target_service = None
            if service_id:
                target_service = self._services.get(service_id)
            elif slot_id:
                for service in self._services.values():
                    if service.service_type == "agent" and service.metadata.get("slot_id") == slot_id:
                        target_service = service
                        break

            if not target_service:
                raise HTTPException(status_code=404, detail="No matching body service found")
            if target_service.service_type != "agent":
                raise HTTPException(status_code=400, detail="Only agent services can be activated as a body")
            if not target_service.healthy:
                raise HTTPException(status_code=503, detail="Target body service unhealthy")

            body_routing = self._activate_body_service(target_service)
            active_body = self._serialize_body_service(target_service)

            self._touch_activity("self_evolution", metadata={
                "action": "body_activation",
                "slot_id": target_service.metadata.get("slot_id"),
                "service_id": target_service.service_id,
            })
            logger.info(
                "Body activation synced: slot=%s service=%s",
                target_service.metadata.get("slot_id"),
                target_service.service_id,
            )
            return {
                "status": "activated",
                "active_body": active_body,
                "body_routing": body_routing,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error activating body: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def get_body_status(self):
        active = self._build_active_body_snapshot()
        return {
            "active_body": active,
            "body_routing": self._build_body_routing_snapshot(active_body=active),
            "body_slots": self._list_body_slots(),
        }

    async def _auto_configure_route(self, service_type: str, service_id: str, address: str):
        route_map = {
            "memory": "/mem/",
            "agent": "/agent/",
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
            
            if self._active_body_service_id == service_id:
                self._active_body_service_id = None
                for sid, s in self._services.items():
                    if s.service_type == "agent" and s.healthy:
                        self._activate_body_service(s)
                        break
            
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
        import time
        cutoff = time.time() - self._session_ttl_seconds
        stale = [
            sid for sid, s in self._agent_session_cache.items()
            if s.get('last_access', 0) < cutoff
        ]
        for sid in stale:
            del self._agent_session_cache[sid]

    async def chat_completions_proxy(self, request: Request):
        self._evict_stale_sessions()
        self._request_counter += 1
        request_id = str(uuid.uuid4())
        
        try:
            if not self._active_body_service_id:
                raise HTTPException(status_code=503, detail="No active body service available")
            
            target_service = self._services.get(self._active_body_service_id)
            if not target_service:
                raise HTTPException(status_code=503, detail="Active body service not found")
            
            if not target_service.healthy:
                raise HTTPException(status_code=503, detail="Active body service unhealthy")

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

            url = f"{target_service.address}/v1/chat/completions"
            logger.debug(f"Proxying chat completion {request_id} -> {url}")

            headers = dict(request.headers)
            
            async with asyncio.timeout(60):
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, data=body, headers=headers) as response:
                        response_body = await response.read()
                        response_headers = dict(response.headers)
                        
                        return Response(
                            content=response_body,
                            status_code=response.status,
                            headers=response_headers
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
            
            if not self._active_body_service_id:
                raise HTTPException(status_code=503, detail="No active body service available")
            
            target_service = self._services.get(self._active_body_service_id)
            if not target_service:
                raise HTTPException(status_code=503, detail="Active body service not found")
            
            if not target_service.healthy:
                raise HTTPException(status_code=503, detail="Active body service unhealthy")
            
            url = f"{target_service.address}/v1/agent/query"
            logger.debug(f"Proxying agent query {request_id} -> {url}")
            
            session_id = data.get("session_id") or str(uuid.uuid4())
            activity_metadata = self._extract_activity_metadata_from_payload(data)
            self._touch_activity("user_request", session_id=session_id, metadata=activity_metadata)
            
            if session_id not in self._agent_session_cache:
                self._agent_session_cache[session_id] = {
                    "created_at": datetime.now(),
                    "last_used_at": datetime.now(),
                    "message_count": 0
                }
            
            self._agent_session_cache[session_id]["last_used_at"] = datetime.now()
            self._agent_session_cache[session_id]["message_count"] += 1
            
            body = json.dumps(data).encode('utf-8')
            headers = {"Content-Type": "application/json"}
            
            async with asyncio.timeout(120):
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, data=body, headers=headers) as response:
                        response_data = await response.json()
                        
                        return JSONResponse(
                            content={
                                "session_id": session_id,
                                "response": response_data,
                                "metadata": self._serialize_agent_session_metadata(session_id),
                            },
                            status_code=response.status
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
        session_data = self._agent_session_cache.get(session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found")
        
        return {
            "session_id": session_id,
            **session_data,
            "active_body_service_id": self._active_body_service_id,
            "active_body_address": (
                self._services[self._active_body_service_id].address
                if self._active_body_service_id and self._active_body_service_id in self._services
                else None
            ),
            "active_slot_id": (
                self._services[self._active_body_service_id].metadata.get("slot_id")
                if self._active_body_service_id and self._active_body_service_id in self._services
                else None
            ),
        }

    async def delete_session(self, session_id: str):
        if session_id not in self._agent_session_cache:
            raise HTTPException(status_code=404, detail="Session not found")
        
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
