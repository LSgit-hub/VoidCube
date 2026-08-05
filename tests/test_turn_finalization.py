from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.conversation_turn import ConversationTurnState
from agent.turn_finalization import (
    TurnFinalizationPorts,
    derive_turn_diagnostics,
    finalize_conversation_turn,
    last_assistant_reasoning,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _owner(events):
    memory = SimpleNamespace(
        sync_all=lambda user, response, session_id="": events.append(
            ("memory_sync", user, response, session_id)
        ),
    )
    owner = SimpleNamespace(
        max_iterations=5,
        model="safe-model",
        provider="test-provider",
        base_url="https://example.test/v1",
        session_id="session-1",
        platform="cli",
        iteration_budget=SimpleNamespace(used=1, max_total=5),
        context_compressor=SimpleNamespace(last_prompt_tokens=321),
        valid_tool_names=["skill_manage"],
        session_input_tokens=10,
        session_output_tokens=20,
        session_cache_read_tokens=3,
        session_cache_write_tokens=4,
        session_reasoning_tokens=5,
        session_prompt_tokens=11,
        session_completion_tokens=22,
        session_total_tokens=33,
        session_estimated_cost_usd=0.25,
        session_cost_status="estimated",
        session_cost_source="pricing",
        _session_persistence=SimpleNamespace(
            persist=lambda messages, history: events.append(
                ("persist", messages, history)
            )
        ),
        _response_was_previewed=True,
        _interrupt_message=None,
        _stream_callback=object(),
        _skill_nudge_interval=2,
        _iters_since_skill=2,
        _memory_manager=memory,
        _cleanup_task_resources=lambda task_id: events.append(
            ("cleanup", task_id)
        ),
        clear_interrupt=lambda: events.append(("clear_interrupt",)),
        _spawn_background_review=lambda **kwargs: events.append(
            ("background", kwargs)
        ),
    )
    return owner


def _ports(owner):
    return TurnFinalizationPorts(
        cleanup_task_resources=owner._cleanup_task_resources,
        persist_session=owner._session_persistence.persist,
        model=owner.model,
        provider=owner.provider,
        base_url=owner.base_url,
        session_id=owner.session_id,
        platform=owner.platform,
        max_iterations=owner.max_iterations,
        iteration_budget=owner.iteration_budget,
        context_compressor=owner.context_compressor,
        valid_tool_names=owner.valid_tool_names,
        usage_snapshot=lambda: {
            "input_tokens": owner.session_input_tokens,
            "output_tokens": owner.session_output_tokens,
            "cache_read_tokens": owner.session_cache_read_tokens,
            "cache_write_tokens": owner.session_cache_write_tokens,
            "reasoning_tokens": owner.session_reasoning_tokens,
            "prompt_tokens": owner.session_prompt_tokens,
            "completion_tokens": owner.session_completion_tokens,
            "total_tokens": owner.session_total_tokens,
            "estimated_cost_usd": owner.session_estimated_cost_usd,
            "cost_status": owner.session_cost_status,
            "cost_source": owner.session_cost_source,
        },
        response_was_previewed=lambda: owner._response_was_previewed,
        clear_response_preview=lambda: setattr(
            owner, "_response_was_previewed", False
        ),
        interrupt_message=lambda: owner._interrupt_message,
        clear_interrupt=owner.clear_interrupt,
        clear_stream_callback=lambda: setattr(owner, "_stream_callback", None),
        skill_nudge_interval=owner._skill_nudge_interval,
        iterations_since_skill=lambda: owner._iters_since_skill,
        clear_skill_nudge=lambda: setattr(owner, "_iters_since_skill", 0),
        sync_memory=lambda user, response, session_id: owner._memory_manager.sync_all(
            user,
            response,
            session_id=session_id,
        ),
        spawn_background_review=owner._spawn_background_review,
    )


def test_turn_diagnostics_identifies_pending_tool_result():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"function": {"name": "read_file"}},
                {"function": {"name": "terminal"}},
            ],
        },
        {"role": "tool", "content": "done"},
    ]
    state = ConversationTurnState(
        api_call_count=2,
        exit_reason="budget_exhausted",
    )

    diagnostics = derive_turn_diagnostics(
        messages,
        state,
        model="safe-model",
        max_iterations=5,
        budget_used=2,
        budget_max=5,
        session_id="session-1",
    )

    assert diagnostics.pending_tool_result is True
    assert diagnostics.last_tool_name == "terminal"
    assert diagnostics.tool_turn_count == 1


def test_last_assistant_reasoning_uses_latest_reasoning_message():
    messages = [
        {"role": "assistant", "reasoning": "old"},
        {"role": "user", "content": "next"},
        {"role": "assistant", "reasoning": "latest"},
    ]

    assert last_assistant_reasoning(messages) == "latest"


def test_finalizer_runs_one_ordered_success_sequence():
    events = []
    owner = _owner(events)
    messages = [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "answer",
            "reasoning": "reason",
        },
    ]
    state = ConversationTurnState(
        api_call_count=1,
        final_response="answer",
        exit_reason="text_response",
    )

    def hook(name, **kwargs):
        events.append(("hook", name, kwargs))

    result = finalize_conversation_turn(
        _ports(owner),
        state=state,
        messages=messages,
        conversation_history=None,
        task_id="task-1",
        original_user_message="question",
        invoke_hook=hook,
    )

    assert [event[0] for event in events] == [
        "cleanup",
        "persist",
        "hook",
        "clear_interrupt",
        "memory_sync",
        "background",
        "hook",
    ]
    assert events[2][1] == "post_llm_call"
    assert events[4] == ("memory_sync", "question", "answer", "session-1")
    assert events[-1][1] == "on_session_end"
    assert result["final_response"] == "answer"
    assert result["last_reasoning"] == "reason"
    assert result["completed"] is True
    assert result["response_previewed"] is True
    assert result["last_prompt_tokens"] == 321
    assert owner._response_was_previewed is False
    assert owner._stream_callback is None
    assert owner._iters_since_skill == 0


def test_interrupted_finalization_skips_success_side_effects():
    events = []
    owner = _owner(events)
    owner._interrupt_message = "new request"
    state = ConversationTurnState(
        api_call_count=1,
        final_response="partial",
        interrupted=True,
        exit_reason="interrupted",
    )

    result = finalize_conversation_turn(
        _ports(owner),
        state=state,
        messages=[{"role": "assistant", "content": "partial"}],
        conversation_history=None,
        task_id="task-1",
        original_user_message="question",
        invoke_hook=lambda name, **kwargs: events.append(("hook", name, kwargs)),
    )

    assert result["completed"] is False
    assert result["interrupt_message"] == "new request"
    assert not any(event[0] == "memory_sync" for event in events)
    assert not any(event[0] == "background" for event in events)
    assert [event[1] for event in events if event[0] == "hook"] == [
        "on_session_end"
    ]
