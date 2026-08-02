from __future__ import annotations

from VoidCube_cli.turn_result_application_runtime import (
    TurnResultApplicationPorts,
    TurnResultApplicationRuntime,
)


def _runtime(history, calls):
    return TurnResultApplicationRuntime(
        TurnResultApplicationPorts(
            conversation_history=lambda: history,
            set_conversation_history=lambda value: history.__setitem__(slice(None), value),
            record_autonomous_result=lambda result, **kwargs: calls.append(
                ("result", result, kwargs)
            ),
            record_autonomous_finished=lambda result, **kwargs: calls.append(
                ("finished", result, kwargs)
            ),
        )
    )


def test_result_application_runtime_writes_normalized_history():
    history = [{"role": "user", "content": "old"}]
    calls = []

    applied = _runtime(history, calls).apply(
        {
            "final_response": "answer",
            "messages": [
                {"role": "user", "content": "new"},
                {"role": "assistant", "content": "answer"},
            ],
        },
        autonomous_task_run_id="",
        autonomous_timeout_reported=False,
        autonomous_timeout_writeback_succeeded=False,
    )

    assert applied.outcome.response == "answer"
    assert history == [
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "answer"},
    ]
    assert [call[0] for call in calls] == ["result", "finished"]


def test_result_application_runtime_marks_timeout_before_autonomous_writeback():
    history = []
    calls = []

    applied = _runtime(history, calls).apply(
        {"final_response": "partial"},
        autonomous_task_run_id="run-1",
        autonomous_timeout_reported=True,
        autonomous_timeout_writeback_succeeded=False,
    )

    assert applied.turn_result["failed"] is True
    assert applied.turn_result["interrupted"] is True
    assert calls[0][2]["autonomous_task_run_id"] == "run-1"
