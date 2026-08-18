"""Stable inline formatting for CLI tool progress and diff previews."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ...domain.contracts.execution import ExecutionState


def normalize_tool_progress_mode(value: Any) -> str:
    """Return one of the supported display modes."""
    if value is False or value is None:
        return "off"
    mode = str(value).strip().lower()
    return mode if mode in {"off", "new", "all", "verbose"} else "all"


def should_emit_tool_completion(
    mode: Any,
    tool_name: str,
    last_tool_name: str,
) -> bool:
    """Decide whether a completion belongs in the inline transcript."""
    normalized = normalize_tool_progress_mode(mode)
    if not tool_name or normalized == "off":
        return False
    return normalized != "new" or tool_name != last_tool_name


def format_tool_completion(
    tool_name: str,
    arguments: Mapping[str, Any],
    duration: float,
    *,
    result: str | None = None,
    state: ExecutionState = ExecutionState.SUCCEEDED,
    get_message: Callable[..., str] | None = None,
) -> str:
    """Format one compact completion with an explicit terminal status.

    The existing agent formatter remains the source of tool-specific labels and
    previews. The CLI owns only the lifecycle marker, keeping agent display
    policy independent from terminal presentation.
    """
    if get_message is None:
        from ...runtime.agent.display import get_cute_tool_message

        get_message = get_cute_tool_message
    base = get_message(
        tool_name,
        dict(arguments),
        max(0.0, float(duration)),
        result,
    )
    marker = {
        ExecutionState.SUCCEEDED: "✓",
        ExecutionState.FAILED: "✗",
        ExecutionState.CANCELLED: "⊘",
        ExecutionState.TIMED_OUT: "⌛",
        ExecutionState.UNKNOWN: "?",
    }[state]
    return f"{marker} {base}"


def emit_diff_line(emit: Callable[[str], None], line: str) -> None:
    """Keep inline diff sections visually aligned with tool summaries."""
    if line.startswith("  ┊ review diff"):
        emit(line)
        return
    emit(f"  │ {line.lstrip()}")


__all__ = [
    "emit_diff_line",
    "format_tool_completion",
    "normalize_tool_progress_mode",
    "should_emit_tool_completion",
]
