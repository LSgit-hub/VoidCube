from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.conversation_turn import ConversationTurnState
from agent.effect_outcomes import EffectOutcome
from agent.turn_finalization import (
    TurnFinalizationPorts,
    derive_turn_diagnostics,
    finalize_conversation_turn,
    last_assistant_reasoning,
)
from run_agent import AIAgent


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _owner(events):
    def sync_memory(user, response, session_id=""):
        events.append(("memory_sync", user, response, session_id))
        return EffectOutcome(
            status="queued",
            details={"write_id": "write-1", "durable_outbox": True},
        )

    def persist(messages, history):
        events.append(("persist", messages, history))
        return EffectOutcome(status="succeeded")

    def cleanup(task_id):
        events.append(("cleanup", task_id))
        return EffectOutcome(status="succeeded")

    memory = SimpleNamespace(
        sync_turn=sync_memory,
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
            persist=persist,
        ),
        _response_was_previewed=True,
        _interrupt_message=None,
        _stream_callback=object(),
        _skill_nudge_interval=2,
        _iters_since_skill=2,
        _memory_manager=memory,
        _cleanup_task_resources=cleanup,
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
        sync_memory=lambda user, response, session_id: owner._memory_manager.sync_turn(
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
    assert result["finalization"]["status"] == "succeeded"
    assert result["finalization"]["persistence"]["status"] == "succeeded"
    assert result["finalization"]["memory_sync"]["status"] == "queued"


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
    assert result["finalization"]["memory_sync"] == {
        "status": "skipped",
        "details": {"reason": "not_applicable"},
    }


def test_finalizer_reports_cleanup_failure_without_losing_persistence_or_result():
    events = []
    owner = _owner(events)

    def fail_cleanup(_task_id):
        events.append(("cleanup_failed",))
        raise OSError("browser busy")

    owner._cleanup_task_resources = fail_cleanup
    state = ConversationTurnState(
        api_call_count=1,
        final_response="answer",
        exit_reason="text_response",
    )

    result = finalize_conversation_turn(
        _ports(owner),
        state=state,
        messages=[{"role": "assistant", "content": "answer"}],
        conversation_history=None,
        task_id="task-1",
        original_user_message="question",
        invoke_hook=lambda *_args, **_kwargs: None,
    )

    assert result["completed"] is True
    assert any(event[0] == "persist" for event in events)
    assert any(event[0] == "clear_interrupt" for event in events)
    assert result["finalization"]["status"] == "degraded"
    assert result["finalization"]["cleanup"]["task_resources"]["status"] == "failed"


def test_finalizer_reports_persistence_failure_without_reclassifying_completed_turn():
    events = []
    owner = _owner(events)

    def fail_persistence(messages, history):
        events.append(("persist", messages, history))
        return EffectOutcome(status="failed", error="database unavailable")

    owner._session_persistence.persist = fail_persistence
    state = ConversationTurnState(
        api_call_count=1,
        final_response="answer",
        exit_reason="text_response",
    )

    result = finalize_conversation_turn(
        _ports(owner),
        state=state,
        messages=[{"role": "assistant", "content": "answer"}],
        conversation_history=None,
        task_id="task-1",
        original_user_message="question",
        invoke_hook=lambda *_args, **_kwargs: None,
    )

    assert result["completed"] is True
    assert result["finalization"]["status"] == "degraded"
    assert result["finalization"]["persistence"] == {
        "status": "failed",
        "error": "database unavailable",
    }
    assert result["finalization"]["memory_sync"]["status"] == "queued"


def test_finalizer_reports_memory_enqueue_failure_without_reclassifying_completed_turn():
    events = []
    owner = _owner(events)
    owner._memory_manager.sync_turn = lambda *_args, **_kwargs: EffectOutcome(
        status="failed",
        error="outbox unavailable",
    )
    state = ConversationTurnState(
        api_call_count=1,
        final_response="answer",
        exit_reason="text_response",
    )

    result = finalize_conversation_turn(
        _ports(owner),
        state=state,
        messages=[{"role": "assistant", "content": "answer"}],
        conversation_history=None,
        task_id="task-1",
        original_user_message="question",
        invoke_hook=lambda *_args, **_kwargs: None,
    )

    assert result["completed"] is True
    assert result["finalization"]["status"] == "degraded"
    assert result["finalization"]["memory_sync"] == {
        "status": "failed",
        "error": "outbox unavailable",
    }


def test_agent_resource_cleanup_reports_individual_backend_failure(monkeypatch):
    agent = AIAgent.__new__(AIAgent)
    agent.verbose_logging = False
    browser_calls = []

    monkeypatch.setattr("tools.terminal_tool.is_persistent_env", lambda _task_id: False)
    monkeypatch.setattr(
        "tools.terminal_tool.cleanup_vm",
        lambda _task_id: (_ for _ in ()).throw(OSError("terminal busy")),
    )
    monkeypatch.setattr(
        "tools.browser_tool.cleanup_browser",
        lambda task_id: browser_calls.append(task_id),
    )

    outcome = agent._cleanup_task_resources("task-1")

    assert browser_calls == ["task-1"]
    assert outcome.status == "degraded"
    assert outcome.details["terminal"]["status"] == "failed"
    assert outcome.details["browser"]["status"] == "succeeded"


def test_agent_resource_cleanup_preserves_executor_owned_task_scope(monkeypatch):
    agent = AIAgent.__new__(AIAgent)
    agent.verbose_logging = False
    terminal_calls = []
    browser_calls = []

    monkeypatch.setattr("tools.terminal_tool.is_persistent_env", lambda _task_id: False)
    monkeypatch.setattr(
        "tools.terminal_tool.cleanup_vm",
        lambda task_id: terminal_calls.append(task_id),
    )
    monkeypatch.setattr(
        "tools.task_execution.get_task_execution_contract",
        lambda _task_id: SimpleNamespace(lifecycle_owner="executor"),
    )
    monkeypatch.setattr(
        "tools.browser_tool.cleanup_browser",
        lambda task_id: browser_calls.append(task_id),
    )

    outcome = agent._cleanup_task_resources("candidate-task")

    assert terminal_calls == []
    assert browser_calls == ["candidate-task"]
    assert outcome.status == "succeeded"
    assert outcome.details["terminal"] == {
        "status": "skipped",
        "details": {"reason": "executor_owned_environment"},
    }
