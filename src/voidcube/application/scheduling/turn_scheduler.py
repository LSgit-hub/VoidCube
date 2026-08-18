"""UI-independent turn admission and lifecycle state machine."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Condition, Event, RLock
from time import monotonic
from typing import Any, Callable, Protocol

from ...domain.contracts.scheduler import (
    SchedulerEvent,
    SchedulerEventKind,
    SchedulerSnapshot,
    SchedulerState,
    TurnLane,
    TurnRequest,
)


class CancellationToken:
    """Small cancellation primitive passed to an executor for one turn."""

    def __init__(self) -> None:
        self._cancel_event = Event()

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def cancel(self) -> bool:
        if self._cancel_event.is_set():
            return False
        self._cancel_event.set()
        return True


class TurnExecutor(Protocol):
    def execute(self, request: TurnRequest, cancellation: CancellationToken) -> Any: ...

    def cancel(self, request_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _QueuedTurn:
    request: TurnRequest
    sequence: int


class TurnScheduler:
    """Owns all admission state for user and autonomous turns.

    The scheduler deliberately does not create threads or call model APIs. An
    adapter admits requests, calls :meth:`start_next`, and reports completion
    through :meth:`finish` or :meth:`fail`. This keeps execution and display
    concerns outside the shared application layer.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        event_sink: Callable[[SchedulerEvent], None] | None = None,
        executor: TurnExecutor | None = None,
        autonomous_gate_active: bool = False,
    ) -> None:
        self._clock = clock or monotonic
        self._event_sink = event_sink
        self._executor = executor
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._queue: list[_QueuedTurn] = []
        self._active: TurnRequest | None = None
        self._active_token: CancellationToken | None = None
        self._active_state = SchedulerState.IDLE
        self._sequence = 0
        self._autonomous_gate = bool(autonomous_gate_active)
        self._blocked_reason = ""
        self._closed = False
        self._events: deque[SchedulerEvent] = deque()

    def submit(self, request: TurnRequest) -> SchedulerSnapshot:
        """Admit a request, rejecting new autonomous work while gated."""
        if not isinstance(request, TurnRequest):
            raise TypeError("request must be a TurnRequest")
        with self._lock:
            if self._closed:
                raise RuntimeError("scheduler is shut down")
            if request.lane is TurnLane.SUPERVISOR_TASK and not self._autonomous_gate:
                self._blocked_reason = "autonomous_gate_closed"
                self._emit(
                    SchedulerEvent(
                        kind=SchedulerEventKind.WAITING,
                        request_id=request.request_id,
                        lane=request.lane,
                        state=SchedulerState.CANCELLED,
                        timestamp=self._now(),
                        reason="rejected",
                        blocked_reason=self._blocked_reason,
                        autonomous_gate=self._autonomous_gate,
                    )
                )
                raise RuntimeError("autonomous gate is closed")
            if request.request_id == (self._active.request_id if self._active else None) or any(
                item.request.request_id == request.request_id for item in self._queue
            ):
                raise ValueError(f"duplicate request_id: {request.request_id}")
            self._sequence += 1
            self._queue.append(_QueuedTurn(request, self._sequence))
            self._queue.sort(key=lambda item: (-int(item.request.priority or 0), item.sequence))
            self._emit(
                SchedulerEvent(
                    kind=SchedulerEventKind.QUEUED,
                    request_id=request.request_id,
                    lane=request.lane,
                    state=SchedulerState.QUEUED,
                    timestamp=self._now(),
                    autonomous_gate=self._autonomous_gate,
                )
            )
            return self._snapshot_locked()

    @property
    def executor(self) -> TurnExecutor | None:
        return self._executor

    def start_next(self) -> TurnRequest | None:
        """Move the highest-priority queued request to the active slot."""
        with self._condition:
            request = self._start_next_locked()
            self._condition.notify_all()
            return request

    def run(
        self,
        request: TurnRequest,
        execute: Callable[[TurnRequest, CancellationToken], Any],
        *,
        wait_timeout: float | None = None,
    ) -> bool:
        """Submit and synchronously run one request when its turn is admitted.

        Multiple producer threads may call this method. They block only while
        another turn owns the active slot; the callback itself always runs
        without the scheduler lock. ``False`` means the request was cancelled
        while queued (or never became admissible).
        """
        self.submit(request)
        return self.run_admitted(request, execute, wait_timeout=wait_timeout)

    def run_admitted(
        self,
        request: TurnRequest,
        execute: Callable[[TurnRequest, CancellationToken], Any],
        *,
        wait_timeout: float | None = None,
    ) -> bool:
        """Run a request that has already been admitted to the queue.

        Keeping admission separate lets adapters assign queue order on their
        caller thread while execution remains asynchronous.
        """
        with self._condition:
            while self._active is None or self._active.request_id != request.request_id:
                if not any(item.request.request_id == request.request_id for item in self._queue):
                    return False
                if self._active is None:
                    self._start_next_locked()
                    self._condition.notify_all()
                    continue
                if not self._condition.wait(timeout=wait_timeout):
                    return False
            token = self._active_token
        if token is None:
            self.fail(request.request_id, "missing_cancellation_token")
            raise RuntimeError("active turn has no cancellation token")
        try:
            execute(request, token)
        except Exception as exc:
            if token.cancelled:
                self.cancelled(request.request_id, f"executor_cancelled: {exc}")
            else:
                self.fail(request.request_id, str(exc))
            raise
        if token.cancelled:
            self.cancelled(request.request_id, "executor_observed_cancel")
        else:
            self.finish(request.request_id)
        return True

    def shutdown(self, reason: str = "shutdown", *, wait_timeout: float = 5.0) -> bool:
        """Stop accepting work, cancel queued/active requests, and drain.

        The scheduler remains the sole owner of terminal state. A ``False``
        return means an executor did not observe cancellation before the
        deadline; the active request is intentionally retained for its worker
        to close later.
        """
        active_id: str | None = None
        with self._condition:
            self._closed = True
            self._autonomous_gate = False
            self._blocked_reason = str(reason or "shutdown")
            queued = tuple(self._queue)
            self._queue.clear()
            for item in queued:
                self._emit(
                    SchedulerEvent(
                        kind=SchedulerEventKind.CANCELLED,
                        request_id=item.request.request_id,
                        lane=item.request.lane,
                        state=SchedulerState.CANCELLED,
                        timestamp=self._now(),
                        reason=self._blocked_reason,
                    )
                )
            if self._active is not None:
                active_id = self._active.request_id
            self._condition.notify_all()
        if active_id is not None:
            self.cancel(active_id)
        deadline = monotonic() + max(0.0, float(wait_timeout))
        with self._condition:
            while self._active is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)
        return True

    def dispatch_next(
        self,
        execute: Callable[[TurnRequest, CancellationToken], Any],
    ) -> TurnRequest | None:
        """Execute one admitted turn and close its scheduler lifecycle.

        The callback runs outside the scheduler lock, so another thread can
        issue ``cancel`` while model/tool work is in progress. Callback errors
        are recorded as a failed event and then re-raised to the caller. A
        callback that observes cancellation (or is cancelled concurrently) is
        closed as ``cancelled``.
        """
        request = self.start_next()
        if request is None:
            return None
        token = self.active_cancellation()
        if token is None:
            self.fail(request.request_id, "missing_cancellation_token")
            raise RuntimeError("active turn has no cancellation token")
        try:
            execute(request, token)
        except Exception as exc:
            if token.cancelled:
                self.cancelled(request.request_id, f"executor_cancelled: {exc}")
            else:
                self.fail(request.request_id, str(exc))
            raise
        if token.cancelled:
            self.cancelled(request.request_id, "executor_observed_cancel")
        else:
            self.finish(request.request_id)
        return request

    def active_cancellation(self) -> CancellationToken | None:
        with self._lock:
            return self._active_token

    def cancel(self, request_id: str) -> bool:
        """Cancel an active or queued request; repeated calls are idempotent."""
        request_id = str(request_id or "").strip()
        if not request_id:
            return False
        with self._condition:
            for index, item in enumerate(self._queue):
                if item.request.request_id == request_id:
                    self._queue.pop(index)
                    self._emit(
                        SchedulerEvent(
                            kind=SchedulerEventKind.CANCELLED,
                            request_id=request_id,
                            lane=item.request.lane,
                            state=SchedulerState.CANCELLED,
                            timestamp=self._now(),
                            reason="queued_cancelled",
                        )
                    )
                    self._condition.notify_all()
                    return True
            if self._active is None or self._active.request_id != request_id:
                return False
            if self._active_state in {SchedulerState.CANCELLING, SchedulerState.CANCELLED}:
                return False
            self._active_state = SchedulerState.CANCELLING
            if self._active_token is not None:
                self._active_token.cancel()
            self._emit(
                SchedulerEvent(
                    kind=SchedulerEventKind.CANCEL_REQUESTED,
                    request_id=request_id,
                    lane=self._active.lane,
                    state=SchedulerState.CANCELLING,
                    timestamp=self._now(),
                    reason="cancel_requested",
                )
            )
            self._condition.notify_all()
            executor = self._executor
        if executor is not None:
            try:
                executor.cancel(request_id)
            except Exception as exc:
                with self._condition:
                    if self._active is not None and self._active.request_id == request_id:
                        self._emit(
                            SchedulerEvent(
                                kind=SchedulerEventKind.FAILED,
                                request_id=request_id,
                                lane=self._active.lane,
                                state=SchedulerState.CANCELLING,
                                timestamp=self._now(),
                                reason=f"cancel_executor_failed: {exc}",
                            )
                        )
        return True

    def finish(self, request_id: str) -> bool:
        return self._complete(request_id, SchedulerState.FINISHED, SchedulerEventKind.FINISHED)

    def fail(self, request_id: str, reason: str = "") -> bool:
        return self._complete(request_id, SchedulerState.FAILED, SchedulerEventKind.FAILED, reason)

    def cancelled(self, request_id: str, reason: str = "") -> bool:
        return self._complete(request_id, SchedulerState.CANCELLED, SchedulerEventKind.CANCELLED, reason)

    def pause_autonomous(self, reason: str = "auto-q") -> SchedulerSnapshot:
        active_id: str | None = None
        with self._condition:
            self._autonomous_gate = False
            self._blocked_reason = reason
            retained: list[_QueuedTurn] = []
            for item in self._queue:
                if item.request.lane is TurnLane.SUPERVISOR_TASK:
                    self._emit(
                        SchedulerEvent(
                            kind=SchedulerEventKind.CANCELLED,
                            request_id=item.request.request_id,
                            lane=item.request.lane,
                            state=SchedulerState.CANCELLED,
                            timestamp=self._now(),
                            reason=reason,
                            blocked_reason=self._blocked_reason,
                            autonomous_gate=self._autonomous_gate,
                        )
                    )
                else:
                    retained.append(item)
            self._queue = retained
            if self._active is not None and self._active.lane is TurnLane.SUPERVISOR_TASK:
                active_id = self._active.request_id
            self._emit_gate_changed(reason)
        if active_id is not None:
            self.cancel(active_id)
        return self.snapshot()

    def resume_autonomous(self, reason: str = "auto") -> SchedulerSnapshot:
        with self._condition:
            if self._closed:
                raise RuntimeError("scheduler is shut down")
            self._autonomous_gate = True
            self._blocked_reason = ""
            self._emit_gate_changed(reason)
            self._condition.notify_all()
            return self._snapshot_locked()

    def snapshot(self) -> SchedulerSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def set_executor(self, executor: TurnExecutor) -> None:
        """Bind the cancellation port before production dispatch starts."""
        with self._lock:
            if self._active is not None or self._queue:
                raise RuntimeError("cannot replace executor while turns are pending")
            self._executor = executor

    def drain_events(self) -> tuple[SchedulerEvent, ...]:
        with self._lock:
            events = tuple(self._events)
            self._events.clear()
            return events

    def _complete(
        self,
        request_id: str,
        state: SchedulerState,
        kind: SchedulerEventKind,
        reason: str = "",
    ) -> bool:
        with self._lock:
            if self._active is None or self._active.request_id != request_id:
                return False
            request = self._active
            self._active = None
            self._active_token = None
            self._active_state = SchedulerState.IDLE
            self._emit(
                SchedulerEvent(
                    kind=kind,
                    request_id=request_id,
                    lane=request.lane,
                    state=state,
                    timestamp=self._now(),
                    reason=reason,
                )
            )
            self._condition.notify_all()
            return True

    def _snapshot_locked(self) -> SchedulerSnapshot:
        active = (
            self._active.summary(state=self._active_state) if self._active is not None else None
        )
        queued = tuple(item.request.summary() for item in self._queue)
        return SchedulerSnapshot(
            active=active,
            queued=queued,
            autonomous_gate=self._autonomous_gate,
            blocked_reason=self._blocked_reason,
            updated_at=self._now(),
        )

    def _start_next_locked(self) -> TurnRequest | None:
        if self._active is not None or not self._queue:
            return None
        item = self._queue.pop(0)
        self._active = item.request
        self._active_token = CancellationToken()
        self._active_state = SchedulerState.RUNNING
        self._blocked_reason = ""
        self._emit(
            SchedulerEvent(
                kind=SchedulerEventKind.STARTED,
                request_id=item.request.request_id,
                lane=item.request.lane,
                state=SchedulerState.RUNNING,
                timestamp=self._now(),
                autonomous_gate=self._autonomous_gate,
            )
        )
        return item.request

    def _emit_gate_changed(self, reason: str) -> None:
        self._emit(
            SchedulerEvent(
                kind=SchedulerEventKind.GATE_CHANGED,
                state=SchedulerState.IDLE,
                timestamp=self._now(),
                reason=reason,
                blocked_reason=self._blocked_reason,
                autonomous_gate=self._autonomous_gate,
            )
        )

    def _emit(self, event: SchedulerEvent) -> None:
        self._events.append(event)
        if self._event_sink is not None:
            self._event_sink(event)

    def _now(self) -> float:
        return float(self._clock())


__all__ = ["CancellationToken", "TurnExecutor", "TurnScheduler"]
