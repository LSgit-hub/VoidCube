from __future__ import annotations

from types import SimpleNamespace

from voidcube.infrastructure.persistence.session_runtime import SessionDB
from voidcube.interfaces.cli.session_goal_runtime import (
    BLOCKED,
    create_goal,
    get_goal,
    stop_goal_after_turn,
)


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
    assert db.update_session_goal("session-a", "active", "TTY restored") is True
    assert db.get_session_goal("session-a")["status"] == "active"
    assert db.update_session_goal("session-a", "blocked", "Done testing") is True
    assert db.clear_session_goal("session-a") is True
    assert db.get_session_goal("session-a") is None

    db.close()


def test_session_goal_blocker_requires_same_reason_in_three_turns(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    db.create_session_goal("session-a", "Finish the task")

    first = db.audit_session_goal_blocker(
        "session-a", "Missing access", turn_id="turn-1"
    )
    duplicate = db.audit_session_goal_blocker(
        "session-a", "Different issue", turn_id="turn-1"
    )
    second = db.audit_session_goal_blocker(
        "session-a", "Missing access", turn_id="turn-2"
    )
    changed = db.audit_session_goal_blocker(
        "session-a", "Different issue", turn_id="turn-3"
    )
    assert first["blocked_streak"] == duplicate["blocked_streak"] == 1
    assert duplicate["reason"] == "Missing access"
    assert second["blocked_streak"] == 2
    assert changed["blocked_streak"] == 1
    assert db.get_session_goal("session-a")["status"] == "active"

    db.close()


def test_session_goal_blocks_after_three_consecutive_same_blockers(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    db.create_session_goal("session-a", "Finish the task")

    db.audit_session_goal_blocker("session-a", "Missing access", turn_id="turn-1")
    db.audit_session_goal_blocker("session-a", "Missing access", turn_id="turn-2")
    result = db.audit_session_goal_blocker(
        "session-a", "Missing access", turn_id="turn-3"
    )

    assert result == {
        "status": "blocked",
        "reason": "Missing access",
        "blocked_streak": 3,
    }
    assert db.get_session_goal("session-a")["status"] == "blocked"
    db.close()


def test_unreported_completed_goal_turn_clears_blocker_streak(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    db.create_session_goal("session-a", "Finish the task")

    db.audit_session_goal_blocker("session-a", "Missing access", turn_id="turn-1")
    db.finish_session_goal_audit_turn("session-a", "turn-2")

    result = db.audit_session_goal_blocker(
        "session-a", "Missing access", turn_id="turn-3"
    )
    assert result["status"] == "active"
    assert result["blocked_streak"] == 1
    db.close()


def test_turn_failure_persists_blocked_goal_until_resume(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    host = SimpleNamespace(session_id="session-a", _session_db=db)
    create_goal(host, "Finish the task")

    assert stop_goal_after_turn(host, "Execution unavailable") is True
    assert get_goal(host)["status"] == BLOCKED

    db.close()


def test_session_goal_objective_can_be_revised_without_resetting_status(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    db.create_session_goal("session-a", "Initial objective")
    db.update_session_goal("session-a", "paused", "waiting for user")

    assert db.update_session_goal_objective(
        "session-a", "Revised objective", reason="scope clarified"
    ) is True
    goal = db.get_session_goal("session-a")
    assert goal["objective"] == "Revised objective"
    assert goal["status"] == "paused"
    assert goal["reason"] == "scope clarified"

    db.close()


def test_session_goal_objective_revision_clears_previous_audit_turn(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    created = db.create_session_goal("session-a", "Initial objective")
    db.audit_session_goal_blocker(
        "session-a", "Old blocker", turn_id="turn-1",
        expected_revision=created["revision"],
    )
    current = db.get_session_goal("session-a")

    assert db.update_session_goal_objective(
        "session-a", "Revised objective", reason="scope clarified",
        expected_revision=current["revision"],
    ) is True
    result = db.audit_session_goal_blocker(
        "session-a", "New blocker", turn_id="turn-1",
        expected_revision=db.get_session_goal("session-a")["revision"],
    )

    assert result["reason"] == "New blocker"
    assert result["blocked_streak"] == 1
    db.close()


def test_session_goal_revision_rejects_stale_status_or_objective_writes(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    created = db.create_session_goal("session-a", "Initial objective")
    revision = created["revision"]

    assert db.update_session_goal(
        "session-a", "paused", "first writer", expected_revision=revision
    ) is True
    assert db.update_session_goal(
        "session-a", "blocked", "stale writer", expected_revision=revision
    ) is False
    assert db.update_session_goal_objective(
        "session-a", "stale objective", expected_revision=revision
    ) is False
    assert db.get_session_goal("session-a")["status"] == "paused"

    db.close()


def test_session_goal_backend_binding_rejects_stale_revision(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    created = db.create_session_goal("session-a", "Initial objective")

    assert db.update_session_goal(
        "session-a", "paused", "waiting", expected_revision=created["revision"]
    ) is True
    assert db.bind_session_goal_backend(
        "session-a",
        backend="goal_manager",
        project_id="project-1",
        root_node_id="root-1",
        backend_status="available",
        expected_revision=created["revision"],
    ) is False
    goal = db.get_session_goal("session-a")
    assert goal["status"] == "paused"
    assert goal["project_id"] is None
    db.close()


def test_session_goal_read_does_not_fallback_to_stale_memory_when_repository_fails():
    class FailingRepository:
        def get_session_goal(self, _session_id):
            raise OSError("database unavailable")

    host = SimpleNamespace(
        session_id="session-a",
        _session_db=FailingRepository(),
        _session_goals={
            "session-a": {
                "session_id": "session-a",
                "objective": "stale objective",
                "status": "active",
            },
        },
    )

    import pytest

    with pytest.raises(OSError, match="database unavailable"):
        get_goal(host)


def test_session_goal_audit_cleanup_rejects_stale_revision(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session-a", "cli")
    created = db.create_session_goal("session-a", "Initial objective")
    revision = created["revision"]

    db.audit_session_goal_blocker(
        "session-a", "Missing access", turn_id="turn-1", expected_revision=revision
    )
    current = db.get_session_goal("session-a")
    assert current["revision"] == revision + 1

    # A completion from the old turn must not clear a newer blocker update.
    assert db.finish_session_goal_audit_turn(
        "session-a", "turn-2", expected_revision=revision
    ) is False
    unchanged = db.get_session_goal("session-a")
    assert unchanged["blocked_reason"] == "Missing access"
    assert unchanged["blocked_streak"] == 1

    assert db.finish_session_goal_audit_turn(
        "session-a", "turn-2", expected_revision=unchanged["revision"]
    ) is True
    cleared = db.get_session_goal("session-a")
    assert cleared["blocked_reason"] is None
    assert cleared["blocked_streak"] == 0
    db.close()
