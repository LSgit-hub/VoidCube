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


class ToolPort(Protocol):
    """Stable boundary for invoking a named tool from orchestration code."""

    def invoke(
        self, tool_name: str, arguments: Mapping[str, Any], *, tool_call_id: str = ""
    ) -> str: ...


class GovernancePort(Protocol):
    def record_decision(self, decision: Any) -> EffectOutcome: ...


class ContextPort(Protocol):
    def compress(
        self, messages: list[dict[str, Any]], system_message: str | None,
        *, approx_tokens: int | None = None, task_id: str = "default",
        focus_topic: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]: ...

    def compress_until_below_threshold(
        self,
        messages: list[dict[str, Any]],
        system_message: str | None,
        *,
        token_estimator: Callable[[list[dict[str, Any]], str], int],
        threshold_tokens: int,
        protect_first_n: int = 0,
        protect_last_n: int = 0,
        task_id: str = "default",
        focus_topic: str | None = None,
        max_passes: int = 3,
    ) -> tuple[list[dict[str, Any]], str, bool]: ...


class EventPort(Protocol):
    def emit(self, event: Any) -> EffectOutcome: ...


class TurnEventJournalPort(Protocol):
    """Durable turn event storage required by application orchestration."""

    def append(self, event: Any) -> int: ...

    def recover_active_turn(self, session_id: str) -> Mapping[str, Any] | None: ...

    def session_summary(self, session_id: str) -> Mapping[str, Any]: ...


class ApprovalJournalPort(Protocol):
    """Durable approval storage required by application orchestration."""

    def request(self, *, session_id: str, turn_id: str, command: str, description: str) -> str: ...

    def resolve(self, request_id: str, status: str, reason: str = "") -> bool: ...

    def pending(self, session_id: str) -> list[Mapping[str, Any]]: ...


class SessionGoalPort(Protocol):
    """Session goal operations exposed to tool adapters."""

    def get_goal(self, host: Any) -> Mapping[str, Any] | None: ...

    def create_goal(self, host: Any, objective: str) -> Mapping[str, Any]: ...

    def update_goal(self, host: Any, status: str, reason: str | None = None) -> bool: ...

    def clear_goal(self, host: Any) -> bool: ...

    def audit_blocked_goal(self, host: Any, reason: str, *, turn_id: str) -> Mapping[str, Any] | None: ...

    def update_goal_objective(self, host: Any, objective: str, *, reason: str | None = None) -> bool: ...

    def bind_goal_backend(self, host: Any, objective: str) -> Mapping[str, Any] | None: ...

    def backend_status(self, host: Any, goal: Mapping[str, Any]) -> Mapping[str, Any] | None: ...

    def complete_backend(self, host: Any, reason: str | None) -> bool: ...

    def goal_update_error(self, host: Any) -> str | None: ...

    def resolve_goal_memory_context(self, host: Any, goal: Mapping[str, Any] | None) -> str: ...

    def goal_prompt(self, goal: Mapping[str, Any] | None) -> str: ...

    def stop_goal_after_turn(self, host: Any, reason: str) -> bool: ...

    def finish_goal_audit_turn(self, host: Any, turn_id: str) -> None: ...


class DefaultSessionGoalPort:
    """Bind application session-goal use cases for runtime composition."""

    def get_goal(self, host: Any) -> Mapping[str, Any] | None:
        from ..session_goal import get_goal
        return get_goal(host)

    def create_goal(self, host: Any, objective: str) -> Mapping[str, Any]:
        from ..session_goal import create_goal
        return create_goal(host, objective)

    def update_goal(self, host: Any, status: str, reason: str | None = None) -> bool:
        from ..session_goal import update_goal
        return update_goal(host, status, reason)

    def clear_goal(self, host: Any) -> bool:
        from ..session_goal import clear_goal
        return clear_goal(host)

    def audit_blocked_goal(self, host: Any, reason: str, *, turn_id: str) -> Mapping[str, Any] | None:
        from ..session_goal import audit_blocked_goal
        return audit_blocked_goal(host, reason, turn_id=turn_id)

    def update_goal_objective(self, host: Any, objective: str, *, reason: str | None = None) -> bool:
        from ..session_goal import update_goal_objective
        return update_goal_objective(host, objective, reason=reason)

    def bind_goal_backend(self, host: Any, objective: str) -> Mapping[str, Any] | None:
        from ..session_goal import bind_goal_backend
        return bind_goal_backend(host, objective)

    def backend_status(self, host: Any, goal: Mapping[str, Any]) -> Mapping[str, Any] | None:
        from ..session_goal import backend_status
        return backend_status(host, goal)

    def complete_backend(self, host: Any, reason: str | None) -> bool:
        from ..session_goal import complete_goal_backend
        return complete_goal_backend(host, reason)

    def goal_update_error(self, host: Any) -> str | None:
        from ..session_goal import goal_update_error
        return goal_update_error(host)

    def resolve_goal_memory_context(self, host: Any, goal: Mapping[str, Any] | None) -> str:
        from ..session_goal import resolve_goal_memory_context
        return resolve_goal_memory_context(host, goal)

    def goal_prompt(self, goal: Mapping[str, Any] | None) -> str:
        from ..session_goal import goal_prompt
        return goal_prompt(goal)

    def stop_goal_after_turn(self, host: Any, reason: str) -> bool:
        from ..session_goal import stop_goal_after_turn
        return stop_goal_after_turn(host, reason)

    def finish_goal_audit_turn(self, host: Any, turn_id: str) -> None:
        from ..session_goal import finish_goal_audit_turn
        return finish_goal_audit_turn(host, turn_id)


class GoalManagerPort(Protocol):
    """Goal Manager service operations used by session goal orchestration."""

    def health(self) -> bool: ...

    def create_session_project(self, objective: str, session_id: str) -> Mapping[str, Any]: ...

    def complete_node(self, node_id: str, reason: str, *, session_id: str | None = None) -> Mapping[str, Any]: ...

    def project(self, project_id: str) -> Mapping[str, Any]: ...

    def update_node_status(
        self, node_id: str, expected_version: int, status: str, reason: str,
        *, session_id: str | None = None,
    ) -> Mapping[str, Any]: ...

    def context(self, node_id: str) -> Mapping[str, Any]: ...


class UnavailableGoalManagerPort:
    """Fail-closed default when the optional Goal Manager is not composed."""

    def health(self) -> bool:
        return False

    def _unavailable(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Goal Manager backend is unavailable")

    create_session_project = _unavailable
    complete_node = _unavailable
    project = _unavailable
    update_node_status = _unavailable
    context = _unavailable


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


class CallbackToolPort:
    """Adapt a tool router while keeping invocation details behind a port."""

    def __init__(self, callback: Callable[..., Any] | None) -> None:
        self._callback = callback

    def invoke(
        self, tool_name: str, arguments: Mapping[str, Any], *, tool_call_id: str = ""
    ) -> str:
        if self._callback is None:
            return "tool_unavailable"
        try:
            result = self._callback(
                str(tool_name), dict(arguments), tool_call_id=str(tool_call_id or "")
            )
            return result if isinstance(result, str) else str(result)
        except Exception as exc:
            return f"tool_failed: {type(exc).__name__}: {exc}"


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
    tool: ToolPort | None = None


__all__ = [
    "EventPort",
    "ContextPort",
    "CallbackEventPort",
    "CallbackPersistencePort",
    "MemoryPort",
    "PersistencePort",
    "RuntimePorts",
    "TaskPort",
    "ToolPort",
    "GovernancePort",
    "CallbackTaskPort",
    "CallbackToolPort",
    "CallbackGovernancePort",
    "TurnEventJournalPort",
    "ApprovalJournalPort",
    "SessionGoalPort",
    "GoalManagerPort",
    "UnavailableGoalManagerPort",
]
