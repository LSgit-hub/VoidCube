"""Build the session status snapshot from explicit data ports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class CliStatusSnapshotPorts:
    """Session, usage and compressor data supplied by the CLI host."""

    configured_model: Callable[[], object]
    active_model: Callable[[], object]
    session_start: Callable[[], datetime]
    now: Callable[[], datetime]
    agent_usage: Callable[[], Mapping[str, Any]]
    context_usage: Callable[[], Mapping[str, Any]]
    subagent_snapshot: Callable[[], Mapping[str, Any]]
    format_duration: Callable[[float], str]


class CliStatusSnapshotRuntime:
    """Own session status projection without owning the agent or session."""

    _USAGE_FIELDS = (
        "session_input_tokens",
        "session_output_tokens",
        "session_cache_read_tokens",
        "session_cache_write_tokens",
        "session_prompt_tokens",
        "session_completion_tokens",
        "session_total_tokens",
        "session_api_calls",
    )

    def __init__(self, ports: CliStatusSnapshotPorts) -> None:
        self.ports = ports

    def snapshot(self) -> dict[str, Any]:
        ports = self.ports
        active_model = ports.active_model()
        configured_model = ports.configured_model()
        model_name = str(active_model or configured_model or "unknown")
        model_short = model_name.rsplit("/", 1)[-1]
        if model_short.endswith(".gguf"):
            model_short = model_short[:-5]
        if len(model_short) > 26:
            model_short = f"{model_short[:23]}..."

        elapsed_seconds = max(
            0.0,
            (ports.now() - ports.session_start()).total_seconds(),
        )
        snapshot: dict[str, Any] = {
            "model_name": model_name,
            "model_short": model_short,
            "duration": ports.format_duration(elapsed_seconds),
            "context_tokens": 0,
            "context_length": None,
            "context_percent": None,
            "session_input_tokens": 0,
            "session_output_tokens": 0,
            "session_cache_read_tokens": 0,
            "session_cache_write_tokens": 0,
            "session_prompt_tokens": 0,
            "session_completion_tokens": 0,
            "session_total_tokens": 0,
            "session_api_calls": 0,
            "compressions": 0,
            "subagent": dict(ports.subagent_snapshot()),
        }

        usage = ports.agent_usage()
        for field in self._USAGE_FIELDS:
            snapshot[field] = usage.get(field, 0) or 0

        context = ports.context_usage()
        context_tokens = context.get("context_tokens", 0) or 0
        context_length = context.get("context_length", 0) or 0
        snapshot["context_tokens"] = context_tokens
        snapshot["context_length"] = context_length or None
        snapshot["compressions"] = context.get("compressions", 0) or 0
        if context_length:
            snapshot["context_percent"] = max(
                0,
                min(100, round((context_tokens / context_length) * 100)),
            )
        return snapshot
