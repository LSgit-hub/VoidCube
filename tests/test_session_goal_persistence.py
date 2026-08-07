from __future__ import annotations

from VoidCube_core.state import SessionDB


def test_session_goal_persists_and_enforces_one_active_goal(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    db.create_session("session-b", "cli")

    created = db.create_session_goal("session-a", "Verify the terminal UX")
    assert created["objective"] == "Verify the terminal UX"
    assert created["status"] == "active"
    assert db.get_session_goal("session-b") is None

    assert db.update_session_goal("session-a", "blocked", "No TTY") is True
    assert db.get_session_goal("session-a")["reason"] == "No TTY"
    assert db.clear_session_goal("session-a") is True
    assert db.get_session_goal("session-a") is None

    db.close()
