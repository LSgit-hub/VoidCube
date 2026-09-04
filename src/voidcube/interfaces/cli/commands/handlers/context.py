"""Context-window inspection and override through explicit CLI ports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..router import ParsedCliCommand


_CONTEXT_RE = re.compile(r"^(\d+(?:\.\d+)?)([kKmMgG])?$")
_MANUAL_CONTEXT_MINIMUM = 128_000


@dataclass(frozen=True, slots=True)
class ContextCommandPorts:
    agent: Callable[[], Any | None]
    current_model: Callable[[], str]
    current_provider: Callable[[], str]
    context_state: Callable[[Any], Mapping[str, Any]]
    set_context_length: Callable[[Any, int], Mapping[str, Any]]
    save_context_length: Callable[[str, str, int], bool]
    emit: Callable[[str], None]


def parse_context_length(raw: str) -> int | None:
    """Parse a positive token count with optional K/M/G suffix."""
    match = _CONTEXT_RE.fullmatch(str(raw or "").strip())
    if not match:
        return None
    value = float(match.group(1))
    multiplier = {"k": 1_000, "m": 1_000_000, "g": 1_000_000_000}.get(
        (match.group(2) or "").lower(), 1
    )
    result = int(value * multiplier)
    return result if result > 0 else None


def _format_tokens(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "unknown"


def handle_context_command(
    request: ParsedCliCommand,
    *,
    ports: ContextCommandPorts,
) -> None:
    """Show or override the active model context window."""
    agent = ports.agent()
    argument = request.arguments.strip()
    if agent is None and not argument:
        ports.emit("  (._.) No active agent -- set a value first, for example: /context 512K.")
        return

    state = ports.context_state(agent) if agent is not None else {
        "minimum_context_length": _MANUAL_CONTEXT_MINIMUM,
        "target_ratio": 0.20,
    }
    if not argument:
        ports.emit(
            "  Context: "
            f"{_format_tokens(state.get('context_length'))} tokens "
            f"(source: {state.get('source', 'unknown')})"
        )
        ports.emit(
            "  Compression: "
            f"threshold {_format_tokens(state.get('threshold_tokens'))}, "
            f"summary {float(state.get('target_ratio', 0.2)) * 100:.0f}%, "
            f"tail {_format_tokens(state.get('tail_token_budget'))}"
        )
        ports.emit("  Usage: /context <128K|256K|512K|1M>")
        return

    # Accept exactly one argument so accidental model/config text is rejected.
    if len(argument.split()) != 1:
        ports.emit("  (._.) Usage: /context <128K|256K|512K|1M>")
        return
    context_length = parse_context_length(argument)
    minimum = max(
        _MANUAL_CONTEXT_MINIMUM,
        int(state.get("minimum_context_length", 64_000) or 64_000),
    )
    if context_length is None or context_length < minimum:
        ports.emit(
            f"  (._.) Context length must be at least {minimum:,} tokens "
            "(choose /context 128K, 256K, 512K, or 1M)."
        )
        return

    saved = ports.save_context_length(
        ports.current_provider(), ports.current_model(), context_length
    )
    if agent is None:
        source = "saved to config" if saved else "unable to save config"
        ports.emit(
            f"  ✓ Context set to {_format_tokens(context_length)} tokens ({source}); "
            "it will apply when the Agent starts."
        )
        return

    updated = ports.set_context_length(agent, context_length)
    source = "saved to config" if saved else "session only"
    ports.emit(
        f"  ✓ Context set to {_format_tokens(updated.get('context_length'))} tokens "
        f"({source}); compression threshold "
        f"{_format_tokens(updated.get('threshold_tokens'))}, "
        f"summary {float(updated.get('target_ratio', 0.2)) * 100:.0f}%."
    )
