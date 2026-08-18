"""Successful tool-turn sequencing and context-pressure ownership."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ...domain.agent.response import has_visible_content, strip_thinking_blocks
from ...domain.agent.conversation_runtime import ConversationTurnRuntime
from ...domain.agent.conversation_turn import ConversationTurnState
from ...infrastructure.providers.model_metadata import estimate_messages_tokens_rough


HOUSEKEEPING_TOOLS = frozenset(
    {"memory", "todo", "skill_manage", "session_search"}
)


class ContextPressureTracker:
    """Process-wide warning dedup for sessions recreated by the Gateway."""

    def __init__(self, *, cooldown: float = 300.0, clock=time.time) -> None:
        self._cooldown = cooldown
        self._clock = clock
        self._entries: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def next_warning(self, session_id: str, progress: float) -> float:
        tier = 0.95 if progress >= 0.95 else 0.85 if progress >= 0.85 else 0.0
        if tier == 0.0:
            return 0.0
        now = self._clock()
        with self._lock:
            previous = self._entries.get(session_id)
            if (
                previous is not None
                and previous[0] >= tier
                and now - previous[1] < self._cooldown
            ):
                return 0.0
            self._entries[session_id] = (tier, now)
            cutoff = now - self._cooldown * 2
            self._entries = {
                key: value
                for key, value in self._entries.items()
                if value[1] > cutoff
            }
        return tier

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._entries.pop(session_id, None)


context_pressure_tracker = ContextPressureTracker()


class SuccessfulToolTurnPort(Protocol):
    quiet_mode: bool
    stream_delta_callback: Any
    iteration_budget: Any
    context_compressor: Any
    compression_enabled: bool
    session_id: str | None
    _last_content_with_tools: str | None
    _mute_post_response: bool
    _thinking_prefill_retries: int
    _empty_content_retries: int
    _stream_needs_break: bool
    _conversation_turn_runtime: ConversationTurnRuntime

    def _cap_delegate_task_calls(self, tool_calls: list[Any]) -> list[Any]: ...

    def _deduplicate_tool_calls(self, tool_calls: list[Any]) -> list[Any]: ...

    def _build_assistant_message(
        self,
        assistant_message: Any,
        finish_reason: str,
    ) -> dict[str, Any]: ...

    def _has_stream_consumers(self) -> bool: ...

    def _vprint(self, message: str, *, force: bool = False) -> None: ...

    def _emit_interim_assistant_message(
        self,
        assistant_message: dict[str, Any],
    ) -> None: ...

    def _execute_tool_calls(
        self,
        assistant_message: Any,
        messages: list[dict[str, Any]],
        task_id: str,
    ) -> None: ...

    def _emit_context_pressure(self, progress: float, compressor: Any) -> None: ...

    def _safe_print(self, message: str) -> None: ...

    def _compress_context(
        self,
        messages: list[dict[str, Any]],
        system_message: str,
        *,
        approx_tokens: int | None,
        task_id: str,
    ) -> tuple[list[dict[str, Any]], str]: ...


@dataclass(frozen=True, slots=True)
class SuccessfulToolTurnExecution:
    messages: list[dict[str, Any]]
    system_prompt: str
    conversation_history_reset: bool = False


def execute_successful_tool_turn(
    owner: SuccessfulToolTurnPort,
    *,
    state: ConversationTurnState,
    assistant_message: Any,
    finish_reason: str,
    messages: list[dict[str, Any]],
    system_message: str,
    active_system_prompt: str,
    task_id: str,
    pressure_tracker: ContextPressureTracker = context_pressure_tracker,
) -> SuccessfulToolTurnExecution:
    """Apply the canonical successful tool-turn sequence once."""
    assistant_message.tool_calls = owner._cap_delegate_task_calls(
        assistant_message.tool_calls
    )
    assistant_message.tool_calls = owner._deduplicate_tool_calls(
        assistant_message.tool_calls
    )
    assistant_record = owner._build_assistant_message(
        assistant_message,
        finish_reason,
    )

    turn_content = assistant_message.content or ""
    if turn_content and has_visible_content(turn_content):
        owner._last_content_with_tools = turn_content
        all_housekeeping = all(
            tool_call.function.name in HOUSEKEEPING_TOOLS
            for tool_call in assistant_message.tool_calls
        )
        if all_housekeeping and owner._has_stream_consumers():
            owner._mute_post_response = True
        elif owner.quiet_mode:
            clean = strip_thinking_blocks(turn_content).strip()
            if clean:
                owner._vprint(f"  ┊ 💬 {clean}")

    had_prefill = False
    while (
        messages
        and isinstance(messages[-1], dict)
        and messages[-1].get("_thinking_prefill")
    ):
        messages.pop()
        had_prefill = True
    if had_prefill:
        owner._thinking_prefill_retries = 0
        owner._empty_content_retries = 0

    messages.append(assistant_record)
    owner._emit_interim_assistant_message(assistant_record)
    if owner.stream_delta_callback:
        try:
            owner.stream_delta_callback(None)
        except Exception:
            pass

    owner._execute_tool_calls(assistant_message, messages, task_id)
    state.truncated_tool_call_retries = 0
    owner._stream_needs_break = True

    tool_names = {
        tool_call.function.name for tool_call in assistant_message.tool_calls
    }
    if tool_names == {"execute_code"}:
        owner.iteration_budget.refund()

    compressor = owner.context_compressor
    if compressor.last_prompt_tokens > 0:
        real_tokens = (
            compressor.last_prompt_tokens + compressor.last_completion_tokens
        )
    else:
        real_tokens = estimate_messages_tokens_rough(messages)

    if compressor.threshold_tokens > 0:
        progress = real_tokens / compressor.threshold_tokens
        warning_tier = pressure_tracker.next_warning(
            owner.session_id or "default",
            progress,
        )
        if warning_tier:
            owner._emit_context_pressure(progress, compressor)

    history_reset = False
    if owner.compression_enabled and compressor.should_compress(real_tokens):
        owner._safe_print("  ⟳ compacting context…")
        messages, active_system_prompt = owner._compress_context(
            messages,
            system_message,
            approx_tokens=compressor.last_prompt_tokens,
            task_id=task_id,
        )
        history_reset = True

    owner._conversation_turn_runtime.save_progress(messages)
    return SuccessfulToolTurnExecution(
        messages=messages,
        system_prompt=active_system_prompt,
        conversation_history_reset=history_reset,
    )
