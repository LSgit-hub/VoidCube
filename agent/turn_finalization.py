"""Canonical finalization for one completed Agent conversation turn."""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent.conversation_turn import ConversationTurnState
from agent.effect_outcomes import (
    EffectOutcome,
    failed_effect,
    finalization_status,
    require_effect_outcome,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TurnFinalizationPorts:
    """Explicit inputs and side-effect ports for one completed turn."""

    cleanup_task_resources: Callable[[str], EffectOutcome]
    persist_session: Callable[
        [list[dict[str, Any]], Sequence[Mapping[str, Any]] | None], EffectOutcome
    ]
    model: str
    provider: str
    base_url: str
    session_id: str | None
    platform: str | None
    max_iterations: int
    iteration_budget: Any
    context_compressor: Any
    valid_tool_names: Collection[str]
    usage_snapshot: Callable[[], Mapping[str, Any]]
    response_was_previewed: Callable[[], bool]
    clear_response_preview: Callable[[], None]
    interrupt_message: Callable[[], Any]
    clear_interrupt: Callable[[], None]
    clear_stream_callback: Callable[[], None]
    skill_nudge_interval: int
    iterations_since_skill: Callable[[], int]
    clear_skill_nudge: Callable[[], None]
    sync_memory: Callable[[Any, str, str], EffectOutcome] | None
    spawn_background_review: Callable[..., None]


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
    from VoidCube_app.plugins import invoke_hook

    return invoke_hook(name, **kwargs)


def finalize_conversation_turn(
    ports: TurnFinalizationPorts,
    *,
    state: ConversationTurnState,
    messages: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]] | None,
    task_id: str,
    original_user_message: Any,
    invoke_hook: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run the one canonical post-loop finalization sequence."""
    hook = invoke_hook or _default_hook_invoker
    completed = state.completed()

    try:
        cleanup_outcome = require_effect_outcome(
            ports.cleanup_task_resources(task_id),
            effect="cleanup_task_resources",
        )
    except Exception as exc:
        logger.warning("Task resource cleanup failed: %s", exc)
        cleanup_outcome = failed_effect(exc)

    try:
        persistence_outcome = require_effect_outcome(
            ports.persist_session(messages, conversation_history),
            effect="persist_session",
        )
    except Exception as exc:
        logger.warning("Session persistence failed during finalization: %s", exc)
        persistence_outcome = failed_effect(exc)

    budget = ports.iteration_budget
    diagnostics = derive_turn_diagnostics(
        messages,
        state,
        model=ports.model,
        max_iterations=ports.max_iterations,
        budget_used=budget.used if budget else 0,
        budget_max=budget.max_total if budget else 0,
        session_id=ports.session_id or "none",
    )
    emit_turn_diagnostics(diagnostics)

    if state.final_response and not state.interrupted:
        try:
            hook(
                "post_llm_call",
                session_id=ports.session_id,
                user_message=original_user_message,
                assistant_response=state.final_response,
                conversation_history=list(messages),
                model=ports.model,
                platform=ports.platform or "",
            )
        except Exception as exc:
            logger.warning("post_llm_call hook failed: %s", exc)

    usage = ports.usage_snapshot()
    response_previewed = bool(ports.response_was_previewed())
    result = {
        "final_response": state.final_response,
        "last_reasoning": last_assistant_reasoning(messages),
        "messages": messages,
        "api_calls": state.api_call_count,
        "completed": completed,
        "partial": False,
        "interrupted": state.interrupted,
        "response_previewed": response_previewed,
        "model": ports.model,
        "provider": ports.provider,
        "base_url": ports.base_url,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_tokens", 0),
        "cache_write_tokens": usage.get("cache_write_tokens", 0),
        "reasoning_tokens": usage.get("reasoning_tokens", 0),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "last_prompt_tokens": (
            getattr(ports.context_compressor, "last_prompt_tokens", 0) or 0
        ),
        "estimated_cost_usd": usage.get("estimated_cost_usd", 0.0),
        "cost_status": usage.get("cost_status", "unknown"),
        "cost_source": usage.get("cost_source", "none"),
    }
    try:
        ports.clear_response_preview()
        preview_cleanup_outcome = EffectOutcome(status="succeeded")
    except Exception as exc:
        logger.warning("Response preview cleanup failed: %s", exc)
        preview_cleanup_outcome = failed_effect(exc)
    interrupt_message = ports.interrupt_message()
    if state.interrupted and interrupt_message:
        result["interrupt_message"] = interrupt_message

    try:
        ports.clear_interrupt()
        interrupt_cleanup_outcome = EffectOutcome(status="succeeded")
    except Exception as exc:
        logger.warning("Interrupt cleanup failed: %s", exc)
        interrupt_cleanup_outcome = failed_effect(exc)
    try:
        ports.clear_stream_callback()
        stream_cleanup_outcome = EffectOutcome(status="succeeded")
    except Exception as exc:
        logger.warning("Stream callback cleanup failed: %s", exc)
        stream_cleanup_outcome = failed_effect(exc)

    review_skills = False
    memory_outcome = EffectOutcome(
        status="skipped",
        details={"reason": "not_applicable"},
    )
    if (
        ports.skill_nudge_interval > 0
        and ports.iterations_since_skill() >= ports.skill_nudge_interval
        and "skill_manage" in ports.valid_tool_names
    ):
        review_skills = True
        ports.clear_skill_nudge()

    if (
        ports.sync_memory
        and state.final_response
        and not state.interrupted
        and original_user_message
    ):
        try:
            memory_outcome = require_effect_outcome(
                ports.sync_memory(
                    original_user_message,
                    state.final_response,
                    ports.session_id or "",
                ),
                effect="sync_memory",
            )
        except Exception as exc:
            logger.warning("Memory sync enqueue failed: %s", exc)
            memory_outcome = failed_effect(exc)

    if (
        state.final_response
        and not state.interrupted
        and review_skills
    ):
        try:
            ports.spawn_background_review(
                messages_snapshot=list(messages),
            )
        except Exception:
            pass

    try:
        hook(
            "on_session_end",
            session_id=ports.session_id,
            completed=completed,
            interrupted=state.interrupted,
            model=ports.model,
            platform=ports.platform or "",
        )
    except Exception as exc:
        logger.warning("on_session_end hook failed: %s", exc)

    cleanup_status = finalization_status(
        cleanup_outcome,
        preview_cleanup_outcome,
        interrupt_cleanup_outcome,
        stream_cleanup_outcome,
    )
    result["finalization"] = {
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
    }

    return result
