from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from VoidCube_core.state import SCHEMA_VERSION, SessionDB, SessionSequenceConflictError


def _message(db: SessionDB, session_id: str, sequence_no: int, content: str):
    message = {
        "sequence_no": sequence_no,
        "role": "user",
        "content": content,
    }
    message["message_id"] = db.stable_message_id(
        session_id, sequence_no, message
    )
    return message


def test_batch_failure_rolls_back_all_messages_and_counters(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session", source="cli")
    db._conn.execute(
        """CREATE TRIGGER fail_second_message BEFORE INSERT ON messages
        WHEN new.sequence_no = 2 BEGIN
            SELECT RAISE(ABORT, 'injected failure');
        END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        db.append_messages_batch(
            "session",
            [
                _message(db, "session", 1, "first"),
                _message(db, "session", 2, "second"),
            ],
        )

    assert db.get_messages("session") == []
    session = db.get_session("session")
    assert session["message_count"] == 0
    assert session["flush_sequence"] == 0


def test_committed_batch_replay_is_idempotent(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session", source="cli")
    batch = [
        _message(db, "session", 1, "first"),
        _message(db, "session", 2, "second"),
    ]

    first_ids = db.append_messages_batch("session", batch)
    replay_ids = db.append_messages_batch("session", batch)

    assert replay_ids == first_ids
    assert [message["content"] for message in db.get_messages("session")] == [
        "first",
        "second",
    ]
    session = db.get_session("session")
    assert session["message_count"] == 2
    assert session["flush_sequence"] == 2


def test_v7_database_migrates_rows_to_stable_order(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE schema_version(version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES(7);
        CREATE TABLE sessions(
            id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at REAL NOT NULL,
            message_count INTEGER DEFAULT 0, tool_call_count INTEGER DEFAULT 0,
            title TEXT, parent_session_id TEXT
        );
        CREATE TABLE messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, content TEXT, tool_call_id TEXT, tool_calls TEXT,
            tool_name TEXT, timestamp REAL NOT NULL, token_count INTEGER,
            finish_reason TEXT, reasoning TEXT, reasoning_details TEXT
        );
        INSERT INTO sessions(id, source, started_at, message_count)
        VALUES('legacy', 'cli', 1, 2);
        INSERT INTO messages(session_id, role, content, timestamp)
        VALUES('legacy', 'user', 'first', 5), ('legacy', 'assistant', 'second', 5);
        """
    )
    conn.commit()
    conn.close()

    db = SessionDB(path)
    messages = db.get_messages("legacy")

    assert [message["sequence_no"] for message in messages] == [1, 2]
    assert all(message["message_id"].startswith("legacy:legacy:") for message in messages)
    assert db.get_session("legacy")["flush_sequence"] == 2
    assert db._conn.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION


def test_sequences_remain_unique_when_timestamps_match(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session", source="cli")
    batch = [
        {**_message(db, "session", 1, "first"), "timestamp": 10},
        {**_message(db, "session", 2, "second"), "timestamp": 10},
    ]
    db.append_messages_batch("session", batch)

    assert [row["sequence_no"] for row in db.get_messages("session")] == [1, 2]
    assert json.loads(json.dumps(db.get_messages_as_conversation("session")))[0][
        "content"
    ] == "first"


def test_concurrent_instances_allocate_contiguous_sequences_atomically(tmp_path):
    path = tmp_path / "sessions.db"
    first = SessionDB(path)
    first.create_session("session", source="cli")
    second = SessionDB(path)
    barrier = threading.Barrier(3)
    errors = []

    def append(db: SessionDB, content: str) -> None:
        barrier.wait()
        try:
            db.append_message("session", "user", content)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=append, args=(first, "first")),
        threading.Thread(target=append, args=(second, "second")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    rows = first.get_messages("session")
    assert [row["sequence_no"] for row in rows] == [1, 2]
    assert {row["content"] for row in rows} == {"first", "second"}


def test_stale_expected_cursor_is_rejected_without_dropping_message(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    db.create_session("session", source="cli")
    db.append_message("session", "user", "committed")
    message = _message(db, "session", 1, "stale")

    with pytest.raises(SessionSequenceConflictError, match="stale session cursor"):
        db.append_messages_batch(
            "session",
            [message],
            expected_flush_sequence=0,
        )

    assert [row["content"] for row in db.get_messages("session")] == ["committed"]


def test_action_refs_migrate_and_round_trip_in_authoritative_session_store(tmp_path):
    path = tmp_path / "v8.db"
    db = SessionDB(path)
    db.create_session("session", source="cli")
    db._conn.execute("UPDATE schema_version SET version = 8")
    db._conn.commit()
    db.close()

    migrated = SessionDB(path)
    message = {
        "sequence_no": 1,
        "role": "tool",
        "content": "created",
        "tool_call_id": "call-1",
        "action_refs": [
            {
                "action_id": "act-1",
                "state": "succeeded",
                "target_summary": "resource-1",
                "evidence_refs": [],
            }
        ],
    }
    message["message_id"] = migrated.stable_message_id("session", 1, message)
    migrated.append_messages_batch("session", [message])

    assert migrated._conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert migrated.get_messages_as_conversation("session")[0]["action_refs"] == message["action_refs"]
