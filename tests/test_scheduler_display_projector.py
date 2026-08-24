from __future__ import annotations

from voidcube.domain.contracts.scheduler import (
    SchedulerEvent,
    SchedulerEventKind,
    SchedulerSnapshot,
    SchedulerState,
    TurnLane,
    TurnSummary,
)
from voidcube.interfaces.cli.scheduler_display_projector import SchedulerDisplayProjector


def _snapshot(*, active=None, queued=(), blocked_reason=""):
    return SchedulerSnapshot(
        active=active,
        queued=tuple(queued),
        autonomous_gate=True,
        blocked_reason=blocked_reason,
        updated_at=1.0,
    )


def test_projector_keeps_scheduler_snapshot_authoritative_and_events_read_only():
    projector = SchedulerDisplayProjector(max_events=2)
    event = SchedulerEvent(
        kind=SchedulerEventKind.CANCEL_REQUESTED,
        request_id="user-123456789",
        lane=TurnLane.USER_CHAT,
        state=SchedulerState.CANCELLING,
        reason="cancel_requested",
    )
    projector.accept(event)

    active = TurnSummary(
        request_id="user-123456789",
        lane=TurnLane.USER_CHAT,
        priority=100,
        state=SchedulerState.CANCELLING,
    )
    snapshot = _snapshot(active=active, blocked_reason="busy")
    presentation = projector.presentation(lambda: snapshot)

    assert presentation["active"]["request_id"] == "user-123456789"
    assert presentation["blocked_reason"] == "busy"
    assert presentation["latest_event"]["kind"] == "cancel_requested"
    assert projector.events() == (event,)


def test_projector_bounds_event_history():
    projector = SchedulerDisplayProjector(max_events=2)
    for index in range(3):
        projector.accept(
            SchedulerEvent(
                kind=SchedulerEventKind.QUEUED,
                request_id=f"request-{index}",
                lane=TurnLane.SUPERVISOR_TASK,
            )
        )

    assert [event.request_id for event in projector.events()] == [
        "request-1",
        "request-2",
    ]
