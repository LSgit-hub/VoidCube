from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.api_response import (
    TruncationAction,
    decide_truncation_recovery,
    extract_reasoning,
    has_thinking_tags,
    has_visible_content,
    inspect_chat_response,
    normalize_assistant_message,
    strip_thinking_blocks,
    strip_thinking_tags,
    visible_or_reasoning_text,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (None, "response is None"),
        (SimpleNamespace(), "response has no 'choices' attribute"),
        (SimpleNamespace(choices=None), "response.choices is None"),
        (SimpleNamespace(choices=[]), "response.choices is empty"),
        (
            SimpleNamespace(choices=[SimpleNamespace()]),
            "response.choices[0] has no 'message' attribute",
        ),
    ],
)
def test_inspect_chat_response_rejects_malformed_shapes(response, expected_error):
    inspection = inspect_chat_response(response)

    assert inspection.valid is False
    assert inspection.errors == (expected_error,)


def test_inspect_chat_response_returns_valid_first_choice_contract():
    message = SimpleNamespace(content="answer", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")

    inspection = inspect_chat_response(
        SimpleNamespace(choices=[choice], model="safe-model"),
        duration_seconds=12.5,
    )

    assert inspection.valid is True
    assert inspection.choice is choice
    assert inspection.message is message
    assert inspection.finish_reason == "tool_calls"
    assert inspection.provider_name == "model=safe-model"
    assert inspection.failure_hint == "response time 12.5s"


def test_inspect_chat_response_extracts_provider_error_diagnostics():
    response = SimpleNamespace(
        choices=[],
        error={
            "code": "524",
            "message": "upstream timed out",
            "metadata": {"provider_name": "upstream-a"},
        },
    )

    inspection = inspect_chat_response(response, duration_seconds=61.2)

    assert inspection.valid is False
    assert inspection.provider_name == "upstream-a"
    assert inspection.error_code == 524
    assert inspection.failure_hint == (
        "upstream provider timed out (Cloudflare 524, 61s)"
    )


def test_truncation_recovery_ignores_non_length_responses():
    recovery = decide_truncation_recovery(
        SimpleNamespace(content="answer", tool_calls=None),
        "stop",
        text_truncation_count=0,
        tool_truncation_count=0,
    )

    assert recovery.action is TruncationAction.proceed


def test_truncation_recovery_detects_reasoning_only_budget_exhaustion():
    recovery = decide_truncation_recovery(
        SimpleNamespace(content="<think>unfinished plan</think>", tool_calls=None),
        "length",
        text_truncation_count=0,
        tool_truncation_count=0,
    )

    assert recovery.action is TruncationAction.fail_thinking_budget


def test_truncation_recovery_limits_text_continuations():
    message = SimpleNamespace(content="partial", tool_calls=None)

    first = decide_truncation_recovery(
        message,
        "length",
        text_truncation_count=0,
        tool_truncation_count=0,
    )
    final = decide_truncation_recovery(
        message,
        "length",
        text_truncation_count=2,
        tool_truncation_count=0,
    )

    assert first.action is TruncationAction.continue_text
    assert first.text_truncation_count == 1
    assert final.action is TruncationAction.return_partial_text
    assert final.text_truncation_count == 3


def test_truncation_recovery_retries_incomplete_tool_call_once():
    message = SimpleNamespace(content="", tool_calls=[SimpleNamespace()])

    first = decide_truncation_recovery(
        message,
        "length",
        text_truncation_count=0,
        tool_truncation_count=0,
    )
    second = decide_truncation_recovery(
        message,
        "length",
        text_truncation_count=0,
        tool_truncation_count=first.tool_truncation_count,
    )

    assert first.action is TruncationAction.retry_tool_call
    assert first.tool_truncation_count == 1
    assert second.action is TruncationAction.fail_tool_call
    assert second.tool_truncation_count == 2


def test_strip_thinking_blocks_uses_one_case_insensitive_tag_contract():
    content = (
        "<THINK data-kind='internal'>hidden</think>Visible "
        "<reasoning>secret</REASONING><thought>draft</thought>answer"
    )

    assert has_thinking_tags(content) is True
    assert strip_thinking_blocks(content) == "Visible answer"
    assert strip_thinking_tags("<think>keep this</think>") == "keep this"
    assert has_visible_content(content) is True
    assert has_visible_content("<thinking>only reasoning</thinking>") is False


def test_extract_reasoning_prefers_unique_structured_fields():
    message = SimpleNamespace(
        content="<think>inline fallback</think>",
        reasoning=" first ",
        reasoning_content="first",
        reasoning_details=[
            {"summary": "second"},
            {"thinking": "third"},
            {"content": "second"},
        ],
    )

    assert extract_reasoning(message) == "first\n\nsecond\n\nthird"


def test_extract_reasoning_reads_inline_variants_when_structured_fields_are_empty():
    message = SimpleNamespace(
        content=(
            "<thinking>step one</thinking>"
            "<REASONING_SCRATCHPAD>step two</REASONING_SCRATCHPAD>"
        ),
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )

    assert extract_reasoning(message) == "step one\n\nstep two"


def test_visible_or_reasoning_text_prefers_user_visible_content():
    message = SimpleNamespace(
        content="<think>internal</think> final answer ",
        reasoning="structured",
        reasoning_content=None,
        reasoning_details=None,
    )

    assert visible_or_reasoning_text(message) == "final answer"


def test_visible_or_reasoning_text_falls_back_to_reasoning():
    message = SimpleNamespace(
        content="<think>inline</think>",
        reasoning_content=" structured fallback ",
        reasoning=None,
        reasoning_details=None,
    )

    assert visible_or_reasoning_text(message) == "structured fallback"


def test_normalize_assistant_message_preserves_reasoning_details_and_tool_metadata():
    detail = SimpleNamespace(model_dump=lambda: {"type": "summary", "summary": "plan"})
    extra = SimpleNamespace(model_dump=lambda: {"thought_signature": "signed"})
    message = SimpleNamespace(
        content="",
        reasoning="plan",
        reasoning_content=None,
        reasoning_details=[detail],
        tool_calls=[
            SimpleNamespace(
                id=" call_existing ",
                type="function",
                function=SimpleNamespace(name="inspect", arguments='{"path":"."}'),
                extra_content=extra,
            ),
            SimpleNamespace(
                id="",
                type="function",
                function=SimpleNamespace(name="finish", arguments="{}"),
                extra_content=None,
            ),
        ],
    )

    normalized = normalize_assistant_message(
        message,
        "tool_calls",
        tool_call_id_factory=lambda: "call_generated",
    )

    assert normalized["reasoning"] == "plan"
    assert normalized["reasoning_details"] == [
        {"type": "summary", "summary": "plan"}
    ]
    assert normalized["tool_calls"] == [
        {
            "id": "call_existing",
            "type": "function",
            "function": {"name": "inspect", "arguments": '{"path":"."}'},
            "extra_content": {"thought_signature": "signed"},
        },
        {
            "id": "call_generated",
            "type": "function",
            "function": {"name": "finish", "arguments": "{}"},
        },
    ]
