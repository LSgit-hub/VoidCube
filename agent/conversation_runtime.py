"""Turn-scoped output, persistence, cleanup, and terminal result coordination."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.effect_outcomes import (
    EffectOutcome,
    failed_effect,
    finalization_status,
    require_effect_outcome,
)

Message = dict[str, Any]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class ConversationTurnPorts:
    """External effects used while one conversation turn is running."""

    persist_session: Callable[
        [list[Message], Sequence[Mapping[str, Any]] | None], EffectOutcome
    ]
    save_session_log: Callable[[list[Message]], None]
    cleanup_task_resources: Callable[[str], EffectOutcome]
    clear_interrupt: Callable[[], None]
    emit_status: Callable[[str], None]
    emit_verbose: Callable[[str, bool], None]


class ConversationTurnRuntime:
    """Own intermediate turn effects and canonical early-exit payloads."""

    def __init__(self, ports: ConversationTurnPorts) -> None:
        self._ports = ports

    def persist(
        self,
        messages: list[Message],
        conversation_history: Sequence[Mapping[str, Any]] | None = None,
    ) -> EffectOutcome:
        return self._ports.persist_session(messages, conversation_history)

    def save_progress(self, messages: list[Message]) -> None:
        self._ports.save_session_log(messages)

    def status(self, message: str) -> None:
        self._ports.emit_status(message)

    def verbose(self, message: str, *, force: bool = False) -> None:
        self._ports.emit_verbose(message, force)

    def terminate(
        self,
        *,
        messages: list[Message],
        conversation_history: Sequence[Mapping[str, Any]] | None,
        api_call_count: int,
        final_response: str | None | object = _MISSING,
        error: str | None = None,
        partial: bool = False,
        failed: bool = False,
        interrupted: bool = False,
        cleanup_task_id: str | None = None,
        persist: bool = True,
        clear_interrupt: bool = False,
    ) -> dict[str, Any]:
        """Apply ordered terminal effects and build one stable result mapping."""
        cleanup_outcome = EffectOutcome(
            status="skipped",
            details={"reason": "not_requested"},
        )
        if cleanup_task_id is not None:
            try:
                cleanup_outcome = require_effect_outcome(
                    self._ports.cleanup_task_resources(cleanup_task_id),
                    effect="cleanup_task_resources",
                )
            except Exception as exc:
                cleanup_outcome = failed_effect(exc)

        persistence_outcome = EffectOutcome(
            status="skipped",
            details={"reason": "not_requested"},
        )
        if persist:
            try:
                persistence_outcome = require_effect_outcome(
                    self.persist(messages, conversation_history),
                    effect="persist_session",
                )
            except Exception as exc:
                persistence_outcome = failed_effect(exc)

        interrupt_cleanup_outcome = EffectOutcome(
            status="skipped",
            details={"reason": "not_requested"},
        )
        if clear_interrupt:
            try:
                self._ports.clear_interrupt()
                interrupt_cleanup_outcome = EffectOutcome(status="succeeded")
            except Exception as exc:
                interrupt_cleanup_outcome = failed_effect(exc)

        preview_cleanup_outcome = EffectOutcome(
            status="skipped",
            details={"reason": "not_available_in_early_exit"},
        )
        stream_cleanup_outcome = EffectOutcome(
            status="skipped",
            details={"reason": "not_available_in_early_exit"},
        )
        memory_outcome = EffectOutcome(
            status="skipped",
            details={"reason": "not_applicable"},
        )
        cleanup_status = finalization_status(
            cleanup_outcome,
            preview_cleanup_outcome,
            interrupt_cleanup_outcome,
            stream_cleanup_outcome,
        )

        result: dict[str, Any] = {
            "messages": messages,
            "completed": False,
            "api_calls": api_call_count,
            "finalization": {
                "status": finalization_status(
                    cleanup_outcome,
                    persistence_outcome,
                    preview_cleanup_outcome,
                    interrupt_cleanup_outcome,
                    stream_cleanup_outcome,
                    memory_outcome,
                ),
                "cleanup": {
                    "status": cleanup_status,
                    "task_resources": cleanup_outcome.as_dict(),
                    "response_preview": preview_cleanup_outcome.as_dict(),
                    "interrupt": interrupt_cleanup_outcome.as_dict(),
                    "stream_callback": stream_cleanup_outcome.as_dict(),
                },
                "persistence": persistence_outcome.as_dict(),
                "memory_sync": memory_outcome.as_dict(),
            },
        }
        if final_response is not _MISSING:
            result["final_response"] = final_response
        if partial:
            result["partial"] = True
        if failed:
            result["failed"] = True
        if interrupted:
            result["interrupted"] = True
        if error is not None:
            result["error"] = error
        return result

    def partial_failure(
        self,
        *,
        messages: list[Message],
        conversation_history: Sequence[Mapping[str, Any]] | None,
        api_call_count: int,
        error: str,
        final_response: str | None | object = _MISSING,
        cleanup_task_id: str | None = None,
    ) -> dict[str, Any]:
        return self.terminate(
            messages=messages,
            conversation_history=conversation_history,
            api_call_count=api_call_count,
            final_response=final_response,
            error=error,
            partial=True,
            cleanup_task_id=cleanup_task_id,
        )

    def interrupted_result(
        self,
        *,
        messages: list[Message],
        conversation_history: Sequence[Mapping[str, Any]] | None,
        api_call_count: int,
        final_response: str,
    ) -> dict[str, Any]:
        return self.terminate(
            messages=messages,
            conversation_history=conversation_history,
            api_call_count=api_call_count,
            final_response=final_response,
            interrupted=True,
            clear_interrupt=True,
        )
