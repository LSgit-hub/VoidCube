"""Pure disposition decisions for validated assistant responses."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.api_response import has_visible_content, strip_thinking_blocks


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
