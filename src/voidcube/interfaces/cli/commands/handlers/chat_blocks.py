"""CLI handlers for structured chat-block search and export."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ...chat.block_exports import (
    default_export_path,
    export_chat_blocks,
    render_search_result,
    search_chat_blocks,
)
from ...chat.block_store import ChatBlock
from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class ChatBlockCommandPorts:
    blocks: Callable[[], Sequence[ChatBlock]]
    session_id: Callable[[], str]
    now: Callable[[], datetime]
    working_directory: Callable[[], Path]
    emit: Callable[[str], None]


def handle_find_command(
    request: ParsedCliCommand, *, ports: ChatBlockCommandPorts,
) -> None:
    query = request.arguments.strip()
    if not query:
        ports.emit("  Usage: /find <keyword>")
        return
    matches = search_chat_blocks(ports.blocks(), query)
    if not matches:
        ports.emit(f"  No matches for: {query}")
        return
    ports.emit(f"  Found {len(matches)} matching block(s):")
    for block in matches:
        ports.emit(render_search_result(block))


def handle_export_command(
    request: ParsedCliCommand, *, ports: ChatBlockCommandPorts,
) -> None:
    output_format = request.arguments.strip().lower()
    if output_format not in {"markdown", "json"}:
        ports.emit("  Usage: /export <markdown|json>")
        return
    blocks = tuple(ports.blocks())
    if not blocks:
        ports.emit("  No structured chat blocks to export.")
        return
    exported_at = ports.now()
    destination = default_export_path(
        ports.working_directory(),
        output_format=output_format,
        exported_at=exported_at,
    )
    try:
        export_chat_blocks(
            blocks,
            session_id=ports.session_id(),
            output_format=output_format,
            destination=destination,
            exported_at=exported_at,
        )
    except (OSError, ValueError) as error:
        ports.emit(f"  Export failed: {error}")
        return
    ports.emit(f"  Session exported to: {destination.name}")


__all__ = [
    "ChatBlockCommandPorts",
    "handle_export_command",
    "handle_find_command",
]
