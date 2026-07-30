"""Manual conversation compression through explicit CLI runtime ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class CompressionCommandPorts:
    conversation_history: Callable[[], Sequence[Mapping[str, Any]]]
    agent: Callable[[], Any | None]
    compression_enabled: Callable[[Any], bool]
    estimate_tokens: Callable[[Sequence[Mapping[str, Any]]], int]
    compress: Callable[[Sequence[Mapping[str, Any]], int, str | None], list[dict[str, Any]]]
    synchronize_compressed_session: Callable[[list[dict[str, Any]], Any], None]
    summarize: Callable[[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]], int, int], Mapping[str, Any]]
    emit: Callable[[str], None]


def handle_compression_command(
    request: ParsedCliCommand,
    *,
    ports: CompressionCommandPorts,
) -> None:
    """Run a user-requested compaction while retaining runtime ownership."""
    history = list(ports.conversation_history())
    if len(history) < 4:
        ports.emit("(._.) Not enough conversation to compress (need at least 4 messages).")
        return

    agent = ports.agent()
    if agent is None:
        ports.emit("(._.) No active agent -- send a message first.")
        return
    if not ports.compression_enabled(agent):
        ports.emit("(._.) Compression is disabled in config.")
        return

    focus_topic = request.arguments.strip()
    original_count = len(history)
    try:
        approx_tokens = ports.estimate_tokens(history)
        if focus_topic:
            ports.emit(
                f'🗜️  Compressing {original_count} messages (~{approx_tokens:,} tokens), '
                f'focus: "{focus_topic}"...'
            )
        else:
            ports.emit(f"🗜️  Compressing {original_count} messages (~{approx_tokens:,} tokens)...")

        compressed = ports.compress(history, approx_tokens, focus_topic or None)
        ports.synchronize_compressed_session(compressed, agent)
        new_tokens = ports.estimate_tokens(compressed)
        summary = ports.summarize(history, compressed, approx_tokens, new_tokens)
        icon = "🗜️" if summary["noop"] else "✅"
        ports.emit(f"  {icon} {summary['headline']}")
        ports.emit(f"     {summary['token_line']}")
        if summary.get("note"):
            ports.emit(f"     {summary['note']}")
    except Exception as exc:
        ports.emit(f"  ❌ Compression failed: {exc}")
