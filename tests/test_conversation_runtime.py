from __future__ import annotations

import pytest

from agent.conversation_runtime import ConversationTurnPorts, ConversationTurnRuntime


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _runtime(events):
    return ConversationTurnRuntime(
        ConversationTurnPorts(
            persist_session=lambda messages, history: events.append(
                ("persist", messages, history)
            ),
            save_session_log=lambda messages: events.append(("save", messages)),
            cleanup_task_resources=lambda task_id: events.append(
                ("cleanup", task_id)
            ),
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
