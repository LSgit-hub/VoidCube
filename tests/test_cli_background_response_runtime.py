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
    assert any("后台任务 #3 已完成" in line for line in output)
    assert any('提示词："' + "p" * 60 + '..."' in line for line in output)
    assert len(console.items) == 3
    assert "后台任务 #3" in console.items[-1].title


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

    assert any("任务 #4 失败：timeout" in line for line in output)
    assert any("（未生成响应）" in line for line in output)
    assert len(console.items) == 2
