"""Turn-scoped output, persistence, cleanup, and terminal result coordination."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


Message = dict[str, Any]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class ConversationTurnPorts:
    """External effects used while one conversation turn is running."""

    persist_session: Callable[
        [list[Message], Sequence[Mapping[str, Any]] | None], None
    ]
    save_session_log: Callable[[list[Message]], None]
    cleanup_task_resources: Callable[[str], None]
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
    ) -> None:
        self._ports.persist_session(messages, conversation_history)

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
        if cleanup_task_id is not None:
            self._ports.cleanup_task_resources(cleanup_task_id)
        if persist:
            self.persist(messages, conversation_history)
        if clear_interrupt:
            self._ports.clear_interrupt()

        result: dict[str, Any] = {
            "messages": messages,
            "completed": False,
            "api_calls": api_call_count,
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
