"""Agent-side client for the canonical VoidCube Memory Service."""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from datetime import date
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
            "Use mem_search for relevant structured long-term memory and "
            "mem_timeline for dated Tier 1 conversation history. Treat empty "
            "or unavailable results as an explicit lack of recalled evidence."
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "mem_search",
                "description": "Search canonical structured long-term memory.",
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
                    },
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
                    )
                    if args.get(key) not in (None, "")
                }
                result = self._request_json("POST", "/compressed/search", payload)
            elif tool_name == "mem_timeline":
                params = {
                    "date": args.get("date"),
                    "session_id": args.get("session_id") or self._session_id,
                    "speaker": args.get("speaker"),
                    "limit": args.get("limit", 100),
                }
                result = self._request_json("POST", "/turns/timeline", params)
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
        del session_id
        normalized_query = str(query or "").strip()
        if not self._initialized or not normalized_query:
            return ""
        try:
            result = self._request_json(
                "POST",
                "/compressed/search",
                {"query": normalized_query, "limit": 5},
            )
        except Exception as exc:
            logger.debug("Memory Service prefetch unavailable: %s", exc)
            return ""

        rows = list(result.get("results") or [])
        lines: list[str] = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or row.get("memory_type") or "Memory").strip()
            summary = str(row.get("summary") or "").strip()
            if summary:
                lines.append(f"- {title}: {summary}")
        return "Relevant structured memory:\n" + "\n".join(lines) if lines else ""

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
        for speaker, content in (
            ("user", item.get("user_content")),
            ("agent", item.get("assistant_content")),
        ):
            text = str(content or "").strip()
            if not text:
                continue
            self._request_json(
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
