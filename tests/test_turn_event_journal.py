from voidcube.infrastructure.persistence.turn_event_journal import TurnEventJournal
from voidcube.domain.contracts.events import TurnEvent, TurnEventKind


def test_turn_event_journal_orders_and_recovers_active_turn(tmp_path):
    journal = TurnEventJournal(tmp_path / "events.db")
    journal.append(TurnEvent(TurnEventKind.STARTED, "session", "turn-1"))
    journal.append(TurnEvent(TurnEventKind.STARTED, "session", "turn-2"))
    journal.append(TurnEvent(TurnEventKind.COMPLETED, "session", "turn-2"))

    events = journal.list("session")
    assert [event["sequence_no"] for event in events] == [1, 2, 3]
    assert journal.recover_active_turn("session")["turn_id"] == "turn-1"
    journal.close()


def test_latest_started_turn_is_the_recovery_owner(tmp_path):
    journal = TurnEventJournal(tmp_path / "events.db")
    journal.append(TurnEvent(TurnEventKind.STARTED, "session", "old"))
    journal.append(TurnEvent(TurnEventKind.STARTED, "session", "new"))
    assert journal.recover_active_turn("session")["turn_id"] == "new"
