"""Canonical finalization for one completed Agent conversation turn."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from agent.conversation_turn import ConversationTurnState


logger = logging.getLogger(__name__)


class TurnFinalizationPort(Protocol):
    max_iterations: int
    model: str
    provider: str
    base_url: str
    session_id: str | None
    platform: str | None
    iteration_budget: Any
    context_compressor: Any
    valid_tool_names: list[str]
    session_input_tokens: int
    session_output_tokens: int
    session_cache_read_tokens: int
    session_cache_write_tokens: int
    session_reasoning_tokens: int
    session_prompt_tokens: int
    session_completion_tokens: int
    session_total_tokens: int
    session_estimated_cost_usd: float
    session_cost_status: str
    session_cost_source: str
    _session_persistence: Any
    _response_was_previewed: bool
    _interrupt_message: Any
    _stream_callback: Any
    _skill_nudge_interval: int
    _iters_since_skill: int
    _memory_manager: Any

    def _cleanup_task_resources(self, task_id: str) -> None: ...

    def clear_interrupt(self) -> None: ...

    def _spawn_background_review(
        self,
        *,
        messages_snapshot: list[dict[str, Any]],
        review_memory: bool,
        review_skills: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class TurnDiagnostics:
    exit_reason: str
    model: str
    api_call_count: int
    max_iterations: int
    budget_used: int
    budget_max: int
    tool_turn_count: int
    last_message_role: str | None
    last_tool_name: str | None
    response_length: int
    session_id: str
    interrupted: bool

    @property
    def pending_tool_result(self) -> bool:
        return self.last_message_role == "tool" and not self.interrupted


def derive_turn_diagnostics(
    messages: list[dict[str, Any]],
    state: ConversationTurnState,
    *,
    model: str,
    max_iterations: int,
    budget_used: int,
    budget_max: int,
    session_id: str,
) -> TurnDiagnostics:
    """Derive one immutable diagnostic record from finalized turn data."""
    last_role = messages[-1].get("role") if messages else None
    last_tool_name = None
    if last_role == "tool":
        for message in reversed(messages):
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            tool_calls = message["tool_calls"]
            if tool_calls and isinstance(tool_calls[-1], dict):
                last_tool_name = tool_calls[-1].get("function", {}).get("name")
            break
    tool_turn_count = sum(
        1
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("tool_calls")
    )
    return TurnDiagnostics(
        exit_reason=state.exit_reason,
        model=model,
        api_call_count=state.api_call_count,
        max_iterations=max_iterations,
        budget_used=budget_used,
        budget_max=budget_max,
        tool_turn_count=tool_turn_count,
        last_message_role=last_role,
        last_tool_name=last_tool_name,
        response_length=len(state.final_response or ""),
        session_id=session_id or "none",
        interrupted=state.interrupted,
    )


def emit_turn_diagnostics(diagnostics: TurnDiagnostics) -> None:
    message = (
        "Turn ended: reason=%s model=%s api_calls=%d/%d budget=%d/%d "
        "tool_turns=%d last_msg_role=%s response_len=%d session=%s"
    )
    values = (
        diagnostics.exit_reason,
        diagnostics.model,
        diagnostics.api_call_count,
        diagnostics.max_iterations,
        diagnostics.budget_used,
        diagnostics.budget_max,
        diagnostics.tool_turn_count,
        diagnostics.last_message_role,
        diagnostics.response_length,
        diagnostics.session_id,
    )
    if diagnostics.pending_tool_result:
        logger.warning(
            "Turn ended with pending tool result (agent may appear stuck). "
            + message
            + " last_tool=%s",
            *values,
            diagnostics.last_tool_name,
        )
    else:
        logger.info(message, *values)


def last_assistant_reasoning(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") == "assistant" and message.get("reasoning"):
            return str(message["reasoning"])
    return None


def _default_hook_invoker(name: str, **kwargs: Any) -> Any:
    from VoidCube_cli.plugins import invoke_hook

    return invoke_hook(name, **kwargs)


def finalize_conversation_turn(
    owner: TurnFinalizationPort,
    *,
    state: ConversationTurnState,
    messages: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]] | None,
    task_id: str,
    original_user_message: Any,
    review_memory: bool,
    invoke_hook: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run the one canonical post-loop finalization sequence."""
    hook = invoke_hook or _default_hook_invoker
    completed = state.completed(max_iterations=owner.max_iterations)

    owner._cleanup_task_resources(task_id)
    owner._session_persistence.persist(messages, conversation_history)

    budget = owner.iteration_budget
    diagnostics = derive_turn_diagnostics(
        messages,
        state,
        model=owner.model,
        max_iterations=owner.max_iterations,
        budget_used=budget.used if budget else 0,
        budget_max=budget.max_total if budget else 0,
        session_id=owner.session_id or "none",
    )
    emit_turn_diagnostics(diagnostics)

    if state.final_response and not state.interrupted:
        try:
            hook(
                "post_llm_call",
                session_id=owner.session_id,
                user_message=original_user_message,
                assistant_response=state.final_response,
                conversation_history=list(messages),
                model=owner.model,
                platform=owner.platform or "",
            )
        except Exception as exc:
            logger.warning("post_llm_call hook failed: %s", exc)

    result = {
        "final_response": state.final_response,
        "last_reasoning": last_assistant_reasoning(messages),
        "messages": messages,
        "api_calls": state.api_call_count,
        "completed": completed,
        "partial": False,
        "interrupted": state.interrupted,
        "response_previewed": bool(
            getattr(owner, "_response_was_previewed", False)
        ),
        "model": owner.model,
        "provider": owner.provider,
        "base_url": owner.base_url,
        "input_tokens": owner.session_input_tokens,
        "output_tokens": owner.session_output_tokens,
        "cache_read_tokens": owner.session_cache_read_tokens,
        "cache_write_tokens": owner.session_cache_write_tokens,
        "reasoning_tokens": owner.session_reasoning_tokens,
        "prompt_tokens": owner.session_prompt_tokens,
        "completion_tokens": owner.session_completion_tokens,
        "total_tokens": owner.session_total_tokens,
        "last_prompt_tokens": (
            getattr(owner.context_compressor, "last_prompt_tokens", 0) or 0
        ),
        "estimated_cost_usd": owner.session_estimated_cost_usd,
        "cost_status": owner.session_cost_status,
        "cost_source": owner.session_cost_source,
    }
    owner._response_was_previewed = False
    if state.interrupted and owner._interrupt_message:
        result["interrupt_message"] = owner._interrupt_message

    owner.clear_interrupt()
    owner._stream_callback = None

    review_skills = False
    if (
        owner._skill_nudge_interval > 0
        and owner._iters_since_skill >= owner._skill_nudge_interval
        and "skill_manage" in owner.valid_tool_names
    ):
        review_skills = True
        owner._iters_since_skill = 0

    if (
        owner._memory_manager
        and state.final_response
        and not state.interrupted
        and original_user_message
    ):
        try:
            owner._memory_manager.sync_all(
                original_user_message,
                state.final_response,
            )
            owner._memory_manager.queue_prefetch_all(original_user_message)
        except Exception:
            pass

    if (
        state.final_response
        and not state.interrupted
        and (review_memory or review_skills)
    ):
        try:
            owner._spawn_background_review(
                messages_snapshot=list(messages),
                review_memory=review_memory,
                review_skills=review_skills,
            )
        except Exception:
            pass

    try:
        hook(
            "on_session_end",
            session_id=owner.session_id,
            completed=completed,
            interrupted=state.interrupted,
            model=owner.model,
            platform=owner.platform or "",
        )
    except Exception as exc:
        logger.warning("on_session_end hook failed: %s", exc)

    return result
