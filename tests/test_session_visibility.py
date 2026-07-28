from __future__ import annotations

from VoidCube_core.state import SessionDB


def test_session_listing_excludes_internal_scheduled_prefix(tmp_path) -> None:
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("scheduled_run-1", source="cli", model="test")
    db.create_session("scheduledXuser", source="cli", model="test")
    db.create_session("user-session", source="cli", model="test")

    sessions = db.list_sessions_rich(
        source="cli",
        exclude_id_prefixes=["scheduled_"],
    )

    assert {session["id"] for session in sessions} == {
        "scheduledXuser",
        "user-session",
    }
