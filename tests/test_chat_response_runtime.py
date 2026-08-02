from __future__ import annotations

from VoidCube_cli.chat_response_runtime import ChatResponsePorts, ChatResponseRuntime


class _Console:
    def __init__(self, rendered):
        self.rendered = rendered

    def print(self, value):
        self.rendered.append(value)


def _runtime(calls, rendered, *, emit_scrollback=True):
    return ChatResponseRuntime(
        ChatResponsePorts(
            should_emit_scrollback=lambda: emit_scrollback,
            show_reasoning=lambda: True,
            reasoning_already_shown=lambda: False,
            terminal_width=lambda: 80,
            emit=lambda value: calls.append(value),
            create_console=lambda: _Console(rendered),
            rich_text_from_ansi=lambda value: ("rich", value),
            bell_on_complete=lambda: True,
            bell=lambda: calls.append("bell"),
        )
    )


def test_response_runtime_renders_bounded_reasoning_and_panel():
    calls = []
    rendered = []
    reasoning = "\n".join(f"line-{index}" for index in range(12))

    _runtime(calls, rendered).render(
        response="answer",
        response_previewed=False,
        failed=False,
        partial=False,
        stream_started=False,
        response_box_open=False,
        reasoning=reasoning,
    )

    assert "line-9" in calls[0]
    assert "line-10" not in calls[0]
    assert rendered
    assert calls[-1] == "bell"


def test_response_runtime_skips_panel_for_streamed_response_or_quiet_host():
    calls = []
    rendered = []
    runtime = _runtime(calls, rendered)

    runtime.render(
        response="already streamed",
        response_previewed=False,
        failed=False,
        partial=False,
        stream_started=True,
        response_box_open=True,
        reasoning="",
    )

    assert rendered == []
    assert calls == ["bell"]

    quiet_calls = []
    quiet_rendered = []
    _runtime(quiet_calls, quiet_rendered, emit_scrollback=False).render(
        response="hidden",
        response_previewed=False,
        failed=False,
        partial=False,
        stream_started=False,
        response_box_open=False,
        reasoning="hidden reasoning",
    )

    assert quiet_calls == []
    assert quiet_rendered == []
