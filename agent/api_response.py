"""Canonical response normalization for OpenAI-compatible chat completions."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
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
