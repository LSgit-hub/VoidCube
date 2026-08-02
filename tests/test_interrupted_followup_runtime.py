from __future__ import annotations

from VoidCube_app.turn_queue import InterruptedInputBatch
from VoidCube_cli.interrupted_followup_runtime import (
    InterruptedFollowupPorts,
    InterruptedFollowupRuntime,
)


def test_interrupted_followup_runtime_requeues_and_announces_batch():
    calls = []
    runtime = InterruptedFollowupRuntime(
        InterruptedFollowupPorts(
            has_queue=lambda: True,
            requeue=lambda payload: (
                calls.append(("requeue", payload))
                or InterruptedInputBatch(
                    payloads=(payload, "second"),
                    pending_payloads=("combined",),
                )
            ),
            emit=lambda message: calls.append(("emit", message)),
        )
    )

    assert runtime.requeue("first") is True

    assert calls[0] == ("requeue", "first")
    assert "Sending 2 messages" in calls[1][1]


def test_interrupted_followup_runtime_ignores_missing_queue_or_message():
    calls = []
    runtime = InterruptedFollowupRuntime(
        InterruptedFollowupPorts(
            has_queue=lambda: False,
            requeue=lambda _payload: calls.append("unexpected"),
            emit=lambda message: calls.append(message),
        )
    )

    assert runtime.requeue("ignored") is False
    assert calls == []
