from __future__ import annotations

import pytest

from voidcube.interfaces.cli.chat.render_state import CliStreamRenderState
from voidcube.interfaces.cli.chat.stream_renderer import CliStreamRenderer


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _renderer(
    state: CliStreamRenderState,
    output: list[str],
    *,
    visible: bool = True,
    show_reasoning: bool = True,
    verbose: bool = False,
) -> CliStreamRenderer:
    return CliStreamRenderer(
        state,
        emit_line=output.append,
        should_emit=lambda: visible,
        show_reasoning=lambda: show_reasoning,
        verbose=lambda: verbose,
        terminal_width=lambda: 60,
    )


def test_reasoning_box_is_closed_before_deferred_response_box() -> None:
    state = CliStreamRenderState()
    output: list[str] = []
    renderer = _renderer(state, output)

    renderer.stream_delta("<think>plan</think>answer")
    renderer.flush_stream()

    rendered = "\n".join(output)
    assert rendered.index("Reasoning") < rendered.index("plan")
    assert rendered.index("plan") < rendered.index("└")
    assert rendered.index("└") < rendered.index("╭")
    assert rendered.index("╭") < rendered.index("answer")
    assert rendered.index("answer") < rendered.index("╰")


def test_intermediate_boundary_flushes_and_resets_stream_not_turn_history() -> None:
    state = CliStreamRenderState(reasoning_shown_this_turn=True)
    output: list[str] = []
    renderer = _renderer(state, output, show_reasoning=False)

    renderer.stream_delta("partial")
    renderer.stream_delta(None)

    assert any("partial" in line for line in output)
    assert state == CliStreamRenderState(reasoning_shown_this_turn=True)


def test_hidden_renderer_tracks_delivery_without_emitting_output() -> None:
    state = CliStreamRenderState()
    output: list[str] = []
    renderer = _renderer(state, output, visible=False)

    renderer.stream_delta("hidden")
    assert state.started is True

    renderer.stream_delta(None)
    assert state.started is False
    assert output == []


def test_reasoning_preview_is_bounded_unless_verbose() -> None:
    state = CliStreamRenderState()
    output: list[str] = []
    renderer = _renderer(state, output)
    preview = "\n\n".join(f"line {index}" for index in range(8))

    renderer.emit_reasoning_preview(preview)

    assert "line 0" in output[0]
    assert "more lines" in output[0]
    assert "line 7" not in output[0]


def test_closed_code_fence_is_emitted_once_with_highlighting() -> None:
    state = CliStreamRenderState()
    output: list[str] = []
    renderer = _renderer(state, output, show_reasoning=False)

    renderer.stream_delta("before\n```python\nprint('hello')\n```\nafter\n")
    renderer.flush_stream()

    rendered = "\n".join(output)
    assert "before" in rendered
    assert "print" in rendered
    assert "after" in rendered
    assert "```python" not in rendered
    assert state.in_code_fence is False


def test_unclosed_code_fence_falls_back_to_literal_text() -> None:
    state = CliStreamRenderState()
    output: list[str] = []
    renderer = _renderer(state, output, show_reasoning=False)

    renderer.stream_delta("```python\nprint('partial')")
    renderer.flush_stream()

    rendered = "\n".join(output)
    assert "```python" in rendered
    assert "print('partial')" in rendered
    assert state.in_code_fence is False
