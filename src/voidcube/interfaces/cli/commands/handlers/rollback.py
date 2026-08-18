"""Filesystem checkpoint rollback command handler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from ..router import ParsedCliCommand


Checkpoint = Mapping[str, Any]
CheckpointResult = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RollbackCommandText:
    no_active_agent: str
    checkpoints_not_enabled: str
    checkpoints_enable_command: str
    checkpoints_enable_config: str
    usage_diff: str
    no_checkpoints: Callable[[str], str]
    no_changes: str
    more_lines: Callable[[int], str]
    restored: Callable[[str, str], str]
    restored_file: Callable[[str, str, str], str]
    snapshot_saved: str
    chat_undone: str
    invalid_number: Callable[[int], str]


@dataclass(frozen=True, slots=True)
class RollbackCommandPorts:
    checkpoint_manager: Callable[[], Any | None]
    manager_enabled: Callable[[Any], bool]
    working_directory: Callable[[], str]
    list_checkpoints: Callable[[Any, str], Sequence[Checkpoint]]
    format_checkpoints: Callable[[Sequence[Checkpoint], str], str]
    diff: Callable[[Any, str, str], CheckpointResult]
    restore: Callable[[Any, str, str, str | None], CheckpointResult]
    has_conversation_history: Callable[[], bool]
    undo_chat_history: Callable[[], None]
    emit: Callable[[str], None]
    text: RollbackCommandText


def handle_rollback_command(
    request: ParsedCliCommand,
    *,
    ports: RollbackCommandPorts,
) -> None:
    """List, diff, or restore filesystem checkpoints through narrow ports."""
    manager = ports.checkpoint_manager()
    if manager is None:
        ports.emit(ports.text.no_active_agent)
        return
    if not ports.manager_enabled(manager):
        ports.emit(ports.text.checkpoints_not_enabled)
        ports.emit(ports.text.checkpoints_enable_command)
        ports.emit(ports.text.checkpoints_enable_config)
        return

    directory = ports.working_directory()
    arguments = request.arguments.split()
    if not arguments:
        checkpoints = ports.list_checkpoints(manager, directory)
        ports.emit(ports.format_checkpoints(checkpoints, directory))
        return

    if arguments[0].lower() == "diff":
        _handle_diff(arguments, manager=manager, directory=directory, ports=ports)
        return
    _handle_restore(arguments, manager=manager, directory=directory, ports=ports)


def resolve_checkpoint_reference(
    reference: str,
    checkpoints: Sequence[Checkpoint],
) -> str | None:
    """Resolve a one-based list index, otherwise preserve a hash reference."""
    try:
        index = int(reference) - 1
    except ValueError:
        return reference
    if 0 <= index < len(checkpoints):
        return str(checkpoints[index]["hash"])
    return None


def _handle_diff(
    arguments: list[str],
    *,
    manager: Any,
    directory: str,
    ports: RollbackCommandPorts,
) -> None:
    if len(arguments) < 2:
        ports.emit(ports.text.usage_diff)
        return
    checkpoints = ports.list_checkpoints(manager, directory)
    if not checkpoints:
        ports.emit(ports.text.no_checkpoints(directory))
        return
    target_hash = resolve_checkpoint_reference(arguments[1], checkpoints)
    if target_hash is None:
        ports.emit(ports.text.invalid_number(len(checkpoints)))
        return
    result = ports.diff(manager, directory, target_hash)
    if not result.get("success"):
        ports.emit(f"  ❌ {result.get('error', '')}")
        return

    stat = str(result.get("stat", "") or "")
    diff = str(result.get("diff", "") or "")
    if not stat and not diff:
        ports.emit(ports.text.no_changes)
        return
    if stat:
        ports.emit(f"\n{stat}")
    if diff:
        _emit_diff(diff, ports=ports)


def _handle_restore(
    arguments: list[str],
    *,
    manager: Any,
    directory: str,
    ports: RollbackCommandPorts,
) -> None:
    checkpoints = ports.list_checkpoints(manager, directory)
    if not checkpoints:
        ports.emit(ports.text.no_checkpoints(directory))
        return
    target_hash = resolve_checkpoint_reference(arguments[0], checkpoints)
    if target_hash is None:
        ports.emit(ports.text.invalid_number(len(checkpoints)))
        return

    file_path = arguments[1] if len(arguments) > 1 else None
    result = ports.restore(manager, directory, target_hash, file_path)
    if not result.get("success"):
        ports.emit(f"  ❌ {result.get('error', '')}")
        return

    restored_to = str(result.get("restored_to", ""))
    reason = str(result.get("reason", ""))
    if file_path:
        ports.emit(ports.text.restored_file(file_path, restored_to, reason))
    else:
        ports.emit(ports.text.restored(restored_to, reason))
    ports.emit(ports.text.snapshot_saved)
    if ports.has_conversation_history():
        ports.undo_chat_history()
        ports.emit(ports.text.chat_undone)


def _emit_diff(diff: str, *, ports: RollbackCommandPorts) -> None:
    lines = diff.splitlines()
    if len(lines) > 80:
        ports.emit("\n".join(lines[:80]))
        ports.emit(f"\n{ports.text.more_lines(len(lines) - 80)}")
        return
    ports.emit(f"\n{diff}")
