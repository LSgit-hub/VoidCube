from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

import pytest

from systems.memory.config import MemoryServiceConfig
from systems.memory.database import open_memory_sqlite
from systems.memory.memory_service import (
    DayAggregateRequest,
    ForgetRequest,
    MemoryService,
    SessionCloseRequest,
    SessionCreate,
    TurnCreate,
)
from systems.memory.profile_store import upsert_profile_memory
from systems.memory.time_summary import day_bucket_for_timestamp, day_period


def _profile(memory_id: str, predicate: str, value: str):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=memory_id,
        memory_kind="preference",
        subject="user",
        predicate=predicate,
        value=value,
        summary=value,
        confidence=0.9,
        certainty_state="observed",
        status="active",
        valid_from=now,
        valid_to=None,
        evidence_refs=[f"turn:{memory_id}"],
        source_turns=[memory_id],
        supersedes=[],
        conflict_refs=[],
        created_at=now,
    )


def _insert_time_summary(
    connection: sqlite3.Connection,
    *,
    summary_id: str,
    summary_type: str,
    bucket_key: str,
    owner_id: str = "owner-a",
    workspace_id: str = "workspace-a",
    version: int = 1,
    status: str = "active",
    supersedes_summary_id: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO time_summaries "
        "(summary_id, summary_type, owner_id, workspace_id, bucket_key, "
        "period_start, period_end, timezone, title, summary, source_count, "
        "source_hash, content_hash, version, status, supersedes_summary_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            summary_id,
            summary_type,
            owner_id,
            workspace_id,
            bucket_key,
            "2026-08-01T00:00:00+08:00",
            "2026-09-01T00:00:00+08:00",
            "Asia/Shanghai",
            f"{summary_type} title",
            f"{summary_type} summary",
            1,
            f"source-{summary_id}",
            f"hash-{summary_id}",
            version,
            status,
            supersedes_summary_id,
            "2026-09-01T00:05:00+08:00",
            "2026-09-01T00:05:00+08:00",
        ),
    )


def test_profile_cardinality_keeps_collections_and_revises_scalars(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        for profile in (
            _profile("concise", "long_term_preference", "简洁回答"),
            _profile("chinese", "long_term_preference", "中文回答"),
            _profile("language-old", "preferred_language", "English"),
            _profile("language-new", "preferred_language", "中文"),
        ):
            upsert_profile_memory(
                conn,
                profile,
                owner_id="local-user",
                workspace_id="default",
                now=now,
            )
        conn.commit()
        rows = conn.execute(
            "SELECT memory_id, predicate, status, slot_key FROM profile_memories "
            "ORDER BY memory_id"
        ).fetchall()
    finally:
        conn.close()

    by_id = {row[0]: row[1:] for row in rows}
    assert by_id["concise"][1] == "active"
    assert by_id["chinese"][1] == "active"
    assert by_id["concise"][2] != by_id["chinese"][2]
    assert by_id["language-old"][1] == "superseded"
    assert by_id["language-new"][1] == "active"
    assert by_id["language-old"][2] == by_id["language-new"][2]


def test_legacy_resource_fields_migrate_once_and_repair_relations(tmp_path):
    db_path = tmp_path / "memory.db"
    stamp = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE turns (turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, "
            "speaker TEXT NOT NULL, text TEXT NOT NULL, timestamp TEXT NOT NULL, "
            "relevance_score REAL DEFAULT 1.0, decay_factor REAL DEFAULT 0.01, "
            "tags TEXT, metadata TEXT, compression_retry_count INTEGER DEFAULT 0, "
            "compression_retry_after TEXT, compressed_to_tier2 INTEGER DEFAULT 0)"
        )
        conn.executemany(
            "INSERT INTO turns VALUES (?, 'legacy-session', 'user', ?, ?, 1.0, "
            "0.01, '[]', '{}', ?, ?, ?)",
            (
                ("pending", "pending text", stamp, 0, None, 0),
                ("retry", "retry text", stamp, 1, stamp, 0),
                ("compressed", "compressed text", stamp, 0, None, 1),
                ("quarantined", "quarantined text", stamp, 3, None, -1),
            ),
        )
        conn.execute(
            "CREATE TABLE compressed_memories (memory_id TEXT PRIMARY KEY, "
            "memory_type TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, "
            "timespan_start TEXT NOT NULL, timespan_end TEXT NOT NULL, importance REAL, "
            "confidence REAL, topics TEXT, entities TEXT, source_turns TEXT, "
            "parent_id TEXT, compressed_at TEXT NOT NULL, compression_level INTEGER, "
            "status TEXT, weight REAL)"
        )
        rows = (
            ("scene-root", "scene", None),
            ("event-linked", "event", "scene-root"),
            ("event-orphan", "event", "missing-scene"),
            ("event-source", "event", None),
            ("scene-derived", "scene", "event-source"),
        )
        conn.executemany(
            "INSERT INTO compressed_memories VALUES (?, ?, ?, ?, ?, ?, 0.8, 0.9, "
            "'[]', '[]', '[]', ?, ?, 0, 'active', 1.0)",
            [(item_id, kind, item_id, item_id, stamp, stamp, parent, stamp)
             for item_id, kind, parent in rows],
        )
        conn.commit()
    finally:
        conn.close()

    service = MemoryService(MemoryServiceConfig(db_path=str(db_path)))
    conn = open_memory_sqlite(service._db_path)
    try:
        turn_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(turns)").fetchall()
        }
        memory_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(compressed_memories)").fetchall()
        }
        statuses = dict(
            conn.execute("SELECT turn_id, compression_status FROM turns").fetchall()
        )
        relations = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT memory_id, timeline_parent_id, derived_from_id "
                "FROM compressed_memories WHERE memory_id IN "
                "('event-linked', 'event-orphan', 'scene-derived')"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "compressed_to_tier2" not in turn_columns
    assert "parent_id" not in memory_columns
    assert statuses == {
        "pending": "pending",
        "retry": "retry_wait",
        "compressed": "compressed",
        "quarantined": "quality_quarantined",
    }
    assert relations["event-linked"] == ("scene-root", None)
    assert relations["event-orphan"] == (None, None)
    assert relations["scene-derived"] == (None, "event-source")
    assert len(list((tmp_path / "backups").glob("memory-*.db"))) == 1


def test_time_summary_schema_is_idempotent_and_versioned(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    service._database_bootstrap.reconcile_schema()
    conn = open_memory_sqlite(service._db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        _insert_time_summary(
            conn,
            summary_id="day-a-v1",
            summary_type="day",
            bucket_key="2026-08-17",
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            _insert_time_summary(
                conn,
                summary_id="day-a-v2-premature",
                summary_type="day",
                bucket_key="2026-08-17",
                version=2,
                supersedes_summary_id="day-a-v1",
            )
        conn.execute(
            "UPDATE time_summaries SET status = 'superseded' "
            "WHERE summary_id = 'day-a-v1'"
        )
        _insert_time_summary(
            conn,
            summary_id="day-a-v2",
            summary_type="day",
            bucket_key="2026-08-17",
            version=2,
            supersedes_summary_id="day-a-v1",
        )
        conn.commit()
        rows = conn.execute(
            "SELECT summary_id, version, status, supersedes_summary_id "
            "FROM time_summaries ORDER BY version"
        ).fetchall()
    finally:
        conn.close()

    assert {
        "time_summaries",
        "time_summary_links",
        "session_summary_sources",
    } <= tables
    assert rows == [
        ("day-a-v1", 1, "superseded", None),
        ("day-a-v2", 2, "active", "day-a-v1"),
    ]


def test_time_summary_contract_rejects_invalid_content_and_relations(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    try:
        for summary_id, summary_type, bucket_key, workspace_id in (
            ("month-a", "month", "2026-08", "workspace-a"),
            ("week-a", "week", "2026-W33", "workspace-a"),
            ("day-a", "day", "2026-08-17", "workspace-a"),
            ("session-a", "session", "session-a", "workspace-a"),
            ("week-other", "week", "2026-W33", "workspace-b"),
        ):
            _insert_time_summary(
                conn,
                summary_id=summary_id,
                summary_type=summary_type,
                bucket_key=bucket_key,
                workspace_id=workspace_id,
            )
        conn.executemany(
            "INSERT INTO time_summary_links VALUES (?, ?, ?)",
            (
                ("month-a", "week-a", "2026-09-01"),
                ("week-a", "day-a", "2026-09-01"),
                ("day-a", "session-a", "2026-09-01"),
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="direct levels"):
            conn.execute(
                "INSERT INTO time_summary_links VALUES (?, ?, ?)",
                ("month-a", "day-a", "2026-09-01"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="same-scope"):
            conn.execute(
                "INSERT INTO time_summary_links VALUES (?, ?, ?)",
                ("month-a", "week-other", "2026-09-01"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute(
                "INSERT INTO time_summaries "
                "(summary_id, summary_type, bucket_key, period_start, period_end, "
                "timezone, title, summary, outcomes, source_hash, content_hash, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "invalid-outcomes",
                    "day",
                    "2026-08-19",
                    "2026-08-19T00:00:00+08:00",
                    "2026-08-20T00:00:00+08:00",
                    "Asia/Shanghai",
                    "title",
                    "summary",
                    '{"not": "an array"}',
                    "source-invalid",
                    "hash-invalid",
                    "2026-08-20T00:05:00+08:00",
                    "2026-08-20T00:05:00+08:00",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="versions are immutable"):
            conn.execute(
                "UPDATE time_summaries SET summary = 'silent rewrite' "
                "WHERE summary_id = 'day-a'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="same bucket"):
            _insert_time_summary(
                conn,
                summary_id="day-b-v2",
                summary_type="day",
                bucket_key="2026-08-18",
                version=2,
                supersedes_summary_id="day-a",
            )
    finally:
        conn.close()


def test_memory_export_v2_contains_time_summary_index(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    try:
        _insert_time_summary(
            conn,
            summary_id="session-a",
            summary_type="session",
            bucket_key="session-a",
        )
        conn.commit()
    finally:
        conn.close()

    exported = service._backup_manager.export_json()
    payload = json.loads(
        (tmp_path / "exports" / exported["export_id"]).read_text(encoding="utf-8")
    )

    assert payload["format_version"] == 2
    assert [row["summary_id"] for row in payload["tables"]["time_summaries"]] == [
        "session-a"
    ]
    assert payload["tables"]["time_summary_links"] == []
    assert payload["tables"]["session_summary_sources"] == []


def test_time_summary_timezone_requires_an_iana_zone():
    assert MemoryServiceConfig().time_summary_timezone == "Asia/Shanghai"
    with pytest.raises(ValueError, match="Invalid Memory time-summary timezone"):
        MemoryServiceConfig(time_summary_timezone="not-a-timezone")


def test_day_bucket_uses_configured_local_midnight():
    assert day_bucket_for_timestamp(
        "2026-08-16T15:59:59+00:00",
        timezone_name="Asia/Shanghai",
    ) == "2026-08-16"
    assert day_bucket_for_timestamp(
        "2026-08-16T16:00:00+00:00",
        timezone_name="Asia/Shanghai",
    ) == "2026-08-17"
    assert day_period(
        "2026-08-17",
        timezone_name="Asia/Shanghai",
    ) == (
        "2026-08-17T00:00:00+08:00",
        "2026-08-18T00:00:00+08:00",
    )


@pytest.mark.asyncio
async def test_session_close_summary_is_idempotent_and_revises_on_new_turn(
    tmp_path,
    monkeypatch,
):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    await service.create_session(SessionCreate(session_id="session-close"))
    await service.add_turn(
        "session-close",
        TurnCreate(speaker="user", text="定义会话摘要索引"),
    )

    class _FakeClient:
        def __init__(self):
            self.calls = 0
            self.tasks = []

        def complete_json(self, **kwargs):
            self.calls += 1
            self.tasks.append(kwargs["task"])
            return {
                "title": "建立时间摘要索引",
                "summary": "本次记录确定了永久摘要索引的逐级结构。",
                "outcomes": ["确定会话摘要作为最低永久索引层"],
                "open_questions": [],
            }

    client = _FakeClient()
    monkeypatch.setattr(
        service,
        "_resolve_mem_llm_client",
        lambda role="default": (client, "test-session-model"),
    )
    request = SessionCloseRequest()
    first = await service.close_session("session-close", request)
    second = await service.close_session("session-close", request)

    assert first["write_status"] == "created"
    assert second["write_status"] == "current"
    assert first["version"] == second["version"] == 1
    assert first["day_summary"]["write_status"] == "created"
    assert second["day_summary"]["write_status"] == "current"
    assert client.calls == 2
    assert client.tasks == ["scholar.session_summary", "scholar.day_summary"]

    await service.add_turn(
        "session-close",
        TurnCreate(speaker="agent", text="会话摘要已进入设计契约。"),
    )
    revised = await service.close_session("session-close", request)
    assert revised["write_status"] == "created"
    assert revised["version"] == 2
    assert revised["day_summary"]["version"] == 2
    assert client.calls == 4

    conn = open_memory_sqlite(service._db_path)
    try:
        versions = conn.execute(
            "SELECT version, status FROM time_summaries "
            "WHERE summary_type = 'session' AND bucket_key = 'session-close' "
            "ORDER BY version"
        ).fetchall()
        source_count = conn.execute(
            "SELECT source_count FROM time_summaries WHERE summary_id = ?",
            (revised["summary_id"],),
        ).fetchone()[0]
        linked_sources = conn.execute(
            "SELECT COUNT(*) FROM session_summary_sources WHERE summary_id = ?",
            (revised["summary_id"],),
        ).fetchone()[0]
        day_versions = conn.execute(
            "SELECT version, status, source_count FROM time_summaries "
            "WHERE summary_type = 'day' ORDER BY version"
        ).fetchall()
        day_links = conn.execute(
            "SELECT parent.version, child.version FROM time_summary_links AS link "
            "JOIN time_summaries AS parent ON parent.summary_id = link.parent_summary_id "
            "JOIN time_summaries AS child ON child.summary_id = link.child_summary_id "
            "ORDER BY parent.version"
        ).fetchall()
    finally:
        conn.close()

    assert versions == [(1, "superseded"), (2, "active")]
    assert source_count == linked_sources == 2
    assert day_versions == [(1, "superseded", 1), (2, "active", 1)]
    assert day_links == [(1, 1), (2, 2)]

    forgotten = await service.forget_memory(
        ForgetRequest(
            session_id="session-close",
            reason="用户明确删除测试会话",
            confirmation="FORGET",
        )
    )
    assert forgotten["deleted_counts"]["time_summaries"] == 4
    assert forgotten["deleted_counts"]["time_summary_links"] == 2
    assert forgotten["deleted_counts"]["session_summary_sources"] == 3

    conn = open_memory_sqlite(service._db_path)
    try:
        remaining = conn.execute("SELECT COUNT(*) FROM time_summaries").fetchone()[0]
    finally:
        conn.close()
    assert remaining == 0


@pytest.mark.asyncio
async def test_day_summary_orders_and_links_multiple_session_summaries(
    tmp_path,
    monkeypatch,
):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    day_inputs = []

    class _FakeClient:
        def complete_json(self, **kwargs):
            if kwargs["task"] == "scholar.day_summary":
                day_inputs.append(kwargs["user_payload"]["session_summaries"])
            return {
                "title": "时间摘要",
                "summary": "按时间记录当天完成的工作。",
                "outcomes": ["日摘要已生成"],
                "open_questions": [],
            }

    monkeypatch.setattr(
        service,
        "_resolve_mem_llm_client",
        lambda role="default": (_FakeClient(), "test-day-model"),
    )
    request = SessionCloseRequest()
    for session_id in ("session-first", "session-second"):
        await service.create_session(SessionCreate(session_id=session_id))
        await service.add_turn(
            session_id,
            TurnCreate(speaker="user", text=f"work in {session_id}"),
        )

    first = await service.close_session("session-first", request)
    second = await service.close_session("session-second", request)
    day_key = second["day_summary"]["bucket_key"]
    repeated = await service.aggregate_day(day_key, DayAggregateRequest())

    assert first["day_summary"]["version"] == 1
    assert second["day_summary"]["version"] == 2
    assert second["day_summary"]["source_count"] == 2
    assert repeated["write_status"] == "current"
    assert [item["session_id"] for item in day_inputs[-1]] == [
        "session-first",
        "session-second",
    ]

    conn = open_memory_sqlite(service._db_path)
    try:
        child_ids = conn.execute(
            "SELECT child.bucket_key FROM time_summary_links AS link "
            "JOIN time_summaries AS child ON child.summary_id = link.child_summary_id "
            "WHERE link.parent_summary_id = ? ORDER BY child.period_start, child.bucket_key",
            (second["day_summary"]["summary_id"],),
        ).fetchall()
    finally:
        conn.close()
    assert child_ids == [("session-first",), ("session-second",)]


@pytest.mark.asyncio
async def test_session_time_correction_retires_the_old_day_index(
    tmp_path,
    monkeypatch,
):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))

    class _FakeClient:
        def complete_json(self, **_kwargs):
            return {
                "title": "时间修正",
                "summary": "会话时间已按证据修正。",
                "outcomes": ["目录日期已更新"],
                "open_questions": [],
            }

    monkeypatch.setattr(
        service,
        "_resolve_mem_llm_client",
        lambda role="default": (_FakeClient(), "test-time-correction-model"),
    )
    await service.create_session(SessionCreate(session_id="session-moved"))
    await service.add_turn(
        "session-moved",
        TurnCreate(speaker="user", text="correct my date"),
    )
    first = await service.close_session("session-moved", SessionCloseRequest())
    old_day = first["day_summary"]["bucket_key"]

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = '2020-01-01T01:00:00+08:00' "
            "WHERE session_id = 'session-moved'"
        )
        conn.commit()
    finally:
        conn.close()

    moved = await service.close_session("session-moved", SessionCloseRequest())
    assert moved["day_summary"]["bucket_key"] == "2020-01-01"

    conn = open_memory_sqlite(service._db_path)
    try:
        active_days = conn.execute(
            "SELECT bucket_key FROM time_summaries "
            "WHERE summary_type = 'day' AND status = 'active'"
        ).fetchall()
        old_day_statuses = conn.execute(
            "SELECT status FROM time_summaries "
            "WHERE summary_type = 'day' AND bucket_key = ? ORDER BY version",
            (old_day,),
        ).fetchall()
    finally:
        conn.close()

    assert active_days == [("2020-01-01",)]
    assert old_day_statuses == [("superseded",)]
