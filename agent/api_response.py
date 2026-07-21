"""Canonical response normalization for OpenAI-compatible chat completions."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


_THINKING_BLOCK_RE = re.compile(
    r"<(?P<tag>think|thinking|reasoning|thought|REASONING_SCRATCHPAD)\b[^>]*>"
    r"(?P<body>.*?)"
    r"</(?P=tag)\s*>",
    flags=re.DOTALL | re.IGNORECASE,
)
_THINKING_TAG_RE = re.compile(
    r"</?(?:think|thinking|reasoning|thought|REASONING_SCRATCHPAD)\b[^>]*>\s*",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ChatResponseInspection:
    """Validated first-choice view plus provider diagnostics."""

    choice: Any = None
    message: Any = None
    finish_reason: str = "stop"
    errors: tuple[str, ...] = ()
    provider_name: str = "Unknown"
    provider_error: str = "Unknown"
    error_code: int | None = None
    failure_hint: str = "invalid response"

    @property
    def valid(self) -> bool:
        return not self.errors


class TruncationAction(str, Enum):
    proceed = "proceed"
    fail_thinking_budget = "fail_thinking_budget"
    continue_text = "continue_text"
    return_partial_text = "return_partial_text"
    retry_tool_call = "retry_tool_call"
    fail_tool_call = "fail_tool_call"


@dataclass(frozen=True, slots=True)
class TruncationRecovery:
    """Pure decision for one response truncated by the output limit."""

    action: TruncationAction
    content: str = ""
    text_truncation_count: int = 0
    tool_truncation_count: int = 0


def has_thinking_tags(content: str) -> bool:
    """Return whether content contains a supported reasoning tag."""
    return bool(content and _THINKING_TAG_RE.search(content))


def strip_thinking_tags(content: str) -> str:
    """Remove reasoning markup while preserving the text inside it."""
    if not content:
        return ""
    return _THINKING_TAG_RE.sub("", content)


def strip_thinking_blocks(content: str) -> str:
    """Remove inline reasoning blocks and dangling reasoning tags."""
    if not content:
        return ""
    return strip_thinking_tags(_THINKING_BLOCK_RE.sub("", content))


def has_visible_content(content: str) -> bool:
    """Return whether assistant content contains text outside reasoning blocks."""
    return bool(strip_thinking_blocks(content).strip())


def _object_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, Mapping):
        return dict(raw)
    return None


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def inspect_chat_response(
    response: Any,
    *,
    duration_seconds: float = 0.0,
) -> ChatResponseInspection:
    """Validate one chat-completions response without mutating Agent state."""
    errors: list[str] = []
    choice = None
    message = None
    finish_reason = "stop"

    if response is None:
        errors.append("response is None")
    else:
        missing = object()
        choices = _value(response, "choices", missing)
        if choices is missing:
            errors.append("response has no 'choices' attribute")
        elif choices is None:
            errors.append("response.choices is None")
        else:
            try:
                choice = choices[0]
            except (IndexError, KeyError, TypeError):
                errors.append("response.choices is empty")
            if choice is None and not errors:
                errors.append("response.choices[0] is None")
            elif choice is not None:
                raw_message = _value(choice, "message", missing)
                if raw_message is missing:
                    errors.append("response.choices[0] has no 'message' attribute")
                elif raw_message is None:
                    errors.append("response.choices[0].message is None")
                else:
                    message = raw_message
                    finish_reason = str(
                        _value(choice, "finish_reason", "stop") or "stop"
                    )

    provider_name = "Unknown"
    provider_error = "Unknown"
    error_code = None
    response_error = _value(response, "error") if response is not None else None
    if response_error:
        provider_error = str(response_error)
        metadata = _value(response_error, "metadata")
        metadata_mapping = _object_mapping(metadata)
        if metadata_mapping:
            provider_name = str(
                metadata_mapping.get("provider_name") or provider_name
            )
        raw_code = _value(response_error, "code")
        if raw_code is not None:
            try:
                error_code = int(raw_code)
            except (TypeError, ValueError):
                pass
    else:
        response_message = _value(response, "message") if response is not None else None
        if response_message:
            provider_error = str(response_message)

    response_model = _value(response, "model") if response is not None else None
    if provider_name == "Unknown" and response_model:
        provider_name = f"model={response_model}"

    duration = max(0.0, float(duration_seconds))
    if error_code == 524:
        failure_hint = f"upstream provider timed out (Cloudflare 524, {duration:.0f}s)"
    elif error_code == 504:
        failure_hint = f"upstream gateway timeout (504, {duration:.0f}s)"
    elif error_code == 429:
        failure_hint = "rate limited by upstream provider (429)"
    elif error_code in (500, 502):
        failure_hint = f"upstream server error ({error_code}, {duration:.0f}s)"
    elif error_code in (503, 529):
        failure_hint = f"upstream provider overloaded ({error_code})"
    elif error_code is not None:
        failure_hint = f"upstream error (code {error_code}, {duration:.0f}s)"
    elif duration < 10:
        failure_hint = f"fast response ({duration:.1f}s) - likely rate limited"
    elif duration > 60:
        failure_hint = f"slow response ({duration:.0f}s) - likely upstream timeout"
    else:
        failure_hint = f"response time {duration:.1f}s"

    return ChatResponseInspection(
        choice=choice,
        message=message,
        finish_reason=finish_reason,
        errors=tuple(errors),
        provider_name=provider_name,
        provider_error=provider_error,
        error_code=error_code,
        failure_hint=failure_hint,
    )


def decide_truncation_recovery(
    message: Any,
    finish_reason: str,
    *,
    text_truncation_count: int,
    tool_truncation_count: int,
    max_text_truncations: int = 3,
    max_tool_retries: int = 1,
) -> TruncationRecovery:
    """Choose the recovery action without mutating messages or retry state."""
    if finish_reason != "length":
        return TruncationRecovery(action=TruncationAction.proceed)

    raw_content = _value(message, "content")
    content = raw_content if isinstance(raw_content, str) else ""
    has_tool_calls = bool(_value(message, "tool_calls"))
    thinking_exhausted = (
        not has_tool_calls
        and has_thinking_tags(content)
        and not has_visible_content(content)
    )
    if thinking_exhausted:
        return TruncationRecovery(
            action=TruncationAction.fail_thinking_budget,
            content=content,
            text_truncation_count=max(0, text_truncation_count),
            tool_truncation_count=max(0, tool_truncation_count),
        )

    if has_tool_calls:
        next_count = max(0, tool_truncation_count) + 1
        action = (
            TruncationAction.retry_tool_call
            if next_count <= max(0, max_tool_retries)
            else TruncationAction.fail_tool_call
        )
        return TruncationRecovery(
            action=action,
            content=content,
            text_truncation_count=max(0, text_truncation_count),
            tool_truncation_count=next_count,
        )

    next_count = max(0, text_truncation_count) + 1
    action = (
        TruncationAction.continue_text
        if next_count < max(1, max_text_truncations)
        else TruncationAction.return_partial_text
    )
    return TruncationRecovery(
        action=action,
        content=content,
        text_truncation_count=next_count,
        tool_truncation_count=max(0, tool_truncation_count),
    )


def extract_reasoning(message: Any) -> str | None:
    """Extract unique structured or inline reasoning text from one message."""
    parts: list[str] = []

    for field in ("reasoning", "reasoning_content"):
        value = _value(message, field)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned and cleaned not in parts:
                parts.append(cleaned)

    details = _value(message, "reasoning_details")
    if details:
        for detail in details:
            mapping = _object_mapping(detail)
            if mapping is None:
                continue
            value = (
                mapping.get("summary")
                or mapping.get("thinking")
                or mapping.get("content")
                or mapping.get("text")
            )
            if value is not None:
                cleaned = str(value).strip()
                if cleaned and cleaned not in parts:
                    parts.append(cleaned)

    content = _value(message, "content")
    if not parts and isinstance(content, str):
        for match in _THINKING_BLOCK_RE.finditer(content):
            cleaned = match.group("body").strip()
            if cleaned and cleaned not in parts:
                parts.append(cleaned)

    return "\n\n".join(parts) if parts else None


def visible_or_reasoning_text(message: Any) -> str:
    """Prefer visible content, then fall back to extracted reasoning text."""
    content = _value(message, "content")
    if isinstance(content, str):
        visible = strip_thinking_blocks(content).strip()
        if visible:
            return visible
    return extract_reasoning(message) or ""


def normalize_assistant_message(
    message: Any,
    finish_reason: str,
    *,
    tool_call_id_factory: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """Convert one SDK assistant message into the persisted message contract."""
    normalized: dict[str, Any] = {
        "role": "assistant",
        "content": _value(message, "content") or "",
        "reasoning": extract_reasoning(message),
        "finish_reason": finish_reason,
    }

    details = _value(message, "reasoning_details")
    if details:
        preserved = [
            mapping
            for detail in details
            if (mapping := _object_mapping(detail)) is not None
        ]
        if preserved:
            normalized["reasoning_details"] = preserved

    tool_calls = _value(message, "tool_calls")
    if not tool_calls:
        return normalized

    make_id = tool_call_id_factory or (lambda: f"call_{uuid.uuid4().hex}")
    normalized_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        raw_id = _value(tool_call, "id")
        call_id = raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else make_id()
        function = _value(tool_call, "function", {})
        normalized_call = {
            "id": call_id,
            "type": _value(tool_call, "type", "function") or "function",
            "function": {
                "name": _value(function, "name", ""),
                "arguments": _value(function, "arguments", ""),
            },
        }
        extra = _value(tool_call, "extra_content")
        if extra is not None:
            normalized_call["extra_content"] = _object_mapping(extra) or extra
        normalized_calls.append(normalized_call)
    normalized["tool_calls"] = normalized_calls
    return normalized
