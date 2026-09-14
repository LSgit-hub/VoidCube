"""Application-facing adapter and lifecycle owner for the canonical Mem provider."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable
import json
import logging
from typing import Any

from ...application.memory_context import infer_sync_tags
from ...domain.agent.effect_outcomes import EffectOutcome, failed_effect, require_effect_outcome
from ...domain.contracts.memory import MemoryProvider

logger = logging.getLogger(__name__)


class MemoryBridge:
    """Expose one provider through MemoryPort without leaking its implementation.

    Construction captures the tool surface once, so routing and advertised
    schemas cannot diverge. The runtime factory initializes the provider before
    publishing the bridge; failed initialization never creates an active port.
    """

    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider
        self._schemas: dict[str, dict[str, Any]] = {}
        for schema in provider.get_tool_schemas():
            name = schema.get("name", "")
            if not name or name in self._schemas:
                raise ValueError(f"Invalid or duplicate memory tool name: {name!r}")
            self._schemas[name] = deepcopy(schema)

    def bind_session(self, session_id: str) -> None:
        self._provider.bind_session(session_id)

    def build_system_prompt(self) -> str:
        try:
            return self._provider.system_prompt_block()
        except Exception:
            logger.warning("Memory system prompt unavailable", exc_info=True)
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            return self._provider.prefetch(query, session_id=session_id)
        except Exception:
            logger.warning("Memory recall unavailable", exc_info=True)
            return ""

    def _effect(
        self, operation: str, callback: Callable[..., EffectOutcome],
        *args: Any, **kwargs: Any,
    ) -> EffectOutcome:
        try:
            outcome = require_effect_outcome(
                callback(*args, **kwargs),
                effect=f"memory provider '{self._provider.name}' {operation}",
            )
        except Exception as exc:
            logger.warning("Memory %s failed: %s", operation, exc)
            outcome = failed_effect(exc)
        return EffectOutcome(
            status=outcome.status,
            error=outcome.error,
            details={**dict(outcome.details), "provider": self._provider.name},
        )

    def sync_turn(
        self, user_content: Any, assistant_content: str, *, session_id: str = "",
        tags: list[str] | None = None,
    ) -> EffectOutcome:
        user_text = str(user_content or "")
        return self._effect(
            "sync_turn", self._provider.sync_turn, user_text, str(assistant_content or ""),
            session_id=session_id, tags=infer_sync_tags(user_text, tags),
        )

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        return deepcopy(list(self._schemas.values()))

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._schemas

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if not self.has_tool(tool_name):
            return json.dumps({"success": False, "error": f"Unknown memory tool: {tool_name}"})
        try:
            return self._provider.handle_tool_call(tool_name, args, **kwargs)
        except Exception as exc:
            logger.warning("Memory tool %s failed: %s", tool_name, exc)
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> EffectOutcome:
        return self._effect("on_pre_compress", self._provider.on_pre_compress, messages)

    def on_session_end(self, messages: list[dict[str, Any]]) -> EffectOutcome:
        return self._effect("on_session_end", self._provider.on_session_end, messages)

    def on_delegation(
        self, task: str, result: str, *, child_session_id: str = "",
    ) -> EffectOutcome:
        return self._effect(
            "on_delegation", self._provider.on_delegation, task, result,
            child_session_id=child_session_id,
        )

    def shutdown(self) -> EffectOutcome:
        return self._effect("shutdown", self._provider.shutdown)


def create_memory_bridge(
    *, session_id: str, platform: str = "cli", user_id: str = "",
) -> MemoryBridge:
    """Resolve host settings and publish only a successfully initialized adapter."""
    from memai.domain.scope import CLI_WORKSPACE_ID
    from plugins.memory.mem import MemMemoryProvider

    from ..config.profiles import get_active_profile_name
    from ..config.runtime_paths import get_VoidCube_home

    settings: dict[str, Any] = {
        "session_id": session_id,
        "platform": platform or "cli",
        "VoidCube_home": str(get_VoidCube_home()),
        "agent_context": "primary",
        "agent_workspace": CLI_WORKSPACE_ID,
    }
    if user_id:
        settings["user_id"] = user_id
    settings["agent_identity"] = get_active_profile_name()
    provider = MemMemoryProvider()
    try:
        provider.initialize(**settings)
        return MemoryBridge(provider)
    except Exception:
        try:
            provider.shutdown()
        except Exception:
            logger.warning("Memory initialization cleanup failed", exc_info=True)
        raise
