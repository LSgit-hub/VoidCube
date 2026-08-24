from __future__ import annotations

from voidcube.infrastructure.persistence.session_runtime import SessionDB


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


def test_session_listing_orders_by_latest_message_activity(tmp_path) -> None:
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("active-older", source="cli", model="test")
    db.create_session("empty-newer", source="cli", model="test")
    db._conn.execute(
        "UPDATE sessions SET started_at = CASE id "
        "WHEN 'active-older' THEN 100.0 ELSE 300.0 END "
        "WHERE id IN ('active-older', 'empty-newer')"
    )
    db.append_message(
        session_id="active-older",
        role="user",
        content="latest activity",
    )
    db._conn.execute(
        "UPDATE messages SET timestamp = 400.0 WHERE session_id = 'active-older'"
    )

    sessions = db.list_sessions_rich(source="cli")

    assert [session["id"] for session in sessions] == [
        "active-older",
        "empty-newer",
    ]
    assert sessions[0]["last_active"] == 400.0


def test_resumable_session_follows_only_nonempty_compression_lineage(tmp_path) -> None:
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("root", source="cli", model="test")
    db.append_message(session_id="root", role="user", content="old")
    db.end_session("root", "compression")
    db.create_session(
        "empty-continuation",
        source="cli",
        model="test",
        parent_session_id="root",
    )
    db.create_session(
        "ready-continuation",
        source="cli",
        model="test",
        parent_session_id="root",
    )
    db.append_message(
        session_id="ready-continuation",
        role="assistant",
        content="current",
    )
    db.create_session(
        "manual-branch",
        source="cli",
        model="test",
        parent_session_id="ready-continuation",
    )
    db.append_message(
        session_id="manual-branch",
        role="assistant",
        content="must not follow",
    )

    assert db.resolve_resumable_session_id("root") == "ready-continuation"
    assert (
        db.resolve_resumable_session_id("empty-continuation")
        == "empty-continuation"
    )


def test_truncate_last_user_turn_removes_persisted_exchange_and_recounts(tmp_path) -> None:
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("active", source="cli", model="test")
    db.append_message(session_id="active", role="user", content="first")
    db.append_message(
        session_id="active",
        role="assistant",
        content=None,
        tool_calls=[{"name": "one"}, {"name": "two"}],
    )
    db.append_message(session_id="active", role="tool", content="done")
    db.append_message(session_id="active", role="assistant", content="answer")
    db.append_message(session_id="active", role="user", content="second")
    db.append_message(
        session_id="active",
        role="assistant",
        content=None,
        tool_calls=[{"name": "three"}],
    )
    db.append_message(session_id="active", role="tool", content="done again")

    removed = db.truncate_last_user_turn("active")

    assert removed == 3
    assert [message["content"] for message in db.get_messages("active")] == [
        "first",
        None,
        "done",
        "answer",
    ]
    session = db.get_session("active")
    assert session["message_count"] == 4
    assert session["tool_call_count"] == 2
    assert session["flush_sequence"] == 4

    db.append_message(session_id="active", role="user", content="replacement")
    assert [message["sequence_no"] for message in db.get_messages("active")] == [
        1,
        2,
        3,
        4,
        5,
    ]


def test_truncate_last_user_turn_is_noop_without_user_message(tmp_path) -> None:
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("active", source="cli", model="test")
    db.append_message(session_id="active", role="assistant", content="hello")

    assert db.truncate_last_user_turn("active") == 0
    assert db.get_session("active")["message_count"] == 1


def test_truncate_uses_sequence_order_when_timestamps_are_out_of_order(tmp_path) -> None:
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("active", source="cli", model="test")
    db.append_message(session_id="active", role="user", content="first")
    db.append_message(session_id="active", role="assistant", content="answer")
    db.append_message(session_id="active", role="user", content="second")
    db.append_message(session_id="active", role="assistant", content="latest")
    db._conn.execute(
        "UPDATE messages SET timestamp = CASE sequence_no "
        "WHEN 1 THEN 100 WHEN 2 THEN 90 WHEN 3 THEN 1 ELSE 0 END "
        "WHERE session_id = 'active'"
    )

    assert db.truncate_last_user_turn("active") == 2
    assert [row["content"] for row in db.get_messages("active")] == [
        "first",
        "answer",
    ]
    assert db.get_session("active")["flush_sequence"] == 2
