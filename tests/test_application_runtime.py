from __future__ import annotations

from datetime import datetime
import uuid

import pytest

from VoidCube_app.application import ApplicationRuntime
from VoidCube_app.contracts.artifacts import Artifact
from VoidCube_app.contracts.events import SessionEventKind, TurnEventKind
from VoidCube_app.session_lifecycle import SessionLifecycleState
from VoidCube_app.interaction_contract import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ClarificationDecision,
    ClarificationRequest,
    ClarificationStatus,
)
from VoidCube_app.tool_events import ToolEvent, ToolEventKind
from VoidCube_app.turn_contract import normalize_turn_outcome


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def _fixed_uuid() -> uuid.UUID:
    return uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")


def test_application_runtime_owns_history_and_emits_session_turn_events() -> None:
    events = []
    runtime = ApplicationRuntime.create(
        session_id="session-1",
        session_start=datetime(2026, 8, 4, 12, 0, 0),
        conversation_history=[{"role": "assistant", "content": "previous"}],
        event_sink=events.append,
        uuid_factory=_fixed_uuid,
    )

    turn = runtime.begin_turn("hello")
    outcome = normalize_turn_outcome(
        {
            "messages": [
                *turn.conversation_history,
                {"role": "assistant", "content": "answer"},
            ],
            "final_response": "answer",
        },
        fallback_history=turn.conversation_history,
    )
    runtime.finish_turn(outcome)

    assert runtime.state.session_id == "session-1"
    assert runtime.state.conversation_history[-1] == {
        "role": "assistant",
        "content": "answer",
    }
    assert runtime.state.turn_active is False
    assert [event.kind for event in events] == [
        SessionEventKind.STARTED,
        TurnEventKind.STARTED,
        TurnEventKind.COMPLETED,
    ]
    assert events[1].turn_id == events[2].turn_id


def test_application_runtime_rejects_overlapping_turns_and_fails_closed_on_sink_error() -> None:
    def broken_sink(_event) -> None:
        raise RuntimeError("renderer unavailable")

    runtime = ApplicationRuntime.create(
        session_start=datetime(2026, 8, 4),
        event_sink=broken_sink,
        uuid_factory=_fixed_uuid,
    )

    runtime.begin_turn("first")
    with pytest.raises(RuntimeError, match="already active"):
        runtime.begin_turn("second")


def test_application_runtime_applies_session_transition_and_emits_boundary_events() -> None:
    events = []
    runtime = ApplicationRuntime.create(
        session_id="old-session",
        session_start=datetime(2026, 8, 4),
        event_sink=events.append,
        uuid_factory=_fixed_uuid,
    )

    runtime.apply_session_state(
        SessionLifecycleState(
            session_id="new-session",
            session_start=datetime(2026, 8, 5),
            conversation_history=({"role": "user", "content": "hello"},),
            resumed=True,
        )
    )

    assert runtime.state.session_id == "new-session"
    assert runtime.state.session_start == datetime(2026, 8, 5)
    assert runtime.state.conversation_history == [
        {"role": "user", "content": "hello"}
    ]
    assert [event.kind for event in events] == [
        SessionEventKind.STARTED,
        SessionEventKind.ENDED,
        SessionEventKind.RESUMED,
    ]
    assert events[1].session_id == "old-session"


def test_application_runtime_publishes_interaction_and_delivery_events() -> None:
    events = []
    runtime = ApplicationRuntime.create(
        session_id="session-2",
        session_start=datetime(2026, 8, 4),
        event_sink=events.append,
        uuid_factory=_fixed_uuid,
    )
    runtime.begin_turn("question")

    tool_event = ToolEvent.reasoning("checking")
    runtime.tool_event_sink(tool_event)
    runtime.message_delta_sink("answer")
    runtime.usage_sink({"total_tokens": 12})
    runtime.artifact_sink(
        Artifact(kind="file", uri="report.txt", mime_type="text/plain")
    )

    approval = runtime.resolve_approval(
        ApprovalRequest("rm -rf", "remove files"),
        lambda _request: ApprovalDecision(ApprovalStatus.DENIED, "user denied"),
    )
    clarification = runtime.resolve_clarification(
        ClarificationRequest.create("Which file?", ["a.txt", "b.txt"]),
        lambda _request: ClarificationDecision(
            ClarificationStatus.ANSWERED,
            answer="a.txt",
        ),
    )

    assert approval.status is ApprovalStatus.DENIED
    assert clarification.answer == "a.txt"
    assert events[2] is tool_event
    assert events[3].text == "answer"
    assert events[4].usage == {"total_tokens": 12}
    assert events[5].artifact.uri == "report.txt"
    assert events[6].request.command == "rm -rf"
    assert events[7].request.question == "Which file?"
    assert events[2].kind is ToolEventKind.REASONING


def test_application_runtime_promotes_tool_artifacts_to_application_events() -> None:
    events = []
    runtime = ApplicationRuntime.create(
        session_id="session-artifact",
        session_start=datetime(2026, 8, 4),
        event_sink=events.append,
        uuid_factory=_fixed_uuid,
    )
    runtime.begin_turn("inspect page")
    artifact = Artifact(
        kind="image",
        uri="C:/tmp/screenshot.png",
        mime_type="image/png",
    )

    runtime.tool_event_sink(
        ToolEvent.completed(
            call_id="call-1",
            name="browser_vision",
            arguments={},
            result="ok",
            duration=1.0,
            is_error=False,
            artifacts=(artifact,),
        )
    )

    assert events[-1].artifact == artifact
