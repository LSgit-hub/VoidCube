from __future__ import annotations

from threading import Thread

import pytest

from voidcube.interfaces.cli.turn.execution import TurnExecutionPorts, TurnExecutionRuntime


def test_turn_execution_returns_worker_result():
    runtime = TurnExecutionRuntime(
        TurnExecutionPorts(
            cleanup_async_clients=lambda: None,
            flush_stream=lambda: None,
            flush_output=lambda: None,
            thread_factory=Thread,
        )
    )

    result = runtime.execute(lambda: {"ok": True})

    assert result.result == {"ok": True}
    assert result.error is None


def test_turn_execution_propagates_worker_exception_after_cleanup():
    calls: list[object] = []
    runtime = TurnExecutionRuntime(
        TurnExecutionPorts(
            cleanup_async_clients=lambda: calls.append("cleanup"),
            flush_stream=lambda: calls.append("flush_stream"),
            flush_output=lambda: calls.append("flush_output"),
            sleep=lambda seconds: calls.append(("sleep", seconds)),
            thread_factory=Thread,
        )
    )

    with pytest.raises(RuntimeError, match="boom"):
        runtime.execute(lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    assert calls == [
        "cleanup",
        "flush_stream",
        "flush_output",
        ("sleep", 0.15),
    ]
