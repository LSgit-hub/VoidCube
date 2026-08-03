from VoidCube_cli.cli_background_response_runtime import (
    CliBackgroundResponsePorts,
    CliBackgroundResponseRuntime,
)


class _Console:
    def __init__(self):
        self.items = []

    def print(self, value):
        self.items.append(value)


def _runtime(output, console, events):
    return CliBackgroundResponseRuntime(
        CliBackgroundResponsePorts(
            invalidate=lambda: events.append("invalidate"),
            sleep=lambda seconds: events.append(("sleep", seconds)),
            emit_blank_line=lambda: events.append("blank"),
            emit=output.append,
            create_console=lambda: console,
            rich_text_from_ansi=lambda text: f"rich:{text}",
        )
    )


def test_background_response_runtime_renders_success_panel_and_prompt_preview():
    output = []
    console = _Console()
    events = []

    _runtime(output, console, events).render(
        True,
        "response",
        "",
        3,
        "Background task",
        None,
        "p" * 70,
    )

    assert events == ["invalidate", ("sleep", 0.05), "blank"]
    assert any("Background task #3 complete" in line for line in output)
    assert any('Prompt: "' + "p" * 60 + '..."' in line for line in output)
    assert len(console.items) == 3
    assert "background #3" in console.items[-1].title


def test_background_response_runtime_renders_failure_without_panel():
    output = []
    console = _Console()
    _runtime(output, console, []).render(
        False,
        "",
        "timeout",
        4,
        "Job",
        "Custom",
        "question",
    )

    assert any("Job #4 failed: timeout" in line for line in output)
    assert any("(No response generated)" in line for line in output)
    assert len(console.items) == 2
