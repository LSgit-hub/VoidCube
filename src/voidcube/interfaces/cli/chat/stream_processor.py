from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .render_state import CliStreamRenderState


OPEN_REASONING_TAGS = (
    "<REASONING_SCRATCHPAD>",
    "<think>",
    "<reasoning>",
    "<THINKING>",
    "<thinking>",
    "<thought>",
)
CLOSE_REASONING_TAGS = (
    "</REASONING_SCRATCHPAD>",
    "</think>",
    "</reasoning>",
    "</THINKING>",
    "</thinking>",
    "</thought>",
)


@dataclass(frozen=True, slots=True)
class StreamRenderSegment:
    kind: Literal["text", "reasoning"]
    text: str


def consume_stream_delta(
    state: CliStreamRenderState,
    text: str,
    *,
    show_reasoning: bool,
) -> tuple[StreamRenderSegment, ...]:
    """Split a model text delta into ordered visible text/reasoning segments."""
    if not text:
        return ()

    state.started = True
    segments: list[StreamRenderSegment] = []
    remaining = text

    while remaining:
        state.prefilter_buffer += remaining
        remaining = ""

        if not state.in_reasoning_block:
            opening = _find_opening_tag(state)
            if opening is None:
                safe = _take_safe_text_prefix(state)
                if safe:
                    segments.append(StreamRenderSegment("text", safe))
                    state.last_was_newline = safe.endswith("\n")
                continue

            index, tag = opening
            preceding = state.prefilter_buffer[:index]
            if preceding:
                segments.append(StreamRenderSegment("text", preceding))
                state.last_was_newline = preceding.endswith("\n")
            state.in_reasoning_block = True
            state.prefilter_buffer = state.prefilter_buffer[index + len(tag) :]

        closing = _find_first_tag(state.prefilter_buffer, CLOSE_REASONING_TAGS)
        if closing is None:
            max_tag_length = max(len(tag) for tag in CLOSE_REASONING_TAGS)
            if len(state.prefilter_buffer) > max_tag_length:
                safe_reasoning = state.prefilter_buffer[:-max_tag_length]
                if show_reasoning and safe_reasoning:
                    segments.append(StreamRenderSegment("reasoning", safe_reasoning))
                state.prefilter_buffer = state.prefilter_buffer[-max_tag_length:]
            continue

        index, tag = closing
        reasoning = state.prefilter_buffer[:index]
        if show_reasoning and reasoning:
            segments.append(StreamRenderSegment("reasoning", reasoning))
        remaining = state.prefilter_buffer[index + len(tag) :]
        state.prefilter_buffer = ""
        state.in_reasoning_block = False

    return tuple(segments)


def flush_stream_filter(state: CliStreamRenderState) -> tuple[StreamRenderSegment, ...]:
    """Return text held by an unfinished tag sequence at end of stream."""
    if not state.prefilter_buffer:
        state.in_reasoning_block = False
        return ()

    text = state.prefilter_buffer
    state.prefilter_buffer = ""
    state.in_reasoning_block = False
    state.last_was_newline = text.endswith("\n")
    return (StreamRenderSegment("text", text),)


def drain_reasoning_preview(
    state: CliStreamRenderState,
    *,
    target_width: int,
    force: bool,
) -> str:
    """Take one preview-sized reasoning chunk from the state buffer."""
    buffer = state.reasoning_preview_buffer
    if not buffer:
        return ""

    flush_text = ""
    if force:
        flush_text = buffer
        buffer = ""
    else:
        line_break = buffer.rfind("\n")
        min_newline_flush = max(16, target_width // 3)
        if line_break != -1 and (
            line_break >= min_newline_flush
            or buffer.endswith(("\n\n", ".\n", "!\n", "?\n", ":\n"))
        ):
            flush_text = buffer[: line_break + 1]
            buffer = buffer[line_break + 1 :]
        elif len(buffer) >= target_width:
            search_start = max(20, target_width // 2)
            search_end = min(
                len(buffer),
                max(target_width + (target_width // 3), target_width + 8),
            )
            cut = max(
                (
                    buffer.rfind(boundary, search_start, search_end)
                    for boundary in (" ", "\t", ".", "!", "?", ",", ";", ":")
                ),
                default=-1,
            )
            if cut != -1:
                flush_text = buffer[: cut + 1]
                buffer = buffer[cut + 1 :]

    state.reasoning_preview_buffer = buffer.lstrip() if flush_text else buffer
    return flush_text


def append_text_lines(
    state: CliStreamRenderState,
    text: str,
) -> tuple[str, ...]:
    """Append response text and return complete lines without newlines."""
    state.text_buffer += text
    lines: list[str] = []
    while "\n" in state.text_buffer:
        line, state.text_buffer = state.text_buffer.split("\n", 1)
        lines.append(line)
    return tuple(lines)


def flush_text_line(state: CliStreamRenderState) -> str:
    text = state.text_buffer
    state.text_buffer = ""
    return text


def append_reasoning_lines(
    state: CliStreamRenderState,
    text: str,
    *,
    partial_limit: int = 80,
) -> tuple[str, ...]:
    """Append reasoning and return complete or overlong partial lines."""
    state.reasoning_buffer += text
    lines: list[str] = []
    while "\n" in state.reasoning_buffer:
        line, state.reasoning_buffer = state.reasoning_buffer.split("\n", 1)
        lines.append(line)
    if len(state.reasoning_buffer) > partial_limit:
        lines.append(state.reasoning_buffer)
        state.reasoning_buffer = ""
    return tuple(lines)


def flush_reasoning_line(state: CliStreamRenderState) -> str:
    text = state.reasoning_buffer
    state.reasoning_buffer = ""
    return text


def _find_opening_tag(
    state: CliStreamRenderState,
) -> tuple[int, str] | None:
    matches: list[tuple[int, str]] = []
    buffer = state.prefilter_buffer
    for tag in OPEN_REASONING_TAGS:
        search_start = 0
        while True:
            index = buffer.find(tag, search_start)
            if index == -1:
                break
            if _is_block_boundary(buffer, index, state.last_was_newline):
                matches.append((index, tag))
                break
            search_start = index + 1
    return min(matches, key=lambda match: match[0]) if matches else None


def _is_block_boundary(buffer: str, index: int, last_was_newline: bool) -> bool:
    if index == 0:
        return last_was_newline
    preceding = buffer[:index]
    last_newline = preceding.rfind("\n")
    if last_newline == -1:
        return last_was_newline and preceding.strip() == ""
    return preceding[last_newline + 1 :].strip() == ""


def _take_safe_text_prefix(state: CliStreamRenderState) -> str:
    buffer = state.prefilter_buffer
    held_suffix_length = max(
        (
            length
            for tag in OPEN_REASONING_TAGS
            for length in range(1, len(tag))
            if buffer.endswith(tag[:length])
        ),
        default=0,
    )
    if held_suffix_length:
        safe = buffer[:-held_suffix_length]
    else:
        safe = buffer
    state.prefilter_buffer = buffer[len(safe) :]
    return safe


def _find_first_tag(
    text: str,
    tags: tuple[str, ...],
) -> tuple[int, str] | None:
    matches = ((index, tag) for tag in tags if (index := text.find(tag)) != -1)
    return min(matches, key=lambda match: match[0], default=None)
