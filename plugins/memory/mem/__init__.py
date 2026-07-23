"""Agent-side client for the canonical VoidCube Memory Service."""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from typing import Any, Dict, List
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from agent.memory_provider import MemoryProvider


logger = logging.getLogger(__name__)


class MemMemoryProvider(MemoryProvider):
    """Expose Memory Service recall and queue completed turns for Tier 1."""

    @property
    def name(self) -> str:
        return "mem"

    def __init__(self) -> None:
        self._initialized = False
        self._session_id = ""
        self._gateway_url = "http://127.0.0.1:6000"
        self._request_timeout_seconds = 2.0
        self._auto_sync = True
        self._prefetch_limit = 5
        self._prefetch_max_context_chars = 3500
        self._sync_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._sync_thread: threading.Thread | None = None

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self._session_id = str(session_id or "").strip()
        try:
            from VoidCube_cli.config import load_config

            provider_config = dict(
                load_config().get("memory", {}).get("mem", {}) or {}
            )
        except Exception:
            provider_config = {}

        configured_gateway = str(
            provider_config.get("gateway_address") or ""
        ).strip()
        if not configured_gateway:
            from systems.config import load_config_from_env

            configured_gateway = load_config_from_env().agent.gateway_address
        self._gateway_url = configured_gateway.rstrip("/")
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
        self._initialized = True
        if self._auto_sync:
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
            "for an exact dated chronology. Recalled items include stable IDs, "
            "scores, matched concepts, and evidence references; use those fields "
            "when explaining why a memory was recalled. Use mem_remember for an "
            "explicit durable fact, preference, decision, or verified outcome. "
            "Treat empty or unavailable results as an explicit lack of recalled "
            "evidence."
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
                            "enum": ["event", "scene", "arc", "epoch"],
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
                    "milestones, or verified outcomes; include evidence references."
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
                    },
                    "required": ["title", "summary", "evidence_refs"],
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
                result = self._request_json("POST", "/recall", payload)
            elif tool_name == "mem_timeline":
                params = {
                    "date": args.get("date"),
                    "session_id": args.get("session_id") or self._session_id,
                    "speaker": args.get("speaker"),
                    "limit": args.get("limit", 100),
                }
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
                        "event_kind",
                        "importance",
                    )
                    if args.get(key) not in (None, "")
                }
                evidence_refs = list(payload.get("evidence_refs") or [])
                if self._session_id:
                    evidence_refs.append(f"session:{self._session_id}")
                payload["evidence_refs"] = list(dict.fromkeys(evidence_refs))
                payload["source_actor"] = "agent"
                result = self._request_json("POST", "/remember", payload)
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
                },
            )
        except Exception as exc:
            logger.warning("Memory Service prefetch unavailable: %s", exc)
            return (
                "Memory recall status: unavailable for this turn "
                f"(error={type(exc).__name__}). Do not assume that prior "
                "decisions, preferences, or events were recalled."
            )

        trace_id = str(result.get("trace_id") or "unknown")
        status = str(result.get("recall_status") or "empty")
        context = str(result.get("context") or "").strip()
        status_line = f"Memory recall status: {status} (trace_id={trace_id})."
        if not context:
            return status_line + " No recalled evidence matched this turn."
        return status_line + "\n" + context

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
    ) -> None:
        if not self._initialized or not self._auto_sync:
            return
        resolved_session_id = str(session_id or self._session_id).strip()
        if not resolved_session_id:
            return
        write_id = str(uuid.uuid4())
        self._sync_queue.put(
            {
                "session_id": resolved_session_id,
                "user_content": str(user_content or ""),
                "assistant_content": str(assistant_content or ""),
                "write_id": write_id,
            }
        )

    def shutdown(self) -> None:
        if not self._initialized:
            return
        self._initialized = False
        if self._sync_thread is not None:
            self._sync_queue.put(None)
            self._sync_thread.join(timeout=max(2.0, self._request_timeout_seconds * 3))
            self._sync_thread = None

    def _background_sync(self) -> None:
        while True:
            item = self._sync_queue.get()
            try:
                if item is None:
                    return
                self._write_turn_pair(item)
            except Exception as exc:
                logger.warning("Memory Service turn sync failed: %s", exc)
            finally:
                self._sync_queue.task_done()

    def _write_turn_pair(self, item: dict[str, Any]) -> None:
        session_id = str(item["session_id"])
        encoded_session = quote(session_id, safe="")
        self._request_json(
            "POST",
            "/sessions",
            {
                "session_id": session_id,
                "metadata": {"source": "agent_memory_provider"},
            },
        )
        turn_ids: dict[str, str] = {}
        for speaker, content in (
            ("user", item.get("user_content")),
            ("agent", item.get("assistant_content")),
        ):
            text = str(content or "").strip()
            if not text:
                continue
            response = self._request_json(
                "POST",
                f"/sessions/{encoded_session}/turns",
                {
                    "speaker": speaker,
                    "text": text,
                    "metadata": {
                        "source": "agent_memory_provider",
                        "turn_dedup_key": f"{item['write_id']}:{speaker}",
                    },
                },
            )
            turn_id = str(response.get("turn_id") or "").strip()
            if turn_id:
                turn_ids[speaker] = turn_id
        if turn_ids.get("user"):
            self._request_json(
                "POST",
                "/identity/experiences/settle-interaction",
                {
                    "user_turn_id": turn_ids["user"],
                    "agent_turn_id": turn_ids.get("agent"),
                    "verified_by": "user_explicit_signal",
                },
            )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._gateway_url}/api/mem{path}"
        data = None
        headers = {"Accept": "application/json"}
        if method.upper() == "GET" and payload:
            url += "?" + urlencode(payload)
        elif payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method.upper())
        with urlopen(request, timeout=self._request_timeout_seconds) as response:
            body = response.read().decode("utf-8")
        parsed = json.loads(body or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("Memory Service returned a non-object response")
        return parsed
