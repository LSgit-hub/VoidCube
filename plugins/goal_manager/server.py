"""FastAPI application for the Goal Manager service."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from .config import service_config
from .db.connection import GoalStore
from .domain.graph import GoalConflict
from .domain.guard import ConfirmationRequired


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    description: str = ""
    created_by: str = "agent"
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None
    idempotency_key: str | None = None
    root_status: str = "planned"


class NodeCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_id: str
    node_type: str | None = None
    type: str | None = None
    title: str
    description: str = ""
    status: str = "planned"
    progress: float = Field(default=0, ge=0, le=1)
    progress_mode: str = "manual"
    confidence: float = Field(default=1, ge=0, le=1)
    priority: int = 0
    start_at: str | None = None
    due_at: str | None = None
    completed_at: str | None = None
    acceptance_criteria: list[dict[str, Any]] = Field(default_factory=list)
    owner: str | None = None
    assigned_to: str | None = None
    created_by: str = "agent"
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class NodeUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")
    expected_version: int
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class NodeComplete(BaseModel):
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class EvidenceVerificationApply(BaseModel):
    verification_id: str
    expected_version: int
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class ReviewSubmission(BaseModel):
    expected_version: int
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class ReviewApproval(BaseModel):
    expected_version: int
    reason: str
    actor_type: str = "user"
    actor_id: str | None = None
    session_id: str | None = None


class ReviewRejection(BaseModel):
    expected_version: int
    reason: str
    actor_type: str = "user"
    actor_id: str | None = None
    session_id: str | None = None


class EdgeCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_id: str
    target_id: str
    edge_type: str
    progress_weight: float = Field(default=1, ge=0)
    required: bool = True
    created_by: str = "agent"
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_id: str
    reason: str
    operations: list[dict[str, Any]]
    created_by: str = "agent"
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None
    confirm_token: str | None = None


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    batch_id: str
    reason: str = "rollback batch"
    confirm: bool = False
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class RedoRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    project_id: str | None = None
    batch_id: str | None = None
    reason: str = "redo batch"
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class EvidenceCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_type: str
    title: str | None = None
    content: str | None = None
    uri: str | None = None
    created_by: str = "agent"
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class ExecutionResultCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    status: str
    summary: str
    outputs: list[Any] | dict[str, Any] = Field(default_factory=list)
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class ObservationCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    execution_result_id: str | None = None
    summary: str
    signals: list[Any] | dict[str, Any] = Field(default_factory=list)
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class EvidenceVerificationCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str | None = None
    accepted: bool
    summary: str
    criterion_index: int | None = Field(default=None, ge=0)
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class ResultAcceptanceCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    accepted: bool
    summary: str
    accepted_by: str | None = None
    reason: str
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class IntentContractRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    outcome: str
    success_criteria: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    reason: str = "set intent contract"
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


class PlanVersionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    reason: str = "create plan version"
    actor_type: str = "agent"
    actor_id: str | None = None
    session_id: str | None = None


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, ConfirmationRequired):
        return JSONResponse(
            status_code=409,
            content={
                "detail": exc.detail,
                "requires_confirm": True,
                "confirm_token": exc.token,
            },
        )
    if isinstance(exc, GoalConflict):
        return JSONResponse(
            status_code=409,
            content={"detail": exc.detail, **exc.payload},
        )
    if isinstance(exc, KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})
    if isinstance(exc, (ValueError, TypeError)):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    return JSONResponse(status_code=500, content={"detail": "goal_service_internal_error"})


def create_app(config: dict[str, Any] | None = None) -> FastAPI:
    runtime_config = service_config(config)
    app = FastAPI(title="VoidCube Goal Manager", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:6002",
            "http://localhost:6002",
        ],
        allow_origin_regex=r"http://(127\.0\.0\.1|localhost):\d+",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "Authorization", "X-Goal-Service-Token"],
    )
    store = GoalStore(runtime_config["db_path"])
    app.state.goal_store = store
    configured_token = str(runtime_config.get("service_token") or "").strip()

    @app.middleware("http")
    async def require_service_token(request: Request, call_next):
        if configured_token and request.url.path.startswith("/api/"):
            supplied = str(
                request.headers.get("x-goal-service-token")
                or request.headers.get("authorization", "").removeprefix("Bearer ")
            ).strip()
            if supplied != configured_token:
                return JSONResponse(status_code=401, content={"detail": "invalid goal service token"})
        return await call_next(request)

    @app.exception_handler(GoalConflict)
    async def handle_goal_conflict(_request: Request, exc: GoalConflict):
        return _error_response(exc)

    @app.exception_handler(ConfirmationRequired)
    async def handle_confirmation(_request: Request, exc: ConfirmationRequired):
        return _error_response(exc)

    @app.exception_handler(KeyError)
    async def handle_not_found(_request: Request, exc: KeyError):
        return _error_response(exc)

    @app.exception_handler(ValueError)
    async def handle_value_error(_request: Request, exc: ValueError):
        return _error_response(exc)

    @app.exception_handler(Exception)
    async def handle_exception(_request: Request, exc: Exception):
        return _error_response(exc)

    @app.on_event("shutdown")
    def close_store() -> None:
        store.close()

    async def register_with_gateway() -> None:
        gateway = str(
            runtime_config.get("gateway_address")
            or os.getenv("GATEWAY_ADDRESS")
            or "http://127.0.0.1:6000"
        ).rstrip("/")
        payload = {
            "service_name": "goal_manager",
            "service_type": "goal_service",
            "address": f"http://127.0.0.1:{runtime_config['service_port']}",
            "health_endpoint": "/health",
            "metadata": {"version": "0.1.0", "plugin": "goal_manager"},
        }
        headers = {}
        gateway_token = str(
            runtime_config.get("gateway_auth_token")
            or os.getenv("GATEWAY_AUTH_TOKEN")
            or ""
        ).strip()
        if gateway_token:
            headers["Authorization"] = f"Bearer {gateway_token}"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(f"{gateway}/register", json=payload, headers=headers)
        except Exception:
            # Gateway registration is an optional control-plane signal; the
            # Goal Service remains directly usable when Gateway is unavailable.
            return

    @app.on_event("startup")
    async def schedule_gateway_registration() -> None:
        app.state.gateway_registration_task = asyncio.create_task(register_with_gateway())

    @app.get("/")
    def root() -> dict[str, Any]:
        return {"service": "goal_manager", "status": "ok"}

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"service": "goal_manager", "status": "ok"}

    @app.get("/api/goals/projects")
    def list_projects() -> dict[str, Any]:
        return {"projects": store.list_projects()}

    @app.post("/api/goals/projects", status_code=201)
    def create_project(payload: ProjectCreate) -> dict[str, Any]:
        return store.create_project(**payload.model_dump())

    @app.get("/api/goals/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return store.get_project(project_id)

    @app.put("/api/goals/projects/{project_id}/intent-contract")
    def set_intent_contract(project_id: str, payload: IntentContractRequest) -> dict[str, Any]:
        data = payload.model_dump()
        contract = {key: data[key] for key in (
            "outcome", "success_criteria", "scope", "constraints", "assumptions", "open_questions"
        )}
        return store.set_intent_contract(
            project_id, contract, reason=data["reason"], actor_type=data["actor_type"],
            actor_id=data["actor_id"], session_id=data["session_id"],
        )

    @app.get("/api/goals/projects/{project_id}/intent-contract")
    def get_intent_contract(project_id: str) -> dict[str, Any]:
        return store.get_intent_contract(project_id)

    @app.get("/api/goals/projects/{project_id}/plan-review")
    def plan_review(project_id: str) -> dict[str, Any]:
        return store.review_plan(project_id)

    @app.get("/api/goals/projects/{project_id}/protocol-next-action")
    def protocol_next_action(
        project_id: str,
        limit: int = Query(10, ge=1, le=100),
    ) -> dict[str, Any]:
        return store.protocol_next_action(project_id, limit)

    @app.get("/api/goals/projects/{project_id}/plan-versions")
    def plan_versions(
        project_id: str,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        return {"plan_versions": store.list_plan_versions(project_id, limit)}

    @app.post("/api/goals/projects/{project_id}/plan-versions", status_code=201)
    def create_plan_version(project_id: str, payload: PlanVersionRequest) -> dict[str, Any]:
        return store.create_plan_version(project_id, **payload.model_dump())

    @app.post("/api/goals/projects/{project_id}/replan", status_code=201)
    def replan(project_id: str, payload: PlanVersionRequest) -> dict[str, Any]:
        return store.replan(project_id, **payload.model_dump())

    @app.get("/api/goals/projects/{project_id}/focus")
    def focus(project_id: str, node: str | None = None) -> dict[str, Any]:
        return store.get_focus(project_id, node)

    @app.get("/api/goals/projects/{project_id}/overview")
    def overview(project_id: str, mode: str = Query("parents_only")) -> dict[str, Any]:
        return store.overview(project_id, mode)

    @app.get("/api/goals/projects/{project_id}/graph")
    def graph(
        project_id: str,
        start_node: str,
        depth: int = Query(3, ge=0, le=3),
        edge_types: list[str] | None = Query(None),
    ) -> dict[str, Any]:
        return store.graph_query(project_id, start_node, depth, edge_types)

    @app.get("/api/goals/nodes/{node_id}")
    def get_node(node_id: str) -> dict[str, Any]:
        return store.get_node(node_id)

    @app.post("/api/goals/nodes", status_code=201)
    def create_node(payload: NodeCreate) -> dict[str, Any]:
        return store.create_node(
            payload.project_id,
            payload.model_dump(exclude={"project_id", "created_by", "reason", "actor_type", "actor_id", "session_id"}),
            created_by=payload.created_by,
            reason=payload.reason,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            session_id=payload.session_id,
        )

    @app.patch("/api/goals/nodes/{node_id}")
    def update_node(node_id: str, payload: NodeUpdate) -> dict[str, Any]:
        return store.update_node(node_id, payload.expected_version, payload.patch, **payload.model_dump(exclude={"expected_version", "patch"}))

    @app.get("/api/goals/nodes/{node_id}/completion-check")
    def completion_check(node_id: str) -> dict[str, Any]:
        return store.completion_check(node_id)

    @app.post("/api/goals/nodes/{node_id}/complete")
    def complete_node(node_id: str, payload: NodeComplete) -> dict[str, Any]:
        return store.complete_node(node_id, **payload.model_dump())

    @app.post("/api/goals/nodes/{node_id}/apply-evidence-verification")
    def apply_evidence_verification(node_id: str, payload: EvidenceVerificationApply) -> dict[str, Any]:
        return store.apply_evidence_verification(node_id, **payload.model_dump())

    @app.post("/api/goals/nodes/{node_id}/submit-for-review")
    def submit_for_review(node_id: str, payload: ReviewSubmission) -> dict[str, Any]:
        return store.submit_for_review(node_id, **payload.model_dump())

    @app.post("/api/goals/nodes/{node_id}/approve-review")
    def approve_review(node_id: str, payload: ReviewApproval) -> dict[str, Any]:
        return store.approve_review(node_id, **payload.model_dump())

    @app.post("/api/goals/nodes/{node_id}/reject-review")
    def reject_review(node_id: str, payload: ReviewRejection) -> dict[str, Any]:
        return store.reject_review(node_id, **payload.model_dump())

    @app.delete("/api/goals/nodes/{node_id}")
    def delete_node(
        node_id: str,
        reason: str = Query(...),
        cascade: bool = False,
        confirm_token: str | None = None,
        actor_type: str = "agent",
        actor_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return store.delete_node(
            node_id, cascade=cascade, reason=reason, confirm_token=confirm_token,
            actor_type=actor_type, actor_id=actor_id, session_id=session_id,
        )

    @app.post("/api/goals/edges", status_code=201)
    def create_edge(payload: EdgeCreate) -> dict[str, Any]:
        data = payload.model_dump()
        return store.create_edge(
            data,
            created_by=payload.created_by,
            reason=payload.reason,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            session_id=payload.session_id,
        )

    @app.delete("/api/goals/edges/{edge_id}")
    def delete_edge(
        edge_id: str,
        reason: str = Query(...),
        actor_type: str = "agent",
        actor_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return store.delete_edge(edge_id, reason=reason, actor_type=actor_type, actor_id=actor_id, session_id=session_id)

    @app.post("/api/goals/batch")
    def apply_batch(payload: BatchRequest) -> dict[str, Any]:
        return store.apply_batch(**payload.model_dump())

    @app.post("/api/goals/rollback")
    def rollback(payload: RollbackRequest) -> dict[str, Any]:
        return store.rollback(**payload.model_dump())

    @app.post("/api/goals/redo")
    def redo(payload: RedoRequest) -> dict[str, Any]:
        return store.redo(**payload.model_dump())

    @app.post("/api/goals/nodes/{node_id}/evidence", status_code=201)
    def attach_evidence(node_id: str, payload: EvidenceCreate) -> dict[str, Any]:
        return store.attach_evidence(
            node_id, payload.model_dump(exclude={"created_by", "reason", "actor_type", "actor_id", "session_id"}),
            created_by=payload.created_by, reason=payload.reason, actor_type=payload.actor_type,
            actor_id=payload.actor_id, session_id=payload.session_id,
        )

    @app.get("/api/goals/nodes/{node_id}/lifecycle")
    def lifecycle(node_id: str) -> dict[str, Any]:
        return store.get_lifecycle(node_id)

    @app.post("/api/goals/nodes/{node_id}/execution-results", status_code=201)
    def record_execution_result(node_id: str, payload: ExecutionResultCreate) -> dict[str, Any]:
        return store.record_execution_result(
            node_id,
            payload.model_dump(exclude={"reason", "actor_type", "actor_id", "session_id"}),
            reason=payload.reason, actor_type=payload.actor_type,
            actor_id=payload.actor_id, session_id=payload.session_id,
        )

    @app.post("/api/goals/nodes/{node_id}/observations", status_code=201)
    def record_observation(node_id: str, payload: ObservationCreate) -> dict[str, Any]:
        return store.record_observation(
            node_id,
            payload.model_dump(exclude={"reason", "actor_type", "actor_id", "session_id"}),
            reason=payload.reason, actor_type=payload.actor_type,
            actor_id=payload.actor_id, session_id=payload.session_id,
        )

    @app.post("/api/goals/nodes/{node_id}/evidence-verifications", status_code=201)
    def verify_evidence(node_id: str, payload: EvidenceVerificationCreate) -> dict[str, Any]:
        return store.verify_evidence(
            node_id,
            payload.model_dump(exclude={"reason", "actor_type", "actor_id", "session_id"}),
            reason=payload.reason, actor_type=payload.actor_type,
            actor_id=payload.actor_id, session_id=payload.session_id,
        )

    @app.post("/api/goals/nodes/{node_id}/result-acceptance", status_code=201)
    def accept_result(node_id: str, payload: ResultAcceptanceCreate) -> dict[str, Any]:
        return store.accept_result(
            node_id,
            payload.model_dump(exclude={"reason", "actor_type", "actor_id", "session_id"}),
            reason=payload.reason, actor_type=payload.actor_type,
            actor_id=payload.actor_id, session_id=payload.session_id,
        )

    @app.get("/api/goals/events")
    def events(project_id: str, after: str | None = None, limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        return {"events": store.list_events(project_id, after, limit)}

    @app.get("/api/goals/events/latest")
    def latest_event(project_id: str) -> dict[str, Any]:
        return {"event_id": store.latest_event_id(project_id)}

    @app.get("/api/goals/projects/{project_id}/history")
    def history(project_id: str) -> dict[str, Any]:
        return store.history(project_id)

    @app.get("/api/goals/events/stream")
    @app.get("/api/goals/projects/{project_id}/events")
    async def event_stream(
        project_id: str,
        after: str | None = None,
        poll_seconds: float = Query(1.0, ge=0.2, le=10.0),
        max_seconds: float = Query(25.0, ge=1.0, le=60.0),
    ) -> StreamingResponse:
        store.get_project(project_id)

        async def generate():
            cursor = after or store.latest_event_id(project_id)
            started = asyncio.get_running_loop().time()
            while asyncio.get_running_loop().time() - started < max_seconds:
                changes = store.list_events(project_id, cursor, 100)
                if changes:
                    for event in changes:
                        yield f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    cursor = changes[-1]["id"]
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(poll_seconds)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/goals/nodes/{node_id}/context")
    def context(node_id: str) -> dict[str, Any]:
        return store.get_context(node_id)

    @app.get("/api/goals/projects/{project_id}/next-actions")
    def next_actions(
        project_id: str,
        limit: int = Query(10, ge=1, le=100),
        filters: str | None = None,
    ) -> dict[str, Any]:
        parsed_filters: dict[str, Any] = {}
        if filters:
            try:
                parsed_filters = json.loads(filters)
            except json.JSONDecodeError as exc:
                raise ValueError("filters must be a JSON object") from exc
            if not isinstance(parsed_filters, dict):
                raise ValueError("filters must be a JSON object")
        return {"actions": store.next_actions(project_id, limit, parsed_filters)}

    return app
