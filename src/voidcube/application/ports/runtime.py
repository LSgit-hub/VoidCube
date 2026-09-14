"""Protocols used by application orchestration boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from ...domain.agent.effect_outcomes import EffectOutcome
from ...domain.agent.effect_outcomes import failed_effect, require_effect_outcome


class MemoryPort(Protocol):
    def bind_session(self, session_id: str) -> None: ...

    def build_system_prompt(self) -> str: ...

    def prefetch(self, query: str, *, session_id: str = "") -> str: ...

    def sync_turn(
        self, user_content: Any, assistant_content: str, *, session_id: str = "",
        tags: list[str] | None = None,
    ) -> EffectOutcome: ...

    def get_all_tool_schemas(self) -> list[dict[str, Any]]: ...

    def has_tool(self, tool_name: str) -> bool: ...

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str: ...

    def on_session_end(self, messages: list[dict[str, Any]]) -> EffectOutcome: ...

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> EffectOutcome: ...

    def on_delegation(
        self, task: str, result: str, *, child_session_id: str = "",
    ) -> EffectOutcome: ...

    def shutdown(self) -> EffectOutcome: ...


class PersistencePort(Protocol):
    def persist(
        self, messages: Sequence[Mapping[str, Any]],
        conversation_history: Sequence[Mapping[str, Any]] | None = None,
    ) -> EffectOutcome: ...


class TaskPort(Protocol):
    def submit(self, task: str, *, session_id: str = "") -> EffectOutcome: ...


class GovernancePort(Protocol):
    def record_decision(self, decision: Any) -> EffectOutcome: ...


class ContextPort(Protocol):
    def compress(
        self, messages: list[dict[str, Any]], system_message: str | None,
        *, approx_tokens: int | None = None, task_id: str = "default",
        focus_topic: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]: ...


class EventPort(Protocol):
    def emit(self, event: Any) -> EffectOutcome: ...


class CallbackEventPort:
    """Adapt a legacy event callback to the structured event port."""

    def __init__(self, callback: Callable[[Any], Any] | None) -> None:
        self._callback = callback

    def emit(self, event: Any) -> EffectOutcome:
        if self._callback is None:
            return EffectOutcome(status="skipped", details={"reason": "no_sink"})
        try:
            result = self._callback(event)
            # Legacy callbacks return ``None``; structured publishers may
            # return their own outcome so projection degradation reaches the
            # caller instead of being hidden by this compatibility adapter.
            if isinstance(result, EffectOutcome):
                return result
        except Exception as exc:
            return failed_effect(exc)
        return EffectOutcome(status="succeeded")


class CallbackPersistencePort:
    """Adapt a persistence callback while normalizing legacy return values."""

    def __init__(self, callback: Callable[..., Any] | None) -> None:
        self._callback = callback

    def persist(self, messages: Sequence[Mapping[str, Any]], conversation_history=None) -> EffectOutcome:
        if self._callback is None:
            return EffectOutcome(status="skipped", details={"reason": "no_sink"})
        try:
            result = self._callback(messages, conversation_history)
            return require_effect_outcome(result, effect="persistence callback")
        except Exception as exc:
            return failed_effect(exc)


class CallbackTaskPort:
    """Normalize a legacy task callback at the application boundary."""
    def __init__(self, callback: Callable[..., Any] | None) -> None:
        self._callback = callback

    def submit(self, task: str, *, session_id: str = "") -> EffectOutcome:
        if self._callback is None:
            return EffectOutcome(status="skipped", details={"reason": "no_sink"})
        try:
            return require_effect_outcome(self._callback(task, session_id=session_id), effect="task callback")
        except Exception as exc:
            return failed_effect(exc)


class CallbackGovernancePort:
    """Normalize governance persistence callbacks without leaking exceptions."""
    def __init__(self, callback: Callable[[Any], Any] | None) -> None:
        self._callback = callback

    def record_decision(self, decision: Any) -> EffectOutcome:
        if self._callback is None:
            return EffectOutcome(status="skipped", details={"reason": "no_sink"})
        try:
            return require_effect_outcome(self._callback(decision), effect="governance callback")
        except Exception as exc:
            return failed_effect(exc)


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    """Optional capabilities passed into a turn orchestrator.

    Keeping this object immutable makes dependency wiring explicit and avoids
    runtime services reaching through agent internals via ``getattr``.
    """

    memory: MemoryPort | None = None
    persistence: PersistencePort | None = None
    task: TaskPort | None = None
    governance: GovernancePort | None = None
    events: EventPort | None = None
    context: ContextPort | None = None


__all__ = [
    "EventPort",
    "ContextPort",
    "CallbackEventPort",
    "CallbackPersistencePort",
    "MemoryPort",
    "PersistencePort",
    "RuntimePorts",
    "TaskPort",
    "GovernancePort",
    "CallbackTaskPort",
    "CallbackGovernancePort",
]
