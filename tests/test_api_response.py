from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.api_response import (
    extract_reasoning,
    has_thinking_tags,
    has_visible_content,
    normalize_assistant_message,
    strip_thinking_blocks,
    strip_thinking_tags,
    visible_or_reasoning_text,
)


pytestmark = pytest.mark.unit


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
