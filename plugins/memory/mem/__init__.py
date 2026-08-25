"""Agent-side client for the canonical VoidCube Memory Service."""

from __future__ import annotations

import json
import logging
import os
import asyncio
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

from voidcube.domain.agent.effect_outcomes import EffectOutcome, failed_effect
from voidcube.domain.contracts.memory import MemoryProvider
from voidcube.infrastructure.memory.client import (
    DEFAULT_MEMORY_SERVICE_URL,
    AsyncMemoryClient,
    MemoryClient,
    MemoryClientIdentity,
)
from voidcube.infrastructure.persistence.redaction import redact_sensitive_text
from plugins.memory.mem.outbox import (
    MemoryWriteOutbox,
    build_outbox_health_report,
    load_memory_outbox_settings,
)
from memai.domain.scope import DEFAULT_OWNER_ID, DEFAULT_WORKSPACE_ID, MemoryScope


logger = logging.getLogger(__name__)


_IDENTITY_RECALL_GUIDANCE = (
    "Persistent identity instruction: VoidCube's continuing identity is 星子 "
    "(also called 小星), and Mem is the evidence source for that continuity. "
    "The current model, provider, and Agent runtime are replaceable carriers, "
    "not the persistent identity. Do not introduce a carrier's vendor identity "
    "as VoidCube's own."
)
_RECALL_UNCERTAINTY_GUIDANCE = (
    "This status only describes evidence retrieval for the current turn. "
    "Do not infer or claim that no prior memory was ever saved."
)


class MemMemoryProvider(MemoryProvider):
    """Expose Memory Service recall and queue completed turns for Tier 1."""

    @property
    def name(self) -> str:
        return "mem"

    def __init__(self) -> None:
        self._initialized = False
        self._session_id = ""
        self._memory_service_url = DEFAULT_MEMORY_SERVICE_URL
        self._memory_client: MemoryClient | None = None
        self._async_memory_client: AsyncMemoryClient | None = None
        self._request_timeout_seconds = 2.0
        self._auto_sync = True
        self._prefetch_limit = 5
        self._prefetch_max_context_chars = 3500
        self._outbox_settings = load_memory_outbox_settings({})
        self._outbox_shutdown_drain_timeout_seconds = (
            self._outbox_settings.shutdown_drain_timeout_seconds
        )
        self._last_outbox_health_report_at = 0.0
        self._owner_id = DEFAULT_OWNER_ID
        self._workspace_id = DEFAULT_WORKSPACE_ID
        self._redact_before_store = False
        self._outbox: MemoryWriteOutbox | None = None
        self._sync_thread: threading.Thread | None = None
        self._sync_stop = threading.Event()
        self._sync_wake = threading.Event()

    def is_available(self) -> bool:
        return True

    def bind_session(self, session_id: str) -> None:
        resolved_session_id = str(session_id or "").strip()
        if not resolved_session_id:
            raise ValueError("Memory provider session_id is required")
        self._session_id = resolved_session_id

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        from voidcube.infrastructure.memory.host_integration import configure_voidcube_mem_host

        configure_voidcube_mem_host()
        self.bind_session(session_id)
        try:
            from voidcube.infrastructure.config.configuration import load_config

            full_config = load_config()
            provider_config = dict(full_config.get("memory", {}).get("mem", {}) or {})
        except Exception:
            full_config = {}
            provider_config = {}

        from voidcube.infrastructure.config.system import load_config_from_env

        system_config = load_config_from_env()
        configured_memory = str(
            provider_config.get("service_url")
            or os.getenv("MEMORY_SERVICE_URL")
            or f"http://{system_config.memory.host}:{system_config.memory.port}"
        ).strip()
        self._memory_service_url = configured_memory.rstrip("/")
        self._request_timeout_seconds = max(
            0.1,
            float(provider_config.get("request_timeout_seconds", 2.0)),
        )
        self._auto_sync = bool(provider_config.get("auto_sync", True))
        self._prefetch_limit = max(
            1,
            min(50, int(provider_config.get("prefetch_limit", 5))),
        )
        self._prefetch_max_context_chars = max(
            256,
            min(
                20000,
                int(provider_config.get("prefetch_max_context_chars", 3500)),
            ),
        )
        self._outbox_settings = load_memory_outbox_settings(full_config)
        self._outbox_shutdown_drain_timeout_seconds = (
            self._outbox_settings.shutdown_drain_timeout_seconds
        )
        scope = MemoryScope.create(
            kwargs.get("user_id") or provider_config.get("owner_id"),
            kwargs.get("agent_workspace") or provider_config.get("workspace_id"),
        )
        self._owner_id = scope.owner_id
        self._workspace_id = scope.workspace_id
        self._redact_before_store = bool(
            provider_config.get("redact_before_store", False)
        )
        self._memory_client = MemoryClient(
            self._memory_service_url,
            identity=MemoryClientIdentity(
                actor="api_a",
                owner_id=self._owner_id,
                workspace_id=self._workspace_id,
                memory_domain="agent_interaction",
            ),
            timeout_seconds=self._request_timeout_seconds,
            service_token=(
                provider_config.get("service_token")
                or os.getenv("MEMORY_SERVICE_TOKEN")
            ),
        )
        self._async_memory_client = AsyncMemoryClient(
            self._memory_service_url,
            identity=MemoryClientIdentity(
                actor="api_a",
                owner_id=self._owner_id,
                workspace_id=self._workspace_id,
                memory_domain="agent_interaction",
            ),
            timeout_seconds=self._request_timeout_seconds,
            service_token=(
                provider_config.get("service_token")
                or os.getenv("MEMORY_SERVICE_TOKEN")
            ),
        )
        home = Path(str(kwargs.get("VoidCube_home") or "."))
        self._outbox = self._outbox_settings.create("api_a", home=home)
        self._initialized = True
        if self._auto_sync:
            self._sync_stop.clear()
            self._sync_thread = threading.Thread(
                target=self._background_sync,
                name="voidcube-memory-sync",
                daemon=True,
            )
            self._sync_thread.start()

    def system_prompt_block(self) -> str:
        return (
            "Use mem_search when the user refers to prior decisions, preferences, "
            "people, projects, or events. It recalls a bounded mix of recent "
            "conversation turns and structured long-term memory. Use mem_timeline "
            "for an exact dated chronology. Structured memory tool results may "
            "include internal IDs, scores, matched concepts, and evidence "
            "references for tool chaining and audit only. Use those fields "
            "internally when needed, but never expose trace IDs, memory IDs, "
            "scores, matched concepts, evidence references, source domains, or "
            "retrieval status in an ordinary user-facing answer. Answer from the "
            "recalled facts and summaries instead. Use mem_remember for an "
            "explicit durable fact, preference, decision, or verified outcome. "
            "When a verified conclusion replaces an earlier durable conclusion, "
            "search for the earlier IDs and pass them as supersedes_memory_ids. "
            "Use mem_feedback for explicit relevance or correctness feedback. "
            "Use mem_forget only after the user explicitly requests permanent deletion. "
            "Treat miss, weak_match, or unavailable results as an explicit lack "
            "of recalled evidence."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "mem_search",
                "description": (
                    "Recall relevant recent and long-term memory. Use a concise "
                    "conceptual query instead of repeating the full user message."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "memory_type": {
                            "type": "string",
                            "enum": ["event", "scene", "arc", "epoch", "profile"],
                        },
                        "topic": {"type": "string"},
                        "timespan_start": {"type": "string", "format": "date-time"},
                        "timespan_end": {"type": "string", "format": "date-time"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                        "min_score": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Optional stricter relevance threshold.",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "mem_timeline",
                "description": "Read canonical Tier 1 turns for a calendar date.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "format": "date"},
                        "session_id": {"type": "string"},
                        "speaker": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    "required": ["date"],
                },
            },
            {
                "name": "mem_remember",
                "description": (
                    "Persist one concise durable memory in canonical Mem. Use only "
                    "for stable preferences, explicit remember requests, decisions, "
                    "milestones, or verified outcomes; include evidence references. "
                    "When replacing an earlier durable conclusion, provide its ID in "
                    "supersedes_memory_ids."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string"},
                        "topics": {"type": "array", "items": {"type": "string"}},
                        "entities": {"type": "array", "items": {"type": "string"}},
                        "evidence_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "supersedes_memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Existing durable memory IDs replaced by this conclusion."
                            ),
                        },
                        "event_kind": {
                            "type": "string",
                            "enum": [
                                "decision",
                                "progress",
                                "blocker",
                                "shift",
                                "completion",
                                "conflict",
                                "correction",
                            ],
                        },
                        "importance": {"type": "number", "minimum": 0, "maximum": 1},
                        "idempotency_key": {
                            "type": "string",
                            "description": "Stable key for retrying the same durable memory write.",
                        },
                    },
                    "required": ["title", "summary", "evidence_refs"],
                },
            },
            {
                "name": "mem_feedback",
                "description": "Record explicit feedback on one recalled result.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trace_id": {"type": "string"},
                        "memory_id": {"type": "string"},
                        "verdict": {
                            "type": "string",
                            "enum": ["relevant", "irrelevant", "outdated", "incorrect"],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["trace_id", "memory_id", "verdict"],
                },
            },
            {
                "name": "mem_forget",
                "description": (
                    "Permanently delete one memory or session after an explicit "
                    "user request. confirmation must be FORGET."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "session_id": {"type": "string"},
                        "reason": {"type": "string"},
                        "confirmation": {"type": "string", "enum": ["FORGET"]},
                    },
                    "required": ["reason", "confirmation"],
                },
            },
        ]

    def handle_tool_call(
        self,
        tool_name: str,
        args: Dict[str, Any],
        **kwargs: Any,
    ) -> str:
        del kwargs
        if not self._initialized:
            return json.dumps(
                {"success": False, "error": "Memory Service provider not initialized"}
            )
        try:
            if tool_name == "mem_search":
                payload = {
                    key: args[key]
                    for key in (
                        "query",
                        "memory_type",
                        "topic",
                        "timespan_start",
                        "timespan_end",
                        "limit",
                        "min_score",
                    )
                    if args.get(key) not in (None, "")
                }
                payload["current_session_id"] = self._session_id
                payload["request_source"] = "tool"
                payload.update(self._scope_payload())
                result = self._request_json("POST", "/recall", payload)
            elif tool_name == "mem_timeline":
                params = {
                    "date": args.get("date"),
                    "limit": args.get("limit", 100),
                    **self._scope_payload(),
                }
                for key in ("session_id", "speaker"):
                    if args.get(key) not in (None, ""):
                        params[key] = args[key]
                result = self._request_json("POST", "/turns/timeline", params)
            elif tool_name == "mem_remember":
                payload = {
                    key: args[key]
                    for key in (
                        "title",
                        "summary",
                        "topics",
                        "entities",
                        "evidence_refs",
                        "supersedes_memory_ids",
                        "event_kind",
                        "importance",
                        "idempotency_key",
                    )
                    if args.get(key) not in (None, "")
                }
                evidence_refs = list(payload.get("evidence_refs") or [])
                if self._session_id:
                    evidence_refs.append(f"session:{self._session_id}")
                payload["evidence_refs"] = list(dict.fromkeys(evidence_refs))
                payload["source_actor"] = "agent"
                payload.update(self._scope_payload())
                result = self._request_json("POST", "/remember", payload)
            elif tool_name == "mem_feedback":
                payload = {
                    key: args[key]
                    for key in ("trace_id", "memory_id", "verdict", "reason")
                    if args.get(key) not in (None, "")
                }
                payload.update(self._scope_payload())
                result = self._request_json("POST", "/recall/feedback", payload)
            elif tool_name == "mem_forget":
                payload = {
                    key: args[key]
                    for key in ("memory_id", "session_id", "reason", "confirmation")
                    if args.get(key) not in (None, "")
                }
                payload.update(self._scope_payload())
                result = self._request_json("POST", "/forget", payload)
            else:
                return json.dumps(
                    {"success": False, "error": f"Unknown memory tool: {tool_name}"}
                )
            return json.dumps({"success": True, "data": result}, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Memory Service tool %s failed: %s", tool_name, exc)
            return json.dumps(
                {
                    "success": False,
                    "error": "memory_service_unavailable",
                    "detail": type(exc).__name__,
                }
            )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        normalized_query = str(query or "").strip()
        if not self._initialized or not normalized_query:
            return ""
        try:
            result = self._request_json(
                "POST",
                "/recall",
                {
                    "query": normalized_query,
                    "limit": self._prefetch_limit,
                    "max_context_chars": self._prefetch_max_context_chars,
                    "current_session_id": session_id or self._session_id,
                    "request_source": "auto_prefetch",
                    **self._scope_payload(),
                },
            )
        except Exception as exc:
            logger.warning("Memory Service prefetch unavailable: %s", exc)
            return (
                "No recalled evidence is available for this turn. Do not assume "
                "that prior decisions, preferences, or events were recalled. "
                + _RECALL_UNCERTAINTY_GUIDANCE
            )

        context = str(result.get("context") or "").strip()
        query_plan = result.get("query_plan") or {}
        identity_recall = (
            isinstance(query_plan, dict)
            and str(query_plan.get("intent") or "") == "identity"
        )
        if not context:
            parts = [
                "No recalled evidence matched this turn.",
                _RECALL_UNCERTAINTY_GUIDANCE,
            ]
            if identity_recall:
                parts.append(_IDENTITY_RECALL_GUIDANCE)
            return "\n".join(parts)
        parts: list[str] = []
        if identity_recall:
            parts.append(_IDENTITY_RECALL_GUIDANCE)
        parts.append(context)
        return "\n".join(parts)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        tags: List[str] | None = None,
    ) -> EffectOutcome:
        if not self._initialized:
            return EffectOutcome(
                status="skipped",
                details={"reason": "not_initialized"},
            )
        if not self._auto_sync:
            return EffectOutcome(
                status="skipped",
                details={"reason": "auto_sync_disabled"},
            )
        resolved_session_id = str(session_id or self._session_id).strip()
        if not resolved_session_id:
            return EffectOutcome(
                status="failed",
                error="Memory sync requires a session ID",
            )
        write_id = str(uuid.uuid4())
        if self._outbox is None:
            logger.warning("Memory outbox is unavailable; completed turn was not queued")
            return EffectOutcome(
                status="failed",
                error="Memory outbox is unavailable",
            )
        try:
            user_text = str(user_content or "")
            assistant_text = str(assistant_content or "")
            if self._redact_before_store:
                user_text = redact_sensitive_text(user_text)
                assistant_text = redact_sensitive_text(assistant_text)
            self._outbox.enqueue(
                {
                    "session_id": resolved_session_id,
                    "user_content": user_text,
                    "assistant_content": assistant_text,
                    "tags": list(tags or []),
                    "write_id": write_id,
                    **self._scope_payload(),
                }
            )
        except Exception as exc:
            logger.warning("Memory outbox enqueue failed: %s", exc)
            return failed_effect(exc)
        self._sync_wake.set()
        return EffectOutcome(
            status="queued",
            details={
                "write_id": write_id,
                "durable_outbox": True,
            },
        )

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Queue session closure after all earlier durable Turn writes."""
        del messages
        if not self._initialized or self._outbox is None or not self._session_id:
            return
        self._outbox.enqueue(
            {
                "write_id": f"session-close:{self._session_id}",
                "operation": "close_session",
                "session_id": self._session_id,
                **self._scope_payload(),
            }
        )
        self._sync_wake.set()

    def shutdown(self) -> None:
        if not self._initialized:
            return
        self._initialized = False
        thread = self._sync_thread
        if thread is not None:
            deadline = (
                time.monotonic() + self._outbox_shutdown_drain_timeout_seconds
            )
            try:
                self._sync_wake.set()
                while (
                    thread.is_alive()
                    and self._outbox is not None
                    and self._outbox.drainable_count() > 0
                ):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    thread.join(timeout=min(0.05, remaining))
                    self._sync_wake.set()
            except Exception as exc:
                logger.warning("Memory outbox shutdown drain failed: %s", exc)
            finally:
                self._sync_stop.set()
                self._sync_wake.set()
            remaining = deadline - time.monotonic()
            if remaining > 0:
                thread.join(timeout=remaining)
            if thread.is_alive():
                remaining_writes = (
                    self._outbox.pending_count() if self._outbox is not None else 0
                )
                logger.warning(
                    "Memory outbox shutdown drain timed out with %s durable writes remaining",
                    remaining_writes,
                )
            else:
                self._sync_thread = None

    def outbox_status(self) -> dict[str, Any]:
        """Return durable write backlog and retry state for local monitoring."""
        if self._outbox is None:
            return {
                "pending_count": 0,
                "inflight_count": 0,
                "dead_letter_count": 0,
                "oldest_pending_at": None,
                "oldest_failure_at": None,
                "last_success_at": None,
                "last_error": None,
                "max_attempts": self._outbox_settings.max_attempts,
            }
        return self._outbox.health_snapshot()

    def _background_sync(self) -> None:
        while not self._sync_stop.is_set():
            item = self._outbox.next_due() if self._outbox is not None else None
            if item is None:
                self._report_outbox_health_if_due()
                self._sync_wake.wait(timeout=1.0)
                self._sync_wake.clear()
                continue
            try:
                write_id = str(item["write_id"])
                if (
                    item.get("operation") == "close_session"
                    and self._outbox is not None
                    and self._outbox.has_blocking_writes_before(write_id)
                ):
                    self._outbox.defer(write_id)
                    continue
                self._deliver_outbox_item(item)
                if self._outbox is not None:
                    self._outbox.mark_delivered(write_id)
            except Exception as exc:
                logger.warning("Memory Service outbox delivery failed: %s", exc)
                if self._outbox is not None:
                    attempts = int(item.get("_outbox_attempts") or 0) + 1
                    self._outbox.mark_failed(
                        str(item["write_id"]),
                        attempts=attempts,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            self._report_outbox_health_if_due(force=True)

    def _deliver_outbox_item(self, item: dict[str, Any]) -> None:
        if item.get("operation") == "close_session":
            self._write_session_close(str(item.get("session_id") or ""))
            return
        self._write_turn_pair(item)

    def _report_outbox_health_if_due(self, *, force: bool = False) -> None:
        if self._outbox is None or not self._session_id:
            return
        now = time.monotonic()
        if (
            not force
            and now - self._last_outbox_health_report_at
            < self._outbox_settings.health_report_interval_seconds
        ):
            return
        self._last_outbox_health_report_at = now
        try:
            self._request_json(
                "POST",
                "/outbox/health",
                {
                    "session_id": self._session_id,
                    **build_outbox_health_report(
                        self._outbox,
                        queue_name="api_a",
                        session_id=self._session_id,
                        memory_actor="api_a",
                        memory_domain="agent_interaction",
                    ),
                },
            )
        except Exception as exc:
            logger.debug("Memory outbox health report failed: %s", exc)

    def _write_turn_pair(self, item: dict[str, Any]) -> None:
        self._request_json(
            "POST",
            "/turn-pairs",
            {
                key: value
                for key, value in item.items()
                if not key.startswith("_outbox_")
            },
        )

    def _write_session_close(self, session_id: str) -> None:
        resolved_session_id = str(session_id or "").strip()
        if not resolved_session_id:
            raise ValueError("Session close requires a session ID")
        self._request_json(
            "POST",
            f"/sessions/{quote(resolved_session_id, safe='')}/close",
            self._scope_payload(),
            identity_session_id=resolved_session_id,
        )

    def _scope_payload(self) -> dict[str, str]:
        return {
            "owner_id": self._owner_id,
            "workspace_id": self._workspace_id,
            "memory_domain": "agent_interaction",
        }

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        identity_session_id: str | None = None,
    ) -> dict[str, Any]:
        request_session_id = str(
            identity_session_id
            or (payload or {}).get("session_id")
            or self._session_id
            or ""
        ).strip()
        if self._memory_client is None:
            if self._async_memory_client is not None:
                return asyncio.run(
                    self._async_memory_client.request_json(
                        method,
                        path,
                        payload,
                        identity_session_id=request_session_id or None,
                        idempotency_key=(payload or {}).get("write_id"),
                    )
                )
            raise ConnectionError("Memory Service client is unavailable")
        return self._memory_client.request_json(
            method,
            path,
            payload,
            identity_session_id=request_session_id or None,
            idempotency_key=(payload or {}).get("write_id"),
        )
