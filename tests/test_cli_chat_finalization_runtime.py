import VoidCube_cli.cli_chat_finalization_runtime as finalization_module
from VoidCube_cli.cli_chat_finalization_runtime import (
    CliChatFinalizationPorts,
    CliChatFinalizationRuntime,
)


def test_chat_finalization_renders_before_requeueing_followup(monkeypatch):
    events = []

    class FakeResponseRuntime:
        def __init__(self, ports):
            events.append(("response_ports", ports))

        def render(self, **kwargs):
            events.append(("render", kwargs))

    class FakeFollowupRuntime:
        def __init__(self, ports):
            events.append(("followup_ports", ports))

        def requeue(self, message):
            events.append(("requeue", message))
            return True

    monkeypatch.setattr(finalization_module, "ChatResponseRuntime", FakeResponseRuntime)
    monkeypatch.setattr(
        finalization_module,
        "InterruptedFollowupRuntime",
        FakeFollowupRuntime,
    )
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
            has_pending_queue=lambda: True,
            requeue_followup=lambda message: message,
            emit_followup=lambda _text: None,
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
        pending_message="follow-up",
    )

    assert [event[0] for event in events] == [
        "response_ports",
        "render",
        "followup_ports",
        "requeue",
    ]
    assert events[1][1]["response"] == "answer"
    assert events[-1] == ("requeue", "follow-up")
