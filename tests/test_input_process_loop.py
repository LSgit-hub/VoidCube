from __future__ import annotations

from collections import deque

from voidcube.interfaces.cli.lifecycle.input_loop import run_input_process_loop, start_input_process_loop


def test_input_loop_executes_queued_inputs_without_legacy_gate_requeue() -> None:
    queue = deque(["first", "second"])
    idle: list[None] = []
    executed: list[object] = []

    def get_input(_timeout: float) -> object:
        if queue:
            return queue.popleft()
        raise LookupError()

    run_input_process_loop(
        stop_requested=lambda: len(executed) == 2 and bool(idle),
        get_pending_input=get_input,
        empty_input=LookupError,
        perform_idle_maintenance=lambda: idle.append(None),
        execute_input=executed.append,
        sleep=lambda _seconds: None,
        report_error=lambda error: (_ for _ in ()).throw(error),
    )

    assert executed == ["first", "second"]


def test_start_input_loop_creates_and_starts_a_daemon_thread() -> None:
    captured: dict[str, object] = {}

    class _Thread:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.started = False

        def start(self) -> None:
            self.started = True

    thread = start_input_process_loop(
        stop_requested=lambda: True,
        get_pending_input=lambda _timeout: None,
        empty_input=LookupError,
        perform_idle_maintenance=lambda: None,
        execute_input=lambda _value: None,
        sleep=lambda _seconds: None,
        report_error=lambda _error: None,
        thread_factory=_Thread,  # type: ignore[arg-type]
    )

    assert thread.started is True
    assert captured["daemon"] is True
