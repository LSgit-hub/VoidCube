from datetime import datetime

from voidcube.application.application_runtime import ApplicationRuntime
from voidcube.domain.contracts.events import TurnEvent, TurnEventKind
from voidcube.infrastructure.persistence.turn_event_journal import TurnEventJournal


def test_recoverable_state_is_empty_without_journals():
    runtime = ApplicationRuntime.create(
        session_id="session",
        session_start=datetime.now(),
    )
    assert runtime.recoverable_state() == {
        "active_turn": None,
        "pending_approvals": [],
        "event_summary": None,
    }


def test_event_journal_recovery_state_survives_new_runtime(tmp_path):
    journal = TurnEventJournal(tmp_path / "events.db")
    journal.append(TurnEvent(TurnEventKind.STARTED, "session", "turn"))
    runtime = ApplicationRuntime.create(
        session_id="session",
        session_start=datetime.now(),
        event_journal=TurnEventJournal(tmp_path / "events.db"),
    )
    assert runtime.recoverable_state()["active_turn"]["turn_id"] == "turn"
