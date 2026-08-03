from datetime import datetime

from VoidCube_cli.cli_exit_summary_runtime import CliExitSummaryPorts, CliExitSummaryRuntime


def _runtime(history, output, title=None):
    return CliExitSummaryRuntime(
        CliExitSummaryPorts(
            conversation_history=lambda: history,
            session_id=lambda: "session-1",
            session_start=lambda: datetime(2026, 8, 3, 12, 0, 0),
            now=lambda: datetime(2026, 8, 3, 13, 2, 4),
            session_title=lambda: title,
            translate=lambda _key, default=None, **_kwargs: default,
            emit=output.append,
            emit_blank_line=lambda: output.append(""),
        )
    )


def test_exit_summary_renders_resume_command_title_and_counts():
    output = []
    _runtime(
        [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result"},
        ],
        output,
        title="A session",
    ).render()

    rendered = "\n".join(output)
    assert "VoidCube --resume session-1" in rendered
    assert 'VoidCube -c "A session"' in rendered
    assert "1h 2m 4s" in rendered
    assert "3 (1 user, 2 tool calls)" in rendered


def test_exit_summary_handles_empty_history():
    output = []
    _runtime([], output).render()

    assert output == ["", "bye."]
