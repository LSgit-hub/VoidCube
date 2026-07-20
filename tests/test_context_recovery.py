from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.context_compressor import (
    CompressionRecoveryResult,
    apply_context_recovery_plan,
    build_context_recovery_plan,
    next_compression_attempt,
)
from run_agent import AIAgent


pytestmark = pytest.mark.unit


def test_next_compression_attempt_has_one_explicit_exhaustion_rule():
    assert next_compression_attempt(1, 3).exhausted is False
    assert next_compression_attempt(3, 3).exhausted is True


def test_context_recovery_plan_reduces_only_output_cap_when_space_is_reported():
    plan = build_context_recovery_plan(
        "max_tokens: 32768 > context_window: 200000 - input_tokens: 190000 "
        "= available_tokens: 10000",
        current_context_length=200000,
        previous_attempts=0,
        max_attempts=3,
    )

    assert plan.output_token_limit == 9936
    assert plan.available_output_tokens == 10000
    assert plan.next_context_length is None
    assert plan.context_length_changed is False


def test_context_recovery_plan_keeps_positive_output_limit_after_margin():
    plan = build_context_recovery_plan(
        "max_tokens: 100 > context_window: 1000 = available_tokens: 10",
        current_context_length=1000,
        previous_attempts=0,
        max_attempts=3,
    )

    assert plan.output_token_limit == 1


def test_context_recovery_plan_prefers_reported_context_limit():
    plan = build_context_recovery_plan(
        "maximum context length is 65536 tokens",
        current_context_length=128000,
        previous_attempts=1,
        max_attempts=3,
    )

    assert plan.next_context_length == 65536
    assert plan.parsed_context_limit is True
    assert plan.context_length_changed is True
    assert plan.attempt.number == 2


def test_context_recovery_plan_falls_back_to_next_probe_tier():
    plan = build_context_recovery_plan(
        "prompt is too long",
        current_context_length=200000,
        previous_attempts=0,
        max_attempts=3,
    )

    assert plan.next_context_length == 128000
    assert plan.parsed_context_limit is False


def test_context_recovery_plan_reports_no_lower_tier_at_minimum():
    plan = build_context_recovery_plan(
        "prompt is too long",
        current_context_length=8000,
        previous_attempts=0,
        max_attempts=3,
    )

    assert plan.next_context_length is None
    assert plan.context_length_changed is False


def test_apply_context_recovery_plan_updates_compressor_probe_state():
    updates = []
    compressor = SimpleNamespace(
        _context_probed=False,
        _context_probe_persistable=False,
        update_model=lambda **kwargs: updates.append(kwargs),
    )
    plan = build_context_recovery_plan(
        "maximum context length is 65536 tokens",
        current_context_length=128000,
        previous_attempts=0,
        max_attempts=3,
    )

    changed = apply_context_recovery_plan(
        compressor,
        plan,
        model="test-model",
        base_url="https://example.test/v1",
        api_key="secret",
        provider="test",
    )

    assert changed is True
    assert updates == [
        {
            "model": "test-model",
            "context_length": 65536,
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "provider": "test",
        }
    ]
    assert compressor._context_probed is True
    assert compressor._context_probe_persistable is True


def test_apply_context_recovery_plan_rejects_exhausted_plan():
    updates = []
    compressor = SimpleNamespace(
        update_model=lambda **kwargs: updates.append(kwargs),
    )
    plan = build_context_recovery_plan(
        "maximum context length is 65536 tokens",
        current_context_length=128000,
        previous_attempts=3,
        max_attempts=3,
    )

    changed = apply_context_recovery_plan(
        compressor,
        plan,
        model="test-model",
        base_url="https://example.test/v1",
        api_key="secret",
        provider="test",
    )

    assert plan.attempt.exhausted is True
    assert changed is False
    assert updates == []


def test_compression_recovery_result_accepts_either_kind_of_progress():
    unchanged_messages = [{"role": "user", "content": "same"}]
    context_progress = CompressionRecoveryResult(
        messages=unchanged_messages,
        system_prompt="policy",
        original_message_count=1,
        context_length_changed=True,
    )
    message_progress = CompressionRecoveryResult(
        messages=[],
        system_prompt="policy",
        original_message_count=1,
    )

    assert context_progress.message_count_reduced is False
    assert context_progress.made_progress is True
    assert message_progress.message_count_reduced is True
    assert message_progress.made_progress is True


def test_agent_compression_recovery_returns_named_result():
    agent = AIAgent.__new__(AIAgent)
    calls = []
    agent._compress_context = lambda messages, system, **kwargs: (
        calls.append((messages, system, kwargs)) or ([messages[-1]], "new policy")
    )
    messages = [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "current"},
    ]

    result = agent._compress_for_api_recovery(
        messages,
        "policy",
        approx_tokens=1000,
        task_id="task-1",
    )

    assert result.messages == [messages[-1]]
    assert result.system_prompt == "new policy"
    assert result.original_message_count == 2
    assert result.made_progress is True
    assert calls == [
        (
            messages,
            "policy",
            {"approx_tokens": 1000, "task_id": "task-1"},
        )
    ]


def test_agent_context_recovery_failure_persists_once_and_is_partial():
    agent = AIAgent.__new__(AIAgent)
    persisted = []
    agent._persist_session = lambda messages, history: persisted.append(
        (messages, history)
    )
    messages = [{"role": "user", "content": "current"}]
    history = [{"role": "user", "content": "old"}]

    result = agent._context_recovery_failure_result(
        messages=messages,
        conversation_history=history,
        api_call_count=4,
        error="cannot compress",
    )

    assert persisted == [(messages, history)]
    assert result == {
        "messages": messages,
        "completed": False,
        "api_calls": 4,
        "error": "cannot compress",
        "partial": True,
    }
