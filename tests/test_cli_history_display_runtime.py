from voidcube.interfaces.cli.history_display_runtime import (
    CliHistoryDisplayPorts,
    CliHistoryDisplayRuntime,
)


def _runtime(history, output, *, resume_display="full"):
    return CliHistoryDisplayRuntime(
        CliHistoryDisplayPorts(
            conversation_history=lambda: history,
            resume_display=lambda: resume_display,
            terminal_width=lambda: 80,
            translate=lambda _key, default=None, **_kwargs: default or "translated",
            emit=output.append,
            emit_blank_line=lambda: output.append(""),
        )
    )


def test_history_display_filters_sensitive_blocks_and_summarizes_tools():
    output = []
    history = [
        {"role": "system", "content": "hidden"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "\x1b[31mquestion\x1b[0m"},
                {"type": "image_url", "image_url": {"url": "image"}},
            ],
            "timestamp": 1_700_000_000,
        },
        {
            "role": "assistant",
            "content": "<REASONING_SCRATCHPAD>private</REASONING_SCRATCHPAD>answer",
            "tool_calls": [
                {"function": {"name": "search"}},
                {"function": {"name": "search"}},
            ],
            "timestamp": 1_700_000_060,
        },
        {"role": "tool", "content": "tool result"},
    ]

    _runtime(history, output).run()

    rendered = "\n".join(output)
    assert "question [image]" in rendered
    assert "private" not in rendered
    assert "answer [2 tool calls: search]" in rendered
    assert "hidden" not in rendered
    assert "tool result" not in rendered


def test_history_display_restores_full_last_assistant_and_honors_minimal_mode():
    output = []
    long_answer = "\n".join(["line one", "line two", "line three", "line four"]) + " " + "x" * 250
    history = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": long_answer},
    ]

    _runtime(history, output).run()
    rendered = "\n".join(output)
    assert "line four" in rendered
    assert "x" * 200 in rendered

    minimal_output = []
    _runtime(history, minimal_output, resume_display="minimal").run()
    assert minimal_output == []
