"""Pure disposition decisions for validated assistant responses."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from ...infrastructure.llm.response import (
    extract_reasoning,
    has_visible_content,
    strip_thinking_blocks,
)
from .conversation_runtime import ConversationTurnRuntime
from .conversation_turn import ConversationTurnState


logger = logging.getLogger(__name__)


def normalize_assistant_content(content: Any) -> str | None:
    """Normalize compatible-provider content variants to plain text."""
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, dict):
        return (
            content.get("text", "")
            or content.get("content", "")
            or json.dumps(content)
        )
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and "text" in part:
                parts.append(str(part["text"]))
        return "\n".join(parts)
    return str(content)


@dataclass(frozen=True, slots=True)
class ToolNameRepair:
    original: str
    repaired: str


@dataclass(frozen=True, slots=True)
class ToolCallInspection:
    repairs: tuple[ToolNameRepair, ...] = ()
    invalid_tool_names: tuple[str, ...] = ()
    invalid_json_arguments: tuple[tuple[str, str], ...] = ()
    truncated_json_arguments: bool = False


def inspect_tool_calls(
    tool_calls: Iterable[Any],
    *,
    valid_tool_names: Iterable[str],
    repair_tool_name: Callable[[str], str | None],
) -> ToolCallInspection:
    """Repair names, normalize arguments, and report validation failures."""
    calls = tuple(tool_calls)
    valid_names = frozenset(valid_tool_names)
    repairs: list[ToolNameRepair] = []
    for tool_call in calls:
        name = str(tool_call.function.name)
        if name in valid_names:
            continue
        repaired = repair_tool_name(name)
        if repaired:
            tool_call.function.name = repaired
            repairs.append(ToolNameRepair(name, repaired))

    invalid_names = tuple(
        str(tool_call.function.name)
        for tool_call in calls
        if tool_call.function.name not in valid_names
    )
    if invalid_names:
        return ToolCallInspection(
            repairs=tuple(repairs),
            invalid_tool_names=invalid_names,
        )

    invalid_json: list[tuple[str, str]] = []
    for tool_call in calls:
        arguments = tool_call.function.arguments
        if isinstance(arguments, (dict, list)):
            tool_call.function.arguments = json.dumps(arguments)
            continue
        if arguments is not None and not isinstance(arguments, str):
            tool_call.function.arguments = str(arguments)
            arguments = tool_call.function.arguments
        if not arguments or not arguments.strip():
            tool_call.function.arguments = "{}"
            continue
        try:
            json.loads(arguments)
        except json.JSONDecodeError as exc:
            invalid_json.append((str(tool_call.function.name), str(exc)))

    invalid_names_set = {name for name, _ in invalid_json}
    truncated = any(
        not (tool_call.function.arguments or "").rstrip().endswith(("}", "]"))
        for tool_call in calls
        if tool_call.function.name in invalid_names_set
    )
    return ToolCallInspection(
        repairs=tuple(repairs),
        invalid_json_arguments=tuple(invalid_json),
        truncated_json_arguments=truncated,
    )


class TextResponseAction(str, Enum):
    final_text = "final_text"
    use_prior_content = "use_prior_content"
    prefill_reasoning = "prefill_reasoning"
    retry_empty = "retry_empty"
    try_fallback = "try_fallback"
    terminal_empty = "terminal_empty"


@dataclass(frozen=True, slots=True)
class TextResponseDisposition:
    action: TextResponseAction
    structured_reasoning: bool = False
    truly_empty: bool = False


def decide_text_response_disposition(
    content: str,
    *,
    structured_reasoning: bool,
    thinking_prefill_retries: int,
    empty_content_retries: int,
    prior_content_available: bool,
    fallback_available: bool,
    max_prefill_retries: int = 2,
    max_empty_retries: int = 3,
) -> TextResponseDisposition:
    """Choose the next loop action for a text-only assistant response."""
    if has_visible_content(content):
        return TextResponseDisposition(TextResponseAction.final_text)
    if prior_content_available:
        return TextResponseDisposition(
            TextResponseAction.use_prior_content,
            structured_reasoning=structured_reasoning,
        )
    if structured_reasoning and thinking_prefill_retries < max_prefill_retries:
        return TextResponseDisposition(
            TextResponseAction.prefill_reasoning,
            structured_reasoning=True,
        )

    truly_empty = not strip_thinking_blocks(content).strip()
    prefill_exhausted = (
        structured_reasoning
        and thinking_prefill_retries >= max_prefill_retries
    )
    if (
        truly_empty
        and (not structured_reasoning or prefill_exhausted)
        and empty_content_retries < max_empty_retries
    ):
        return TextResponseDisposition(
            TextResponseAction.retry_empty,
            structured_reasoning=structured_reasoning,
            truly_empty=True,
        )
    if truly_empty and fallback_available:
        return TextResponseDisposition(
            TextResponseAction.try_fallback,
            structured_reasoning=structured_reasoning,
            truly_empty=True,
        )
    return TextResponseDisposition(
        TextResponseAction.terminal_empty,
        structured_reasoning=structured_reasoning,
        truly_empty=truly_empty,
    )


class ResponseDispositionPort(Protocol):
    model: str
    provider: str
    log_prefix: str
    valid_tool_names: Iterable[str]
    quiet_mode: bool
    _invalid_tool_retries: int
    _invalid_json_retries: int
    _empty_content_retries: int
    _thinking_prefill_retries: int
    _last_content_with_tools: str | None
    _fallback_chain: list[Any]
    _response_was_previewed: bool
    _conversation_turn_runtime: ConversationTurnRuntime

    def _vprint(self, message: str, *, force: bool = False) -> None: ...

    def _emit_status(self, message: str) -> None: ...

    def _build_assistant_message(
        self,
        assistant_message: Any,
        finish_reason: str,
    ) -> dict[str, Any]: ...

    def _try_activate_fallback(self) -> bool: ...


class ResponseLoopControl(str, Enum):
    proceed = "proceed"
    continue_loop = "continue_loop"
    break_loop = "break_loop"
    terminal = "terminal"


@dataclass(frozen=True, slots=True)
class ResponseActionExecution:
    control: ResponseLoopControl
    terminal_result: dict[str, Any] | None = None


def apply_tool_call_inspection(
    owner: ResponseDispositionPort,
    inspection: ToolCallInspection,
    *,
    assistant_message: Any,
    finish_reason: str,
    messages: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]] | None,
    api_call_count: int,
    task_id: str,
) -> ResponseActionExecution:
    """Apply tool validation state changes and recovery messages once."""
    for repair in inspection.repairs:
        print(
            f"{owner.log_prefix}🔧 Auto-repaired tool name: "
            f"'{repair.original}' -> '{repair.repaired}'"
        )

    if inspection.invalid_tool_names:
        owner._invalid_tool_retries += 1
        available = ", ".join(sorted(owner.valid_tool_names))
        invalid_name = inspection.invalid_tool_names[0]
        invalid_preview = (
            invalid_name[:80] + "..." if len(invalid_name) > 80 else invalid_name
        )
        owner._vprint(
            f"{owner.log_prefix}⚠️  Unknown tool '{invalid_preview}' — "
            "sending error to model "
            f"for self-correction ({owner._invalid_tool_retries}/3)"
        )
        if owner._invalid_tool_retries >= 3:
            owner._vprint(
                f"{owner.log_prefix}❌ Max retries (3) for invalid tool calls "
                "exceeded. "
                "Stopping as partial.",
                force=True,
            )
            owner._invalid_tool_retries = 0
            terminal_result = owner._conversation_turn_runtime.partial_failure(
                messages=messages,
                conversation_history=conversation_history,
                api_call_count=api_call_count,
                error=f"Model generated invalid tool call: {invalid_preview}",
            )
            return ResponseActionExecution(
                ResponseLoopControl.terminal,
                {"final_response": None, **terminal_result},
            )

        messages.append(
            owner._build_assistant_message(assistant_message, finish_reason)
        )
        for tool_call in assistant_message.tool_calls:
            if tool_call.function.name not in owner.valid_tool_names:
                content = (
                    f"Tool '{tool_call.function.name}' does not exist. "
                    f"Available tools: {available}"
                )
            else:
                content = (
                    "Skipped: another tool call in this turn used an invalid "
                    "name. Please retry this tool call."
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": content,
                }
            )
        return ResponseActionExecution(ResponseLoopControl.continue_loop)

    owner._invalid_tool_retries = 0
    invalid_json = inspection.invalid_json_arguments
    if not invalid_json:
        owner._invalid_json_retries = 0
        return ResponseActionExecution(ResponseLoopControl.proceed)

    if inspection.truncated_json_arguments:
        owner._vprint(
            f"{owner.log_prefix}⚠️  Truncated tool call arguments detected "
            f"(finish_reason={finish_reason!r}) — refusing to execute.",
            force=True,
        )
        owner._invalid_json_retries = 0
        terminal_result = owner._conversation_turn_runtime.partial_failure(
            messages=messages,
            conversation_history=conversation_history,
            api_call_count=api_call_count,
            error="Response truncated due to output length limit",
            final_response=None,
            cleanup_task_id=task_id,
        )
        return ResponseActionExecution(
            ResponseLoopControl.terminal,
            terminal_result,
        )

    owner._invalid_json_retries += 1
    tool_name, error = invalid_json[0]
    owner._vprint(
        f"{owner.log_prefix}⚠️  Invalid JSON in tool call arguments for "
        f"'{tool_name}': {error}"
    )
    if owner._invalid_json_retries < 3:
        owner._vprint(
            f"{owner.log_prefix}🔄 Retrying API call "
            f"({owner._invalid_json_retries}/3)..."
        )
        return ResponseActionExecution(ResponseLoopControl.continue_loop)

    owner._vprint(
        f"{owner.log_prefix}⚠️  Injecting recovery tool results for invalid JSON..."
    )
    owner._invalid_json_retries = 0
    messages.append(
        owner._build_assistant_message(assistant_message, finish_reason)
    )
    invalid_names = {name for name, _ in invalid_json}
    errors = dict(invalid_json)
    for tool_call in assistant_message.tool_calls:
        if tool_call.function.name in invalid_names:
            tool_result = (
                f"Error: Invalid JSON arguments. "
                f"{errors[tool_call.function.name]}. For tools with no required "
                "parameters, use an empty object: {}. Please retry with valid JSON."
            )
        else:
            tool_result = (
                "Skipped: other tool call in this response had invalid JSON."
            )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            }
        )
    return ResponseActionExecution(ResponseLoopControl.continue_loop)


def apply_text_response_disposition(
    owner: ResponseDispositionPort,
    disposition: TextResponseDisposition,
    *,
    assistant_message: Any,
    finish_reason: str,
    state: ConversationTurnState,
    messages: list[dict[str, Any]],
) -> ResponseActionExecution:
    """Apply one text disposition and return explicit loop control."""
    if disposition.action is TextResponseAction.final_text:
        return ResponseActionExecution(ResponseLoopControl.proceed)

    if disposition.action is TextResponseAction.use_prior_content:
        prior_content = owner._last_content_with_tools or ""
        state.exit_reason = "fallback_prior_turn_content"
        logger.info(
            "Empty follow-up after tool calls — using prior turn content as "
            "final response"
        )
        owner._emit_status(
            "↻ Empty response after tool calls — using earlier content as final answer"
        )
        owner._last_content_with_tools = None
        owner._empty_content_retries = 0
        for message in reversed(messages):
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            tool_names = [
                tool_call.get("function", {}).get("name", "unknown")
                for tool_call in message["tool_calls"]
                if isinstance(tool_call, dict)
            ]
            plural = "s" if len(tool_names) > 1 else ""
            message["content"] = (
                f"Calling the {', '.join(tool_names)} tool{plural}..."
            )
            break
        state.final_response = strip_thinking_blocks(prior_content).strip()
        owner._response_was_previewed = True
        return ResponseActionExecution(ResponseLoopControl.break_loop)

    if disposition.action is TextResponseAction.prefill_reasoning:
        owner._thinking_prefill_retries += 1
        logger.info(
            "Thinking-only response (no visible content) — prefilling to "
            "continue (%d/2)",
            owner._thinking_prefill_retries,
        )
        owner._emit_status(
            "↻ Thinking-only response — prefilling to continue "
            f"({owner._thinking_prefill_retries}/2)"
        )
        interim = owner._build_assistant_message(assistant_message, "incomplete")
        interim["_thinking_prefill"] = True
        messages.append(interim)
        owner._conversation_turn_runtime.save_progress(messages)
        state.final_response = None
        return ResponseActionExecution(ResponseLoopControl.continue_loop)

    if disposition.action is TextResponseAction.retry_empty:
        owner._empty_content_retries += 1
        logger.warning(
            "Empty response (no content or reasoning) — retry %d/3 (model=%s)",
            owner._empty_content_retries,
            owner.model,
        )
        owner._emit_status(
            f"⚠️ Empty response from model — retrying "
            f"({owner._empty_content_retries}/3)"
        )
        state.final_response = None
        return ResponseActionExecution(ResponseLoopControl.continue_loop)

    if disposition.action is TextResponseAction.try_fallback:
        logger.warning(
            "Empty response after %d retries — attempting fallback "
            "(model=%s, provider=%s)",
            owner._empty_content_retries,
            owner.model,
            owner.provider,
        )
        owner._emit_status(
            "⚠️ Model returning empty responses — switching to fallback provider..."
        )
        if owner._try_activate_fallback():
            owner._empty_content_retries = 0
            owner._emit_status(
                f"↻ Switched to fallback: {owner.model} ({owner.provider})"
            )
            logger.info(
                "Fallback activated after empty responses: now using %s on %s",
                owner.model,
                owner.provider,
            )
            state.final_response = None
            return ResponseActionExecution(ResponseLoopControl.continue_loop)

    state.exit_reason = "empty_response_exhausted"
    reasoning = extract_reasoning(assistant_message)
    terminal_message = owner._build_assistant_message(
        assistant_message,
        finish_reason,
    )
    terminal_message["content"] = "(empty)"
    messages.append(terminal_message)
    if reasoning:
        preview = reasoning[:500] + "..." if len(reasoning) > 500 else reasoning
        logger.warning(
            "Reasoning-only response (no visible content) after exhausting "
            "retries and fallback. Reasoning: %s",
            preview,
        )
        owner._emit_status(
            "⚠️ Model produced reasoning but no visible response after all "
            "retries. Returning empty."
        )
    else:
        logger.warning(
            "Empty response (no content or reasoning) after %d retries. "
            "No fallback available. model=%s provider=%s",
            owner._empty_content_retries,
            owner.model,
            owner.provider,
        )
        owner._emit_status(
            "❌ Model returned no content after all retries"
            + (
                " and fallback attempts."
                if owner._fallback_chain
                else ". No fallback providers configured."
            )
        )
    state.final_response = "(empty)"
    return ResponseActionExecution(ResponseLoopControl.break_loop)
