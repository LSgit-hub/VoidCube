from __future__ import annotations

import pytest

from voidcube.interfaces.cli.chat.render_state import CliStreamRenderState
from voidcube.interfaces.cli.chat.stream_processor import (
    StreamRenderSegment,
    append_reasoning_lines,
    append_text_lines,
    consume_stream_delta,
    drain_reasoning_preview,
    flush_reasoning_line,
    flush_stream_filter,
    flush_text_line,
)
from voidcube.interfaces.cli.application import VoidcubeCLI


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_plain_text_is_emitted_and_partial_open_tag_is_held_across_chunks() -> None:
    state = CliStreamRenderState()

    first = consume_stream_delta(state, "hello\n<thi", show_reasoning=False)
    second = consume_stream_delta(
        state,
        "nk>private</think>world",
        show_reasoning=False,
    )

    assert first == (StreamRenderSegment("text", "hello\n"),)
    assert second == (StreamRenderSegment("text", "world"),)
    assert state.prefilter_buffer == ""
    assert state.in_reasoning_block is False


def test_reasoning_segments_are_ordered_between_visible_text_segments() -> None:
    state = CliStreamRenderState()

    segments = consume_stream_delta(
        state,
        "before\n<think>plan</think>after",
        show_reasoning=True,
    )

    assert segments == (
        StreamRenderSegment("text", "before\n"),
        StreamRenderSegment("reasoning", "plan"),
        StreamRenderSegment("text", "after"),
    )


def test_reasoning_tag_mentioned_mid_line_remains_visible_text() -> None:
    state = CliStreamRenderState()

    segments = consume_stream_delta(
        state,
        "Use <think> tags only when required.",
        show_reasoning=False,
    )

    assert segments == (
        StreamRenderSegment("text", "Use <think> tags only when required."),
    )
    assert state.in_reasoning_block is False


def test_end_of_stream_recovers_partial_or_unclosed_tag_content() -> None:
    partial_state = CliStreamRenderState()
    consume_stream_delta(partial_state, "answer <thi", show_reasoning=False)

    assert flush_stream_filter(partial_state) == (
        StreamRenderSegment("text", "<thi"),
    )

    unclosed_state = CliStreamRenderState()
    consume_stream_delta(unclosed_state, "<think>unfinished", show_reasoning=False)

    assert flush_stream_filter(unclosed_state) == (
        StreamRenderSegment("text", "unfinished"),
    )
    assert unclosed_state.in_reasoning_block is False


def test_preview_drain_respects_width_and_preserves_remainder() -> None:
    state = CliStreamRenderState(
        reasoning_preview_buffer="first section has enough words. second section"
    )

    preview = drain_reasoning_preview(state, target_width=30, force=False)

    assert preview == "first section has enough words. second "
    assert state.reasoning_preview_buffer == "section"
    assert drain_reasoning_preview(state, target_width=30, force=True) == (
        "section"
    )
    assert state.reasoning_preview_buffer == ""


def test_text_and_reasoning_line_buffers_have_explicit_flushes() -> None:
    state = CliStreamRenderState()

    assert append_text_lines(state, "one\ntwo") == ("one",)
    assert flush_text_line(state) == "two"
    assert append_reasoning_lines(state, "plan\nnext") == ("plan",)
    assert flush_reasoning_line(state) == "next"


def test_cli_stream_delta_delegates_to_renderer() -> None:
    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    calls: list[str | None] = []

    class _Renderer:
        def stream_delta(self, text: str | None) -> None:
            calls.append(text)

    cli._stream_renderer = _Renderer()

    cli._stream_delta("answer\n<think>plan</think>done")

    assert calls == ["answer\n<think>plan</think>done"]
