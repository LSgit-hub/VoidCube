"""会话 transcript 分叉的检测与恢复契约。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from voidcube.infrastructure.persistence.session_db import SessionDB
from voidcube.infrastructure.persistence.session_runtime import SessionPersistence


def _message(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def _persistence(
    tmp_path: Path,
    db: SessionDB,
    session_id: str,
    *,
    allow_recovery: bool = True,
) -> SessionPersistence:
    return SessionPersistence(
        enabled=True,
        logs_dir=tmp_path / "sessions",
        session_db=db,
        session_start=datetime(2026, 9, 10, 12, 0, 0),
        session_id=lambda: session_id,
        model=lambda: "test-model",
        base_url=lambda: "http://localhost",
        platform=lambda: "cli",
        system_prompt=lambda: "system",
        tools=lambda: [],
        user_message_override=lambda: (None, None),
        allow_divergence_recovery=allow_recovery,
    )


def test_flush_appends_only_the_uncommitted_tail(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("session", source="cli", model="test-model")
    persistence = _persistence(tmp_path, db, "session")

    first = _message("user", "hello")
    assert persistence.flush_to_db([first]).status == "succeeded"
    assert db.get_transcript_snapshot("session")["flush_sequence"] == 1

    second = _message("assistant", "hi")
    outcome = persistence.flush_to_db([first, second])

    assert outcome.status == "succeeded"
    snapshot = db.get_transcript_snapshot("session")
    assert snapshot["flush_sequence"] == 2
    assert [m["content"] for m in snapshot["messages"]] == ["hello", "hi"]


def test_divergent_transcript_is_rebaselined_and_backed_up(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("session", source="cli", model="test-model")
    persistence = _persistence(tmp_path, db, "session")

    committed = [_message("user", "original"), _message("assistant", "answer")]
    assert persistence.flush_to_db(committed).status == "succeeded"

    rewritten = [
        _message("user", "summarized head"),
        _message("assistant", "continuation"),
    ]
    outcome = persistence.flush_to_db(rewritten)

    assert outcome.status == "succeeded"
    recovery = outcome.details["divergence_recovery"]
    assert recovery["status"] == "rebaselined"
    assert recovery["committed_messages"] == 2
    assert recovery["local_messages"] == 2

    snapshot = db.get_transcript_snapshot("session")
    assert [m["content"] for m in snapshot["messages"]] == [
        "summarized head",
        "continuation",
    ]

    backup = Path(recovery["backup_path"])
    assert backup.exists()
    payload = json.loads(backup.read_text(encoding="utf-8"))
    assert payload["reason"] == "transcript_divergence_recovery"
    assert [m["content"] for m in payload["messages"]] == ["original", "answer"]


def test_divergence_with_recovery_disabled_reports_structured_failure(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.ensure_session("session", source="cli", model="test-model")
    persistence = _persistence(tmp_path, db, "session", allow_recovery=False)

    assert persistence.flush_to_db([_message("user", "original")]).status == "succeeded"

    outcome = persistence.flush_to_db([_message("user", "divergent")])

    assert outcome.status == "failed"
    assert "SessionTranscriptDivergenceError" in outcome.error

    snapshot = db.get_transcript_snapshot("session")
    assert [m["content"] for m in snapshot["messages"]] == ["original"]
