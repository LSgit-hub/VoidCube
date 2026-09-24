from voidcube.domain.contracts.events import TurnEvent, TurnEventKind
from voidcube.infrastructure.persistence.turn_event_journal import TurnEventJournal


def test_session_summary_is_bounded_and_replayable(tmp_path):
    journal = TurnEventJournal(tmp_path / "events.db")
    journal.append(TurnEvent(TurnEventKind.STARTED, "s", "t"))
    summary = journal.session_summary("s")
    assert summary["event_count"] == 1
    assert summary["event_types"] == {"turn.started": 1}
    assert summary["active_turn"]["turn_id"] == "t"
