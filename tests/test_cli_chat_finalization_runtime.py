import VoidCube_cli.cli_chat_finalization_runtime as finalization_module
from VoidCube_cli.cli_chat_finalization_runtime import (
    CliChatFinalizationPorts,
    CliChatFinalizationRuntime,
)


def test_chat_finalization_renders_response(monkeypatch):
    events = []

    class FakeResponseRuntime:
        def __init__(self, ports):
            events.append(("response_ports", ports))

        def render(self, **kwargs):
            events.append(("render", kwargs))

    monkeypatch.setattr(finalization_module, "ChatResponseRuntime", FakeResponseRuntime)
    runtime = CliChatFinalizationRuntime(
        CliChatFinalizationPorts(
            should_emit_scrollback=lambda: True,
            show_reasoning=lambda: True,
            reasoning_already_shown=lambda: False,
            terminal_width=lambda: 80,
            emit=lambda _text: None,
            create_console=lambda: None,
            rich_text_from_ansi=lambda text: text,
            bell_on_complete=lambda: False,
            bell=lambda: None,
        )
    )

    runtime.finalize(
        response="answer",
        response_previewed=False,
        failed=False,
        partial=False,
        stream_started=False,
        response_box_open=False,
        reasoning="thinking",
    )

    assert [event[0] for event in events] == ["response_ports", "render"]
    assert events[1][1]["response"] == "answer"
