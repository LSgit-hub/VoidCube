from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.response_disposition import (
    TextResponseAction,
    decide_text_response_disposition,
    inspect_tool_calls,
    normalize_assistant_content,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _tool_call(name: str, arguments):
    return SimpleNamespace(
        id=f"call-{name}",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"text": "hello"}, "hello"),
        ({"content": "world"}, "world"),
        (
            ["first", {"type": "text", "text": "second"}, {"text": 3}],
            "first\nsecond\n3",
        ),
        (42, "42"),
    ],
)
def test_normalize_assistant_content_handles_compatible_variants(
    content,
    expected,
):
    assert normalize_assistant_content(content) == expected


def test_tool_inspection_repairs_names_and_normalizes_arguments():
    calls = [
        _tool_call("readfile", {"path": "a.txt"}),
        _tool_call("terminal", ""),
    ]

    inspection = inspect_tool_calls(
        calls,
        valid_tool_names={"read_file", "terminal"},
        repair_tool_name=lambda name: "read_file" if name == "readfile" else None,
    )

    assert [(repair.original, repair.repaired) for repair in inspection.repairs] == [
        ("readfile", "read_file")
    ]
    assert calls[0].function.arguments == '{"path": "a.txt"}'
    assert calls[1].function.arguments == "{}"
    assert inspection.invalid_tool_names == ()
    assert inspection.invalid_json_arguments == ()


def test_tool_inspection_stops_at_unknown_tool_names():
    call = _tool_call("missing", "not-json")

    inspection = inspect_tool_calls(
        [call],
        valid_tool_names={"terminal"},
        repair_tool_name=lambda _name: None,
    )

    assert inspection.invalid_tool_names == ("missing",)
    assert inspection.invalid_json_arguments == ()
    assert call.function.arguments == "not-json"


def test_tool_inspection_distinguishes_truncated_invalid_json():
    call = _tool_call("terminal", '{"command": "echo')

    inspection = inspect_tool_calls(
        [call],
        valid_tool_names={"terminal"},
        repair_tool_name=lambda _name: None,
    )

    assert inspection.invalid_json_arguments[0][0] == "terminal"
    assert inspection.truncated_json_arguments is True


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"content": "answer"}, TextResponseAction.final_text),
        (
            {"content": "", "prior_content_available": True},
            TextResponseAction.use_prior_content,
        ),
        (
            {"content": "", "structured_reasoning": True},
            TextResponseAction.prefill_reasoning,
        ),
        ({"content": ""}, TextResponseAction.retry_empty),
        (
            {"content": "", "empty_content_retries": 3, "fallback_available": True},
            TextResponseAction.try_fallback,
        ),
        (
            {"content": "", "empty_content_retries": 3},
            TextResponseAction.terminal_empty,
        ),
    ],
)
def test_text_response_disposition_selects_one_loop_action(options, expected):
    values = {
        "content": "",
        "structured_reasoning": False,
        "thinking_prefill_retries": 0,
        "empty_content_retries": 0,
        "prior_content_available": False,
        "fallback_available": False,
    }
    values.update(options)

    disposition = decide_text_response_disposition(**values)

    assert disposition.action is expected
