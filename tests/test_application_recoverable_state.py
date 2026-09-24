from datetime import datetime

from voidcube.application.application_runtime import ApplicationRuntime
from voidcube.infrastructure.persistence.approval_journal import ApprovalJournal
from voidcube.infrastructure.persistence.turn_event_journal import TurnEventJournal
from voidcube.domain.contracts.events import TurnEvent, TurnEventKind


def test_runtime_surfaces_persisted_recovery_state(tmp_path):
    events = TurnEventJournal(tmp_path / "events.db")
    approvals = ApprovalJournal(tmp_path / "approvals.db")
    events.append(TurnEvent(TurnEventKind.STARTED, "session", "turn"))
    approvals.request(session_id="session", turn_id="turn", command="rm", description="delete")
    runtime = ApplicationRuntime.create(
        session_id="session",
        session_start=datetime.now(),
        event_journal=events,
        approval_journal=approvals,
    )
    state = runtime.recoverable_state()
    assert state["active_turn"]["turn_id"] == "turn"
    assert len(state["pending_approvals"]) == 1
