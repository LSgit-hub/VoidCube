from __future__ import annotations

import threading

import pytest

from VoidCube_app.contracts.scheduler import (
    SchedulerEventKind,
    SchedulerState,
    TurnLane,
    TurnRequest,
)
from VoidCube_app.turn_scheduler import TurnScheduler


def request(request_id: str, lane: TurnLane) -> TurnRequest:
    return TurnRequest(request_id=request_id, lane=lane, session_id=f"session-{request_id}", prompt="x")


def test_user_turn_is_admitted_before_earlier_autonomous_turn() -> None:
    scheduler = TurnScheduler(clock=lambda: 1.0)
    scheduler.resume_autonomous()
    scheduler.submit(request("auto", TurnLane.SUPERVISOR_TASK))
    scheduler.submit(request("user", TurnLane.USER_CHAT))

    assert scheduler.start_next().request_id == "user"
    assert scheduler.snapshot().queued[0].request_id == "auto"


def test_active_cancel_is_idempotent_and_emits_explicit_events() -> None:
    events = []
    scheduler = TurnScheduler(event_sink=events.append)
    scheduler.submit(request("user", TurnLane.USER_CHAT))
    scheduler.start_next()

    assert scheduler.cancel("user") is True
    assert scheduler.cancel("user") is False
    assert scheduler.snapshot().active.state is SchedulerState.CANCELLING
    assert [event.kind for event in events] == [
        SchedulerEventKind.QUEUED,
        SchedulerEventKind.STARTED,
        SchedulerEventKind.CANCEL_REQUESTED,
    ]

    assert scheduler.cancelled("user", "agent_stopped") is True
    assert scheduler.cancelled("user") is False
    assert scheduler.snapshot().active is None
    assert events[-1].kind is SchedulerEventKind.CANCELLED


def test_pause_autonomous_closes_gate_and_cancels_active_autonomous_turn() -> None:
    scheduler = TurnScheduler(autonomous_gate_active=True)
    scheduler.submit(request("auto", TurnLane.SUPERVISOR_TASK))
    scheduler.submit(request("auto-queued", TurnLane.SUPERVISOR_TASK))
    scheduler.start_next()

    snapshot = scheduler.pause_autonomous()
    assert snapshot.autonomous_gate is False
    assert snapshot.active is not None
    assert snapshot.active.state is SchedulerState.CANCELLING
    assert snapshot.queued == ()
    with pytest.raises(RuntimeError, match="gate is closed"):
        scheduler.submit(request("auto-2", TurnLane.SUPERVISOR_TASK))


def test_queued_cancel_removes_request_without_touching_active_turn() -> None:
    scheduler = TurnScheduler()
    scheduler.submit(request("first", TurnLane.USER_CHAT))
    scheduler.submit(request("second", TurnLane.USER_CHAT))
    scheduler.start_next()

    assert scheduler.cancel("second") is True
    assert scheduler.snapshot().active.request_id == "first"
    assert scheduler.snapshot().queued == ()


def test_dispatch_next_closes_successful_turn_and_passes_token() -> None:
    scheduler = TurnScheduler()
    scheduler.submit(request("user", TurnLane.USER_CHAT))
    observed = []

    dispatched = scheduler.dispatch_next(
        lambda turn, token: observed.append((turn.request_id, token.cancelled))
    )

    assert dispatched.request_id == "user"
    assert observed == [("user", False)]
    assert scheduler.snapshot().active is None
    assert scheduler.drain_events()[-1].kind is SchedulerEventKind.FINISHED


def test_dispatch_next_records_failure_and_reraises() -> None:
    scheduler = TurnScheduler()
    scheduler.submit(request("user", TurnLane.USER_CHAT))

    with pytest.raises(LookupError, match="boom"):
        scheduler.dispatch_next(lambda _turn, _token: (_ for _ in ()).throw(LookupError("boom")))

    event = scheduler.drain_events()[-1]
    assert event.kind is SchedulerEventKind.FAILED
    assert event.state is SchedulerState.FAILED
    assert event.reason == "boom"


def test_dispatch_next_can_be_cancelled_while_executor_runs() -> None:
    scheduler = TurnScheduler()
    scheduler.submit(request("user", TurnLane.USER_CHAT))
    entered = threading.Event()
    release = threading.Event()

    def execute(_turn, token):
        entered.set()
        release.wait(1)
        assert token.cancelled is True

    worker = threading.Thread(target=lambda: scheduler.dispatch_next(execute))
    worker.start()
    assert entered.wait(1)
    assert scheduler.cancel("user") is True
    release.set()
    worker.join(1)

    assert not worker.is_alive()
    assert scheduler.snapshot().active is None
    assert scheduler.drain_events()[-1].kind is SchedulerEventKind.CANCELLED


def test_cancelled_executor_exception_keeps_cancelled_terminal_state() -> None:
    scheduler = TurnScheduler()
    scheduler.submit(request("user", TurnLane.USER_CHAT))
    entered = threading.Event()

    def execute(_turn, token):
        entered.set()
        while not token.cancelled:
            pass
        raise RuntimeError("stopped")

    worker = threading.Thread(target=lambda: scheduler.dispatch_next(execute))
    worker.start()
    assert entered.wait(1)
    assert scheduler.cancel("user") is True
    worker.join(1)
    assert not worker.is_alive()
    assert scheduler.snapshot().active is None
    assert scheduler.drain_events()[-1].kind is SchedulerEventKind.CANCELLED


def test_same_priority_requests_keep_fifo_order() -> None:
    scheduler = TurnScheduler()
    scheduler.submit(request("first", TurnLane.USER_CHAT))
    scheduler.submit(request("second", TurnLane.USER_CHAT))
    assert scheduler.start_next().request_id == "first"


def test_concurrent_submissions_preserve_unique_admission() -> None:
    scheduler = TurnScheduler()
    barrier = threading.Barrier(8)
    errors = []

    def submit(index: int) -> None:
        try:
            barrier.wait(timeout=1)
            scheduler.submit(request(f"request-{index}", TurnLane.USER_CHAT))
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    workers = [threading.Thread(target=submit, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(1)

    assert errors == []
    assert len(scheduler.snapshot().queued) == 8


class _RecordingExecutor:
    def __init__(self) -> None:
        self.cancelled_ids: list[str] = []

    def cancel(self, request_id: str) -> None:
        self.cancelled_ids.append(request_id)


def test_cancel_notifies_injected_executor_once() -> None:
    executor = _RecordingExecutor()
    scheduler = TurnScheduler(executor=executor)
    scheduler.submit(request("user", TurnLane.USER_CHAT))
    scheduler.start_next()
    assert scheduler.cancel("user") is True
    assert scheduler.cancel("user") is False
    assert executor.cancelled_ids == ["user"]


def test_shutdown_cancels_active_and_queued_requests_and_waits_for_drain() -> None:
    executor = _RecordingExecutor()
    scheduler = TurnScheduler(clock=lambda: 1.0, executor=executor)
    entered = threading.Event()

    def execute(_request, token) -> None:
        entered.set()
        assert token._cancel_event.wait(1)

    worker = threading.Thread(
        target=lambda: scheduler.run(request("first", TurnLane.USER_CHAT), execute)
    )
    worker.start()
    assert entered.wait(1)
    scheduler.submit(request("second", TurnLane.USER_CHAT))

    assert scheduler.shutdown(wait_timeout=1) is True
    worker.join(1)

    assert not worker.is_alive()
    assert scheduler.snapshot().active is None
    assert scheduler.snapshot().queued == ()
    assert executor.cancelled_ids == ["first"]
    terminal = {
        event.request_id: event.kind
        for event in scheduler.drain_events()
        if event.kind in {SchedulerEventKind.CANCELLED, SchedulerEventKind.FAILED}
    }
    assert terminal == {
        "first": SchedulerEventKind.CANCELLED,
        "second": SchedulerEventKind.CANCELLED,
    }


def test_shutdown_rejects_new_work_and_autonomous_resume() -> None:
    scheduler = TurnScheduler(autonomous_gate_active=True)

    assert scheduler.shutdown() is True
    with pytest.raises(RuntimeError, match="shut down"):
        scheduler.submit(request("new", TurnLane.USER_CHAT))
    with pytest.raises(RuntimeError, match="shut down"):
        scheduler.resume_autonomous()
