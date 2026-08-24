from __future__ import annotations

import pytest

from voidcube.domain.contracts.scheduler import (
    SchedulerEvent,
    SchedulerEventKind,
    SchedulerSnapshot,
    SchedulerState,
    TurnLane,
    TurnPriority,
    TurnRequest,
    TurnSummary,
)


def test_turn_request_normalizes_values_and_copies_tool_policy() -> None:
    policy = {"shell": False}
    request = TurnRequest(
        request_id="  req-1  ",
        lane="user_chat",
        session_id=" session-1 ",
        prompt={"text": "hello"},
        tool_policy=policy,
    )

    assert request.request_id == "req-1"
    assert request.session_id == "session-1"
    assert request.lane is TurnLane.USER_CHAT
    assert request.priority == TurnPriority.USER
    assert request.tool_policy == {"shell": False}
    policy["shell"] = True
    assert request.tool_policy == {"shell": False}


def test_autonomous_request_gets_lower_default_priority() -> None:
    request = TurnRequest(
        request_id="req-2", lane=TurnLane.SUPERVISOR_TASK, session_id="s", prompt="x"
    )
    assert request.priority == TurnPriority.AUTONOMOUS


@pytest.mark.parametrize("kwargs", [{"request_id": ""}, {"session_id": " "}])
def test_turn_request_rejects_missing_identity(kwargs: dict[str, str]) -> None:
    values = {
        "request_id": "req",
        "lane": TurnLane.USER_CHAT,
        "session_id": "session",
        "prompt": "x",
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        TurnRequest(**values)


def test_contracts_reject_unknown_enum_values() -> None:
    with pytest.raises(ValueError, match="unknown turn lane"):
        TurnRequest(request_id="r", lane="other", session_id="s", prompt="x")
    with pytest.raises(ValueError, match="unknown scheduler state"):
        TurnSummary(request_id="r", lane=TurnLane.USER_CHAT, priority=1, state="other")
    with pytest.raises(ValueError, match="unknown scheduler event"):
        SchedulerEvent(kind="other")


def test_snapshot_and_event_round_trip() -> None:
    summary = TurnSummary(
        request_id="r", lane=TurnLane.SUPERVISOR_TASK, priority=3, state=SchedulerState.RUNNING
    )
    snapshot = SchedulerSnapshot(
        active=summary,
        queued=(summary,),
        autonomous_gate=True,
        blocked_reason="busy",
        updated_at=12.5,
    )
    restored_snapshot = SchedulerSnapshot.from_dict(snapshot.to_dict())
    assert restored_snapshot == snapshot

    event = SchedulerEvent(
        kind=SchedulerEventKind.GATE_CHANGED,
        request_id="r",
        lane=TurnLane.SUPERVISOR_TASK,
        state=SchedulerState.IDLE,
        timestamp=12.5,
        reason="auto-q",
        autonomous_gate=False,
    )
    assert SchedulerEvent.from_dict(event.to_dict()) == event


def test_turn_request_round_trip() -> None:
    request = TurnRequest(
        request_id="r",
        lane=TurnLane.USER_CHAT,
        session_id="s",
        prompt=["hello"],
        priority=77,
        tool_policy={"network": True},
        source="cli",
    )
    assert TurnRequest.from_dict(request.to_dict()) == request
