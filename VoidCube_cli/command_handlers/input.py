"""Input queue command handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class QueueCommandPorts:
    enqueue: Callable[[str], None]
    agent_running: Callable[[], bool]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class RetryCommandPorts:
    remove_last_user_turn: Callable[[], Any]
    enqueue: Callable[[Any], None]
    emit: Callable[[str], None]


def handle_queue_command(
    request: ParsedCliCommand,
    *,
    ports: QueueCommandPorts,
) -> None:
    payload = request.arguments
    if not payload:
        ports.emit("  Usage: /queue <prompt>")
        return
    ports.enqueue(payload)
    preview = f"{payload[:80]}{'...' if len(payload) > 80 else ''}"
    prefix = "Queued for the next turn" if ports.agent_running() else "Queued"
    ports.emit(f"  {prefix}: {preview}")


def handle_retry_command(
    request: ParsedCliCommand,
    *,
    ports: RetryCommandPorts,
) -> None:
    del request
    result = ports.remove_last_user_turn()
    if result is None:
        return
    retry_message = result.user_message
    preview = str(retry_message or "")
    suffix = "..." if len(preview) > 60 else ""
    ports.emit(f'(^_^)b Retrying: "{preview[:60]}{suffix}"')
    if retry_message:
        ports.enqueue(retry_message)
