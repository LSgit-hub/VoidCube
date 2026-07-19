"""Canonical internal function-tool schema normalization."""

from __future__ import annotations

from typing import Any, Optional

from tools.registry import registry as tool_registry


def normalize_tool_definitions(
    tools: Optional[list[Any]],
) -> list[dict[str, Any]]:
    """Normalize mixed runtime tool definitions to OpenAI function tools."""
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(tools or []):
        fallback_name = f"tool_{index}"
        if isinstance(item, dict):
            function = item.get("function")
            source = function if isinstance(function, dict) else item
            raw_name = source.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                fallback_name = raw_name.strip()
        normalized.append(
            tool_registry.normalize_tool_definition(
                item,
                fallback_name=fallback_name,
            )
        )
    return normalized
