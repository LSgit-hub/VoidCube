from __future__ import annotations

from collections import deque

from VoidCube_cli.input_process_loop import run_input_process_loop, start_input_process_loop


class _Gate:
    def __init__(self, *, locked: bool = False, acquire: bool = True) -> None:
        self.is_locked = locked
        self.acquire_result = acquire
        self.released = 0

    def locked(self) -> bool:
        return self.is_locked

    def acquire(self, blocking: bool = True) -> bool:
        assert blocking is False
        return self.acquire_result

    def release(self) -> None:
        self.released += 1


def test_input_loop_handles_idle_gate_requeue_and_execution_ports() -> None:
    queue = deque(["requeue", "execute"])
    sleeps: list[float] = []
    idle: list[None] = []
    executed: list[object] = []
    errors: list[Exception] = []
    requeued: list[object] = []
    gate = _Gate(acquire=False)

    def get_input(_timeout: float) -> object:
        if queue:
            return queue.popleft()
        raise LookupError()

    def requeue(value: object) -> None:
        requeued.append(value)
        gate.acquire_result = True

    run_input_process_loop(
        stop_requested=lambda: len(executed) == 1 and bool(idle),
        execution_gate=gate,
        get_pending_input=get_input,
        empty_input=LookupError,
        requeue_input=requeue,
        perform_idle_maintenance=lambda: idle.append(None),
        execute_input=executed.append,
        sleep=sleeps.append,
        report_error=errors.append,
    )

    assert requeued == ["requeue"]
    assert executed == ["execute"]
    assert gate.released == 1
    assert sleeps == [0.1]
    assert errors == []


def test_input_loop_waits_while_gate_is_locked() -> None:
    gate = _Gate(locked=True)
    sleeps: list[float] = []

    run_input_process_loop(
        stop_requested=lambda: bool(sleeps),
        execution_gate=gate,
        get_pending_input=lambda _timeout: (_ for _ in ()).throw(AssertionError()),
        empty_input=LookupError,
        requeue_input=lambda _value: None,
        perform_idle_maintenance=lambda: None,
        execute_input=lambda _value: None,
        sleep=sleeps.append,
        report_error=lambda _error: None,
    )

    assert sleeps == [0.1]


def test_start_input_loop_creates_and_starts_a_daemon_thread() -> None:
    captured: dict[str, object] = {}

    class _Thread:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)
            self.started = False

        def start(self) -> None:
            self.started = True

    thread = start_input_process_loop(
        stop_requested=lambda: True, execution_gate=None, get_pending_input=lambda _timeout: None,
        empty_input=LookupError, requeue_input=lambda _value: None,
        perform_idle_maintenance=lambda: None, execute_input=lambda _value: None,
        sleep=lambda _seconds: None, report_error=lambda _error: None, thread_factory=_Thread,  # type: ignore[arg-type]
    )

    assert thread.started is True
    assert captured["daemon"] is True
