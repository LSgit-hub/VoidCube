from __future__ import annotations

import pytest

from VoidCube_cli.chat_render_state import CliStreamRenderState
from VoidCube_cli.chat_stream_renderer import CliStreamRenderer
pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _populated_state() -> CliStreamRenderState:
    return CliStreamRenderState(
        text_buffer="response",
        started=True,
        response_box_open=True,
        text_ansi="ansi",
        prefilter_buffer="<think>",
        last_was_newline=False,
        in_reasoning_block=True,
        reasoning_box_open=True,
        reasoning_buffer="reasoning",
        reasoning_preview_buffer="preview",
        deferred_content="deferred",
        reasoning_shown_this_turn=True,
        in_code_fence=True,
        code_fence_language="python",
        code_fence_lines=["print('hello')"],
    )


def test_reset_stream_preserves_turn_level_reasoning_history() -> None:
    state = _populated_state()

    state.reset_stream()

    assert state == CliStreamRenderState(reasoning_shown_this_turn=True)


def test_begin_turn_resets_stream_and_turn_level_reasoning_history() -> None:
    state = _populated_state()

    state.begin_turn()

    assert state == CliStreamRenderState()


def test_hidden_stream_updates_started_state_and_boundary_resets_it() -> None:
    from cli import VoidcubeCLI

    cli = VoidcubeCLI.__new__(VoidcubeCLI)
    cli._stream_render_state = CliStreamRenderState()
    cli._should_emit_scrollback_output = lambda: False
    cli._stream_renderer = CliStreamRenderer(
        cli._stream_render_state,
        emit_line=lambda text: None,
        should_emit=cli._should_emit_scrollback_output,
        show_reasoning=lambda: False,
        verbose=lambda: False,
    )

    cli._stream_delta("content")
    assert cli._stream_render_state.started is True

    cli._stream_delta(None)
    assert cli._stream_render_state.started is False
