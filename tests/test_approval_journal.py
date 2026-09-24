from voidcube.infrastructure.persistence.approval_journal import ApprovalJournal


def test_approval_journal_has_idempotent_resolution(tmp_path):
    journal = ApprovalJournal(tmp_path / "approvals.db")
    request_id = journal.request(
        session_id="session", turn_id="turn", command="rm", description="delete"
    )
    assert len(journal.pending("session")) == 1
    assert journal.resolve(request_id, "denied", "user denied") is True
    assert journal.resolve(request_id, "approved") is False
    assert journal.pending("session") == []
