from __future__ import annotations

import pytest

from agent.conversation_runtime import ConversationTurnPorts, ConversationTurnRuntime
from agent.effect_outcomes import EffectOutcome


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _runtime(events):
    def persist(messages, history):
        events.append(("persist", messages, history))
        return EffectOutcome(status="succeeded")

    def cleanup(task_id):
        events.append(("cleanup", task_id))
        return EffectOutcome(status="succeeded")

    return ConversationTurnRuntime(
        ConversationTurnPorts(
            persist_session=persist,
            save_session_log=lambda messages: events.append(("save", messages)),
            cleanup_task_resources=cleanup,
            clear_interrupt=lambda: events.append(("clear_interrupt",)),
            emit_status=lambda message: events.append(("status", message)),
            emit_verbose=lambda message, force: events.append(
                ("verbose", message, force)
            ),
        )
    )


def test_partial_failure_orders_cleanup_before_persistence():
    events = []
    runtime = _runtime(events)
    messages = [{"role": "user", "content": "question"}]
    history = [{"role": "assistant", "content": "earlier"}]

    result = runtime.partial_failure(
        messages=messages,
        conversation_history=history,
        api_call_count=3,
        final_response=None,
        error="truncated",
        cleanup_task_id="task-1",
    )

    assert events == [
        ("cleanup", "task-1"),
        ("persist", messages, history),
    ]
    assert result == {
        "final_response": None,
        "messages": messages,
        "completed": False,
        "api_calls": 3,
        "finalization": {
            "status": "succeeded",
            "cleanup": {
                "status": "succeeded",
                "task_resources": {"status": "succeeded"},
                "response_preview": {
                    "status": "skipped",
                    "details": {"reason": "not_available_in_early_exit"},
                },
                "interrupt": {
                    "status": "skipped",
                    "details": {"reason": "not_requested"},
                },
                "stream_callback": {
                    "status": "skipped",
                    "details": {"reason": "not_available_in_early_exit"},
                },
            },
            "persistence": {"status": "succeeded"},
            "memory_sync": {
                "status": "skipped",
                "details": {"reason": "not_applicable"},
            },
        },
        "partial": True,
        "error": "truncated",
    }


def test_interrupted_result_persists_then_clears_interrupt():
    events = []
    runtime = _runtime(events)
    messages = []

    result = runtime.interrupted_result(
        messages=messages,
        conversation_history=None,
        api_call_count=2,
        final_response="stopped",
    )

    assert events == [
        ("persist", messages, None),
        ("clear_interrupt",),
    ]
    assert result["interrupted"] is True
    assert result["final_response"] == "stopped"
    assert result["finalization"]["status"] == "succeeded"


def test_failed_result_can_skip_persistence_without_an_alternate_writer():
    events = []
    runtime = _runtime(events)

    result = runtime.terminate(
        messages=[],
        conversation_history=None,
        api_call_count=1,
        final_response=None,
        failed=True,
        error="payload too large",
        persist=False,
    )

    assert events == []
    assert result["failed"] is True


def test_terminal_effect_failures_are_isolated_and_reported():
    events = []

    def fail_cleanup(_task_id):
        events.append(("cleanup",))
        raise OSError("cleanup failed")

    def fail_persistence(_messages, _history):
        events.append(("persist",))
        return EffectOutcome(status="failed", error="database failed")

    runtime = ConversationTurnRuntime(
        ConversationTurnPorts(
            persist_session=fail_persistence,
            save_session_log=lambda _messages: None,
            cleanup_task_resources=fail_cleanup,
            clear_interrupt=lambda: events.append(("clear_interrupt",)),
            emit_status=lambda _message: None,
            emit_verbose=lambda _message, _force: None,
        )
    )

    result = runtime.terminate(
        messages=[],
        conversation_history=None,
        api_call_count=1,
        cleanup_task_id="task-1",
        clear_interrupt=True,
    )

    assert events == [("cleanup",), ("persist",), ("clear_interrupt",)]
    assert result["finalization"]["status"] == "degraded"
    assert result["finalization"]["cleanup"]["status"] == "degraded"
    assert (
        result["finalization"]["cleanup"]["task_resources"]["status"]
        == "failed"
    )
    assert result["finalization"]["persistence"]["status"] == "failed"
    assert result["finalization"]["cleanup"]["interrupt"]["status"] == "succeeded"


def test_terminal_effect_contract_violations_are_reported_as_failures():
    runtime = ConversationTurnRuntime(
        ConversationTurnPorts(
            persist_session=lambda _messages, _history: None,
            save_session_log=lambda _messages: None,
            cleanup_task_resources=lambda _task_id: None,
            clear_interrupt=lambda: None,
            emit_status=lambda _message: None,
            emit_verbose=lambda _message, _force: None,
        )
    )

    result = runtime.terminate(
        messages=[],
        conversation_history=None,
        api_call_count=0,
        cleanup_task_id="task-1",
    )

    assert result["finalization"]["status"] == "degraded"
    assert "must return EffectOutcome" in result["finalization"]["persistence"]["error"]
    assert (
        "must return EffectOutcome"
        in result["finalization"]["cleanup"]["task_resources"]["error"]
    )
