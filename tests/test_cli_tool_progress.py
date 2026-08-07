from __future__ import annotations

from VoidCube_cli.cli_tool_progress import (
    emit_diff_line,
    format_tool_completion,
    normalize_tool_progress_mode,
    should_emit_tool_completion,
)


def test_tool_progress_mode_is_normalized_to_supported_values() -> None:
    assert normalize_tool_progress_mode(False) == "off"
    assert normalize_tool_progress_mode("VERBOSE") == "verbose"
    assert normalize_tool_progress_mode("unknown") == "all"


def test_new_mode_skips_only_repeated_tool_names() -> None:
    assert should_emit_tool_completion("new", "read_file", "shell")
    assert not should_emit_tool_completion("new", "read_file", "read_file")
    assert not should_emit_tool_completion("off", "read_file", "")


def test_formatter_passes_result_to_current_formatter_contract() -> None:
    observed: list[str | None] = []
    rendered = format_tool_completion(
        "shell",
        {"command": "echo ok"},
        0.2,
        result="ok",
        get_message=lambda name, args, duration, result: (
            observed.append(result) or f"{name}:{duration:.1f}"
        ),
    )
    assert rendered == "✓ shell:0.2"
    assert observed == ["ok"]


def test_diff_lines_are_aligned_but_section_header_is_preserved() -> None:
    lines: list[str] = []
    emit_diff_line(lines.append, "  ┊ review diff")
    emit_diff_line(lines.append, "+added")
    assert lines == ["  ┊ review diff", "  │ +added"]
