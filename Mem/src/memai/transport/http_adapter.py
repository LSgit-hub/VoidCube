"""FastAPI route adapter for the Memory application service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
import json
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse



HttpHandler = Callable[..., object]
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


@dataclass(frozen=True, slots=True)
class MemoryHttpRoute:
    path: str
    handler: str
    methods: tuple[str, ...]


MEMORY_HTTP_ROUTES = (
    MemoryHttpRoute("/", "health_check", ("GET",)),
    MemoryHttpRoute("/health", "health_check", ("GET",)),
    MemoryHttpRoute("/outbox/health", "report_agent_outbox_health", ("POST",)),
    MemoryHttpRoute("/mem/usage", "get_mem_usage", ("GET",)),
    MemoryHttpRoute("/sessions", "create_session", ("POST",)),
    MemoryHttpRoute("/sessions", "list_sessions", ("GET",)),
    MemoryHttpRoute("/sessions/{session_id}", "get_session", ("GET",)),
    MemoryHttpRoute("/sessions/{session_id}/close", "close_session", ("POST",)),
    MemoryHttpRoute(
        "/time-summaries/days/{day_key}/aggregate",
        "aggregate_day",
        ("POST",),
    ),
    MemoryHttpRoute(
        "/time-summaries/weeks/{week_key}/aggregate",
        "aggregate_week",
        ("POST",),
    ),
    MemoryHttpRoute(
        "/time-summaries/months/{month_key}/aggregate",
        "aggregate_month",
        ("POST",),
    ),
    MemoryHttpRoute("/sessions/{session_id}/turns", "add_turn", ("POST",)),
    MemoryHttpRoute("/sessions/{session_id}/turns", "get_session_turns", ("GET",)),
    MemoryHttpRoute("/turn-pairs", "add_turn_pair", ("POST",)),
    MemoryHttpRoute("/turns", "query_turns", ("GET",)),
    MemoryHttpRoute("/turns/{turn_id}", "get_turn", ("GET",)),
    MemoryHttpRoute("/turns/timeline", "timeline_view", ("POST",)),
    MemoryHttpRoute("/recall", "recall", ("POST",)),
    MemoryHttpRoute("/recall/traces", "list_recall_traces", ("GET",)),
    MemoryHttpRoute("/recall/feedback", "record_recall_feedback", ("POST",)),
    MemoryHttpRoute("/promotion-candidates", "create_promotion_candidate", ("POST",)),
    MemoryHttpRoute("/promotion-candidates", "list_promotion_candidates", ("GET",)),
    MemoryHttpRoute(
        "/promotion-candidates/{candidate_id}/consent",
        "consent_promotion_candidate",
        ("POST",),
    ),
    MemoryHttpRoute("/promotions", "list_promotions", ("GET",)),
    MemoryHttpRoute("/promotions/{promotion_id}/revoke", "revoke_promotion", ("POST",)),
    MemoryHttpRoute("/forget", "forget_memory", ("POST",)),
    MemoryHttpRoute("/remember", "remember", ("POST",)),
    MemoryHttpRoute("/identity/archive", "get_identity_archive", ("GET",)),
    MemoryHttpRoute("/identity/sync", "sync_identity_archive", ("POST",)),
    MemoryHttpRoute(
        "/identity/experiences/self-author",
        "author_identity_experience",
        ("POST",),
    ),
    MemoryHttpRoute("/identity/revisions", "list_identity_revisions", ("GET",)),
    MemoryHttpRoute("/identity/revisions", "propose_identity_revision", ("POST",)),
    MemoryHttpRoute(
        "/identity/revisions/{proposal_id}/decision",
        "decide_identity_revision",
        ("POST",),
    ),
    MemoryHttpRoute("/tier2/compress", "tier2_compress", ("POST",)),
    MemoryHttpRoute("/tier1/stats", "tier1_stats", ("GET",)),
    MemoryHttpRoute("/compressed/search", "search_compressed", ("POST",)),
    MemoryHttpRoute("/compressed/retention-review", "review_retention", ("POST",)),
    MemoryHttpRoute(
        "/compressed/trace/{turn_id}",
        "trace_compressed_by_turn",
        ("GET",),
    ),
    MemoryHttpRoute("/compressed/lifecycle", "trigger_lifecycle", ("POST",)),
    MemoryHttpRoute("/compressed/run-all-rules", "run_all_rules", ("POST",)),
    MemoryHttpRoute("/compressed/rules-status", "rules_status", ("GET",)),
    MemoryHttpRoute("/compressed/{memory_id}", "get_compressed", ("GET",)),
    MemoryHttpRoute("/compressed/{memory_id}/pin", "pin_memory", ("POST",)),
    MemoryHttpRoute("/compressed/{memory_id}/hide", "hide_memory", ("POST",)),
    MemoryHttpRoute("/compressed/{memory_id}/unpin", "unpin_memory", ("POST",)),
    MemoryHttpRoute("/llm/health", "llm_health", ("GET",)),
    MemoryHttpRoute("/semantic/status", "semantic_status", ("GET",)),
    MemoryHttpRoute("/semantic/backfill", "semantic_backfill", ("POST",)),
    MemoryHttpRoute("/admin/backups", "create_backup", ("POST",)),
    MemoryHttpRoute("/admin/backups", "list_backups", ("GET",)),
    MemoryHttpRoute(
        "/admin/backups/{backup_id}/restore",
        "restore_backup",
        ("POST",),
    ),
    MemoryHttpRoute("/admin/exports", "export_memory", ("POST",)),
    MemoryHttpRoute("/graph/entities", "list_graph_entities", ("GET",)),
    MemoryHttpRoute("/graph/rebuild", "rebuild_entity_graph", ("POST",)),
    MemoryHttpRoute(
        "/graph/neighbors/{entity_id}",
        "get_graph_neighbors",
        ("GET",),
    ),
    MemoryHttpRoute("/compressed/quality", "compression_quality", ("GET",)),
)


def build_memory_http_app(
    handlers: Mapping[str, HttpHandler],
    *,
    lifespan: Lifespan,
    service_token: str | None = None,
    service_tokens: Mapping[str, str] | None = None,
    service_actor: str = "api_a",
) -> FastAPI:
    """Build the HTTP adapter from explicit application handler ports."""
    expected = {route.handler for route in MEMORY_HTTP_ROUTES}
    missing = expected - handlers.keys()
    unexpected = handlers.keys() - expected
    if missing or unexpected:
        raise ValueError(
            "Memory HTTP handler mismatch: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )

    app = FastAPI(
        title="VoidCube Memory Service",
        version="1.0",
        lifespan=lifespan,
    )

    configured_token = str(service_token or "").strip()
    configured_actor = str(service_actor or "").strip()
    actor_tokens = {
        str(actor).strip(): str(token).strip()
        for actor, token in (service_tokens or {}).items()
        if str(actor).strip() and str(token).strip()
    }
    if configured_token:
        if not configured_actor:
            raise ValueError("Memory service_actor is required when service_token is set")
        actor_tokens.setdefault(configured_actor, configured_token)

    @app.middleware("http")
    async def enforce_local_identity(request: Request, call_next):
        # Health is intentionally public so the process supervisor can probe it.
        if request.url.path in {"/", "/health"}:
            return await call_next(request)

        if actor_tokens:
            authorization = str(request.headers.get("authorization") or "")
            supplied_token = authorization.removeprefix("Bearer ").strip()
            supplied_actor = str(
                request.headers.get("x-voidcube-memory-actor") or ""
            ).strip()
            expected_token = actor_tokens.get(supplied_actor, "")
            if not expected_token or not secrets.compare_digest(supplied_token, expected_token):
                return JSONResponse(
                    {"error": "unauthorized", "detail": "invalid Memory Service token"},
                    status_code=401,
                )

        body = await request.body()
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(
                    {"error": "validation_error", "detail": "invalid JSON body"},
                    status_code=400,
                )
            if isinstance(payload, dict):
                # The transport-level idempotency key is part of the request
                # envelope. Bind it to the durable-memory command so callers
                # do not need to duplicate the key in JSON body and headers.
                if request.url.path == "/remember":
                    idempotency_key = str(
                        request.headers.get("idempotency-key") or ""
                    ).strip()
                    body_key = str(payload.get("idempotency_key") or "").strip()
                    if idempotency_key and body_key and idempotency_key != body_key:
                        return JSONResponse(
                            {
                                "error": "validation_error",
                                "detail": "idempotency_key does not match Idempotency-Key header",
                            },
                            status_code=422,
                        )
                    if idempotency_key and not body_key:
                        payload["idempotency_key"] = idempotency_key
                        request._body = json.dumps(
                            payload, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                for key, header_name in (
                    ("memory_actor", "x-voidcube-memory-actor"),
                    ("owner_id", "x-voidcube-owner-id"),
                    ("workspace_id", "x-voidcube-workspace-id"),
                ):
                    value = payload.get(key)
                    header_value = request.headers.get(header_name)
                    if value is not None and header_value is not None and str(value) != str(header_value):
                        return JSONResponse(
                            {"error": "validation_error", "detail": f"{key} does not match request identity"},
                            status_code=422,
                        )

        return await call_next(request)
    for route in MEMORY_HTTP_ROUTES:
        app.add_api_route(
            route.path,
            handlers[route.handler],
            methods=list(route.methods),
        )
    return app
