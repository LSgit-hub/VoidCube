"""Read, export, and mutation handlers for CLI conversation history."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from VoidCube_app.session_lifecycle import (
    HistoryMutationResult,
    HistoryMutationStatus,
    SessionHydration,
)
from VoidCube_cli.command_router import ParsedCliCommand


Message = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HistoryCommandPorts:
    conversation_history: Callable[[], Sequence[Message]]
    show_recent_sessions: Callable[[], bool]
    emit: Callable[[str], None]
    no_history_message: str
    tools_label: str


@dataclass(frozen=True, slots=True)
class SaveConversationPorts:
    conversation_history: Callable[[], Sequence[Message]]
    model: Callable[[], str]
    session_start: Callable[[], datetime]
    now: Callable[[], datetime]
    working_directory: Callable[[], Path]
    write_json: Callable[[Path, Mapping[str, Any]], None]
    emit: Callable[[str], None]
    no_conversation_message: str


@dataclass(frozen=True, slots=True)
class HistoryMutationPorts:
    conversation_history: Callable[[], Sequence[Message]]
    repository: Callable[[], Any | None]
    session_id: Callable[[], str]
    remove_last_user_turn: Callable[[Any | None], HistoryMutationResult]
    synchronize_agent_history: Callable[[list[Message]], None]
    hydration: Callable[[], SessionHydration | None]
    set_hydration: Callable[[SessionHydration], None]
    emit: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class UndoCommandPorts:
    remove_last_user_turn: Callable[[], HistoryMutationResult | None]
    emit: Callable[[str], None]


def handle_history_command(
    request: ParsedCliCommand,
    *,
    ports: HistoryCommandPorts,
) -> None:
    """Project active history without taking ownership of session state."""
    del request
    history = ports.conversation_history()
    if not history:
        if not ports.show_recent_sessions():
            ports.emit(ports.no_history_message)
        return

    ports.emit("")
    ports.emit("+" + "-" * 50 + "+")
    ports.emit("|" + " " * 12 + "(^_^) Conversation History" + " " * 11 + "|")
    ports.emit("+" + "-" * 50 + "+")

    visible_index = 0
    hidden_tool_messages = 0
    for message in history:
        role = message.get("role", "unknown")
        if role == "tool":
            hidden_tool_messages += 1
            continue
        if role not in {"user", "assistant"}:
            continue

        _emit_tool_summary(ports, hidden_tool_messages)
        hidden_tool_messages = 0
        visible_index += 1
        content = message.get("content")
        content_text = "" if content is None else str(content)
        if role == "user":
            ports.emit(f"\n  [You #{visible_index}]")
            ports.emit(f"    {_preview(content_text)}")
            continue

        ports.emit(f"\n  [Voidcube #{visible_index}]")
        ports.emit(f"    {_assistant_preview(content_text, message.get('tool_calls'))}")

    _emit_tool_summary(ports, hidden_tool_messages)
    ports.emit("")


def handle_save_conversation_command(
    request: ParsedCliCommand,
    *,
    ports: SaveConversationPorts,
) -> None:
    """Export the active history to the conventional timestamped JSON file."""
    del request
    history = ports.conversation_history()
    if not history:
        ports.emit(ports.no_conversation_message)
        return

    filename = f"VoidCube_conversation_{ports.now().strftime('%Y%m%d_%H%M%S')}.json"
    destination = ports.working_directory() / filename
    try:
        ports.write_json(
            destination,
            {
                "model": ports.model(),
                "session_start": ports.session_start().isoformat(),
                "messages": list(history),
            },
        )
    except Exception as exc:
        ports.emit(f"(x_x) Failed to save: {exc}")
        return
    ports.emit(f"(^_^)v Conversation saved to: {filename}")


def write_conversation_export(path: Path, payload: Mapping[str, Any]) -> None:
    """Write an export with the original overwrite and Unicode semantics."""
    with path.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)


def remove_last_user_turn_from_history(
    *,
    ports: HistoryMutationPorts,
    empty_message: str,
    no_user_message: str,
) -> HistoryMutationResult | None:
    """Apply the shared mutation and synchronize adapter-owned projections."""
    history = ports.conversation_history()
    has_user_turn = any(message.get("role") == "user" for message in history)
    repository = ports.repository() if has_user_turn else None
    result = ports.remove_last_user_turn(repository)
    if result.status is HistoryMutationStatus.EMPTY:
        ports.emit(empty_message)
        return None
    if result.status is HistoryMutationStatus.NO_USER_MESSAGE:
        ports.emit(no_user_message)
        return None
    if result.status is HistoryMutationStatus.PERSISTENCE_FAILED:
        ports.emit(f"(x_x) Could not update session history: {result.persistence_error}")
        return None

    history = list(result.conversation_history)
    ports.synchronize_agent_history(history)
    hydration = ports.hydration()
    ports.set_hydration(
        result.hydration(
            session_id=ports.session_id(),
            metadata=hydration.metadata if hydration else None,
        )
    )
    return result


def handle_undo_command(
    request: ParsedCliCommand,
    *,
    ports: UndoCommandPorts,
) -> None:
    """Undo one user-anchored conversation turn and project its summary."""
    del request
    result = ports.remove_last_user_turn()
    if result is None:
        return
    preview = str(result.user_message or "")
    suffix = "..." if len(preview) > 60 else ""
    ports.emit(
        f'(^_^)b Undid {len(result.removed_messages)} message(s). Removed: '
        f'"{preview[:60]}{suffix}"'
    )
    ports.emit(f"  {len(result.conversation_history)} message(s) remaining in history.")


def _emit_tool_summary(ports: HistoryCommandPorts, count: int) -> None:
    if not count:
        return
    noun = "message" if count == 1 else "messages"
    ports.emit(ports.tools_label)
    ports.emit(f"    ({count} tool {noun} hidden)")


def _preview(content: str, *, limit: int = 400) -> str:
    return f"{content[:limit]}{'...' if len(content) > limit else ''}"


def _assistant_preview(content: str, tool_calls: object) -> str:
    if content:
        return _preview(content)
    if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, (str, bytes)):
        count = len(tool_calls)
        noun = "call" if count == 1 else "calls"
        return f"(requested {count} tool {noun})"
    return "(no text response)"
