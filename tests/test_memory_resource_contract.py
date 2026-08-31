from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import MethodType, SimpleNamespace

import pytest
from fastapi import HTTPException

from memai.application.config import MemoryServiceConfig
from memai.repository.sqlite import open_memory_sqlite
from memai.application.memory_service import (
    CalendarAggregateRequest,
    DayAggregateRequest,
    DurableMemoryCreate,
    ForgetRequest,
    MemoryService,
    SessionCloseRequest,
    SessionCreate,
    TurnCreate,
)
from memai.repository.profile_store import upsert_profile_memory
from memai.repository.profile_store import revoke_profile_predicates
from memai.indexes.entity_graph import rebuild_entity_graph, update_entity_graph
from memai.indexes.lexical_index import search_memory_fts
from memai.application.recall import build_recall_plan, recall_memories
from memai.domain.time_summary import (
    day_bucket_for_timestamp,
    day_period,
    month_bucket_for_timestamp,
    month_period,
    week_bucket_for_timestamp,
    week_period,
)


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
    service._repository.reconcile_schema()
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
    assert week_bucket_for_timestamp(
        "2026-08-16T16:00:00+00:00",
        timezone_name="Asia/Shanghai",
    ) == "2026-W34"
    assert week_period(
        "2026-W34",
        timezone_name="Asia/Shanghai",
    ) == (
        "2026-08-17T00:00:00+08:00",
        "2026-08-24T00:00:00+08:00",
    )
    assert month_bucket_for_timestamp(
        "2026-08-31T16:00:00+00:00",
        timezone_name="Asia/Shanghai",
    ) == "2026-09"
    assert month_period(
        "2026-09",
        timezone_name="Asia/Shanghai",
    ) == (
        "2026-09-01T00:00:00+08:00",
        "2026-10-01T00:00:00+08:00",
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
    assert client.calls == 4
    assert client.tasks == [
        "scholar.session_summary",
        "scholar.day_summary",
        "scholar.week_summary",
        "scholar.month_summary",
    ]

    await service.add_turn(
        "session-close",
        TurnCreate(speaker="agent", text="会话摘要已进入设计契约。"),
    )
    revised = await service.close_session("session-close", request)
    assert revised["write_status"] == "created"
    assert revised["version"] == 2
    assert revised["day_summary"]["version"] == 2
    assert client.calls == 8

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
            "WHERE parent.summary_type = 'day' "
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
    assert forgotten["deleted_counts"]["time_summaries"] == 8
    assert forgotten["deleted_counts"]["time_summary_links"] == 6
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
async def test_week_and_month_summaries_use_direct_children_and_are_idempotent(
    tmp_path,
    monkeypatch,
):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    calls = []

    class _FakeClient:
        def complete_json(self, **kwargs):
            calls.append(
                (
                    kwargs["task"],
                    tuple(
                        item["summary_id"]
                        for item in kwargs["user_payload"].get(
                            "direct_child_summaries",
                            kwargs["user_payload"].get("session_summaries", []),
                        )
                    ),
                )
            )
            return {
                "title": "目录摘要",
                "summary": "只根据直接子摘要记录时间范围内的工作。",
                "outcomes": ["目录链已更新"],
                "open_questions": [],
            }

    monkeypatch.setattr(
        service,
        "_resolve_mem_llm_client",
        lambda role="default": (_FakeClient(), "test-calendar-model"),
    )
    request = SessionCloseRequest()
    for session_id in ("calendar-first", "calendar-second"):
        await service.create_session(SessionCreate(session_id=session_id))
        await service.add_turn(
            session_id,
            TurnCreate(speaker="user", text=f"calendar work {session_id}"),
        )
        await service.close_session(session_id, request)

    first = await service.close_session(
        "calendar-first",
        request,
    )
    week_key = first["week_summary"]["bucket_key"]
    month_key = first["month_summary"]["bucket_key"]
    week = await service.aggregate_week(week_key, CalendarAggregateRequest())
    month = await service.aggregate_month(month_key, CalendarAggregateRequest())

    assert week["write_status"] == "current"
    assert month["write_status"] == "current"
    assert [task for task, _ in calls].count("scholar.week_summary") == 2
    assert [task for task, _ in calls].count("scholar.month_summary") == 2

    conn = open_memory_sqlite(service._db_path)
    try:
        links = conn.execute(
            "SELECT parent.summary_type, child.summary_type "
            "FROM time_summary_links AS link "
            "JOIN time_summaries AS parent ON parent.summary_id = link.parent_summary_id "
            "JOIN time_summaries AS child ON child.summary_id = link.child_summary_id "
            "WHERE parent.status = 'active' ORDER BY parent.summary_type, child.summary_type"
        ).fetchall()
        active = conn.execute(
            "SELECT summary_type, COUNT(*) FROM time_summaries "
            "WHERE status = 'active' GROUP BY summary_type ORDER BY summary_type"
        ).fetchall()
    finally:
        conn.close()

    assert ("day", "session") in links
    assert ("week", "day") in links
    assert ("month", "week") in links
    assert active == [("day", 1), ("month", 1), ("session", 2), ("week", 1)]


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


def test_profile_revoke_removes_derived_entity_graph_records(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        profile = _profile("profile-1", "preferred_language", "中文")
        profile.source_turns = ["turn-profile"]
        upsert_profile_memory(
            conn,
            profile,
            owner_id="local-user",
            workspace_id="default",
            now=now,
        )
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "source_turns, entities, compressed_at, owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'event', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "derived-1",
                "profile-derived",
                "profile-derived",
                now,
                now,
                json.dumps(["turn-profile"]),
                json.dumps(["private-entity"]),
                now,
                "local-user",
                "default",
                "agent_interaction",
            ),
        )
        update_entity_graph(
            conn,
            memory_id="derived-1",
            memory_type="event",
            entities=["private-entity"],
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
            now=now,
        )
        result = revoke_profile_predicates(
            conn,
            ["preferred_language"],
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
            turn_id="turn-profile",
            now=now,
        )
        conn.commit()
        assert result["action"] == "revoked"
        assert not conn.execute(
            "SELECT 1 FROM entity_memory_links WHERE memory_id = ?", ("derived-1",)
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM entity_nodes WHERE entity_id = ?", ("private-entity",)
        ).fetchone()
    finally:
        conn.close()


def test_promotion_record_filter_blocks_entity_graph_bypass(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        for memory_id, summary in (
            ("promoted-source", "Project decision allowed for promotion"),
            ("unpromoted-source", "Project decision must remain private"),
        ):
            conn.execute(
                "INSERT INTO compressed_memories "
                "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                "topics, entities, source_turns, compressed_at, owner_id, workspace_id, memory_domain) "
                "VALUES (?, 'event', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    "Project decision",
                    summary,
                    now,
                    now,
                    json.dumps(["project"]),
                    json.dumps(["project"]),
                    json.dumps([]),
                    now,
                    "local-user",
                    "default",
                    "agent_interaction",
                ),
            )
            update_entity_graph(
                conn,
                memory_id=memory_id,
                memory_type="event",
                entities=["project"],
                owner_id="local-user",
                workspace_id="default",
                memory_domain="agent_interaction",
                now=now,
            )
        conn.commit()
        payload = recall_memories(
            conn,
            build_recall_plan("project decision"),
            include_tier1=False,
            include_tier2=True,
            owner_id="local-user",
            workspace_id="default",
            record_filter={"compressed": ["promoted-source"]},
            min_score=0.0,
        )
        assert "promoted-source" in {item["id"] for item in payload["results"]}
        assert "unpromoted-source" not in {item["id"] for item in payload["results"]}
    finally:
        conn.close()


def test_regular_recall_excludes_founding_memory_from_entity_graph(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "topics, entities, source_turns, compressed_at, status, pinned, "
            "identity_layer, owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'identity', ?, ?, ?, ?, ?, ?, '[]', ?, 'active', 1, "
            "'founding', '*', '*', 'agent_interaction')",
            (
                "founding-project",
                "Founding project",
                "Founding identity linked to graph-only-project-token",
                now,
                now,
                json.dumps(["graph-only-project-token"]),
                json.dumps(["graph-only-project-token"]),
                now,
            ),
        )
        update_entity_graph(
            conn,
            memory_id="founding-project",
            memory_type="identity",
            entities=["graph-only-project-token"],
            owner_id="*",
            workspace_id="*",
            memory_domain="agent_interaction",
            now=now,
        )
        conn.commit()
        plan = build_recall_plan("graph-only-project-token decision")
        assert plan.intent != "identity"
        payload = recall_memories(
            conn,
            plan,
            include_tier1=False,
            include_tier2=True,
            owner_id="local-user",
            workspace_id="default",
            min_score=0.0,
        )
        assert "founding-project" not in {item["id"] for item in payload["results"]}
    finally:
        conn.close()


def test_recent_conversation_prefetch_keeps_newest_low_relevance_turn(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    reference = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    try:
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at) "
            "VALUES ('recent-session', 'local-user', 'default', "
            "'agent_interaction', ?)",
            (reference.isoformat(),),
        )
        older_rows = [
            (
                f"older-{index}",
                f"Older important turn {index}",
                reference.replace(day=10 + index).isoformat(),
                1.0,
            )
            for index in range(5)
        ]
        conn.executemany(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "tags, owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'recent-session', 'user', ?, ?, ?, '[]', "
            "'local-user', 'default', 'agent_interaction')",
            older_rows,
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "tags, owner_id, workspace_id, memory_domain) "
            "VALUES ('newest-low-relevance', 'recent-session', 'user', ?, ?, 0.01, "
            "'[]', 'local-user', 'default', 'agent_interaction')",
            ("Newest turn that must remain recallable", reference.isoformat()),
        )
        conn.commit()

        plan = build_recall_plan("刚才聊了什么", now=reference)
        payload = recall_memories(
            conn,
            plan,
            limit=1,
            candidate_limit=1,
            include_tier2=False,
            min_score=0.0,
            now=reference,
        )
        assert payload["results"][0]["id"] == "newest-low-relevance"
    finally:
        conn.close()


def test_entity_graph_rebuild_keeps_domain_scope_and_excludes_invisible_rows(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    now = datetime.now(timezone.utc).isoformat()

    def insert_memory(
        memory_id: str,
        entity: str,
        domain: str,
        *,
        hidden: int = 0,
        identity_layer: str | None = None,
        source_turns: list[str] | None = None,
    ) -> None:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "entities, source_turns, compressed_at, status, hidden, identity_layer, "
            "owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'event', ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (
                memory_id,
                memory_id,
                memory_id,
                now,
                now,
                json.dumps([entity]),
                json.dumps(source_turns or []),
                now,
                hidden,
                identity_layer,
                "owner-a",
                "workspace-a",
                domain,
            ),
        )
        update_entity_graph(
            conn,
            memory_id=memory_id,
            memory_type="event",
            entities=[entity],
            owner_id="owner-a",
            workspace_id="workspace-a",
            memory_domain=domain,
            now=now,
        )

    try:
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "evaluation-session",
                "owner-a",
                "workspace-a",
                "domain-a",
                now,
            ),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, tags, owner_id, "
            "workspace_id, memory_domain) VALUES (?, ?, 'user', ?, ?, ?, ?, ?, ?)",
            (
                "evaluation-turn",
                "evaluation-session",
                "evaluation input",
                now,
                json.dumps(["evaluation"]),
                "owner-a",
                "workspace-a",
                "domain-a",
            ),
        )
        insert_memory("visible", "visible-entity", "domain-a")
        insert_memory("hidden", "hidden-entity", "domain-a", hidden=1)
        insert_memory(
            "founding",
            "founding-entity",
            "domain-a",
            identity_layer="founding",
        )
        insert_memory(
            "evaluation",
            "evaluation-entity",
            "domain-a",
            source_turns=["evaluation-turn"],
        )
        insert_memory("other-domain", "other-domain-entity", "domain-b")
        linked = rebuild_entity_graph(
            conn,
            owner_id="*",
            workspace_id="*",
            memory_domain="domain-a",
        )
        conn.commit()

        domain_a_links = conn.execute(
            "SELECT memory_id FROM entity_memory_links "
            "WHERE memory_domain = 'domain-a' ORDER BY memory_id"
        ).fetchall()
        domain_b_links = conn.execute(
            "SELECT memory_id FROM entity_memory_links "
            "WHERE memory_domain = 'domain-b' ORDER BY memory_id"
        ).fetchall()
        assert linked == 1
        assert domain_a_links == [("visible",)]
        assert domain_b_links == [("other-domain",)]
    finally:
        conn.close()


def test_entity_graph_update_preserves_display_names_and_is_idempotent(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    conn = open_memory_sqlite(service._db_path)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "entities, source_turns, compressed_at, owner_id, workspace_id, memory_domain) "
            "VALUES ('display-memory', 'event', 'Display names', 'Display names', "
            "?, ?, ?, '[]', ?, 'local-user', 'default', 'agent_interaction')",
            (now, now, json.dumps(["Alpha Name", "Beta Name"]), now),
        )
        for _ in range(2):
            update_entity_graph(
                conn,
                memory_id="display-memory",
                memory_type="event",
                entities=["Alpha Name", "Beta Name"],
                owner_id="local-user",
                workspace_id="default",
                memory_domain="agent_interaction",
                now=now,
            )
        nodes = conn.execute(
            "SELECT entity_id, display_name, reference_count FROM entity_nodes "
            "WHERE owner_id = 'local-user' ORDER BY entity_id"
        ).fetchall()
        edge = conn.execute(
            "SELECT source_entity, target_entity, strength FROM entity_edges "
            "WHERE owner_id = 'local-user'"
        ).fetchone()
        assert nodes == [
            ("alpha name", "Alpha Name", 1),
            ("beta name", "Beta Name", 1),
        ]
        assert edge == ("alpha name", "beta name", 1)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_lifecycle_upgrade_preserves_provenance(tmp_path, monkeypatch):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))

    async def fake_escalate(self, **kwargs):
        return "Project scene", "Project scene preserves the durable decision and evidence."

    monkeypatch.setattr(service, "_llm_escalate_summary", MethodType(fake_escalate, service))
    old = "2020-01-01T00:00:00+00:00"
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
                "topics, entities, source_turns, evidence_refs, origin_type, origin_id, "
                "verified_at, compressed_at, compression_level, status, weight, owner_id, workspace_id, memory_domain) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-provenance",
                "event",
                "Project decision",
                "The project made a durable decision and completed the retrieval work with evidence tracking.",
                old,
                old,
                json.dumps(["project"]),
                json.dumps(["project"]),
                json.dumps(["turn-1"]),
                json.dumps(["turn:turn-1"]),
                "explicit",
                "origin-1",
                old,
                old,
                0,
                "active",
                1.0,
                "local-user",
                "default",
                "agent_interaction",
            ),
        )
        update_entity_graph(
            conn,
            memory_id="event-provenance",
            memory_type="event",
            entities=["project"],
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
            now=old,
        )
        conn.commit()
    finally:
        conn.close()

    result = await service._apply_compression_lifecycle()
    assert result["escalated"] == 1
    conn = open_memory_sqlite(service._db_path)
    try:
        successor = conn.execute(
            "SELECT memory_id, evidence_refs, origin_type, origin_id, verified_at, derived_from_id "
            "FROM compressed_memories WHERE derived_from_id = ?",
            ("event-provenance",),
        ).fetchone()
        graph_links = conn.execute(
            "SELECT memory_id FROM entity_memory_links WHERE entity_id = 'project'"
        ).fetchall()
        reference_count = conn.execute(
            "SELECT reference_count FROM entity_nodes WHERE entity_id = 'project'"
        ).fetchone()
    finally:
        conn.close()
    assert successor[1:] == (
        '["turn:turn-1"]',
        "explicit",
        "origin-1",
        old,
        "event-provenance",
    )
    assert graph_links == [(successor[0],)]
    assert reference_count == (1,)


@pytest.mark.asyncio
async def test_lifecycle_upgrade_does_not_invent_source_turn_id(tmp_path, monkeypatch):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))

    async def fake_escalate(self, **kwargs):
        return "Project scene", "Project scene preserves the durable decision."

    monkeypatch.setattr(service, "_llm_escalate_summary", MethodType(fake_escalate, service))
    old = "2020-01-01T00:00:00+00:00"
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "topics, entities, source_turns, compressed_at, compression_level, "
            "status, weight, owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'event', ?, ?, ?, ?, '[]', '[]', '[]', ?, 0, "
            "'active', 1.0, 'local-user', 'default', 'agent_interaction')",
            (
                "event-without-source-turn",
                "Project decision",
                "The project completed a durable decision with sufficient detail.",
                old,
                old,
                old,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = await service._apply_compression_lifecycle()
    assert result["escalated"] == 1
    conn = open_memory_sqlite(service._db_path)
    try:
        successor = conn.execute(
            "SELECT source_turns FROM compressed_memories WHERE derived_from_id = ?",
            ("event-without-source-turn",),
        ).fetchone()
    finally:
        conn.close()
    assert successor == ("[]",)


@pytest.mark.asyncio
async def test_lifecycle_does_not_resurface_hidden_memories(tmp_path, monkeypatch):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))

    async def fail_escalate(self, **kwargs):
        pytest.fail("hidden memory must not enter lifecycle escalation")

    monkeypatch.setattr(service, "_llm_escalate_summary", MethodType(fail_escalate, service))
    old = "2020-01-01T00:00:00+00:00"
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "topics, entities, source_turns, compressed_at, compression_level, "
            "status, hidden, weight, owner_id, workspace_id, memory_domain) "
            "VALUES (?, 'event', ?, ?, ?, ?, '[]', '[]', '[]', ?, 0, "
            "'active', 1, 1.0, 'local-user', 'default', 'agent_interaction')",
            (
                "hidden-lifecycle-memory",
                "Hidden decision",
                "An evaluation decision that must stay quarantined.",
                old,
                old,
                old,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = await service._apply_compression_lifecycle()
    assert result["escalated"] == 0


@pytest.mark.asyncio
async def test_lifecycle_rolls_back_when_entity_graph_rebuild_fails(
    tmp_path, monkeypatch
):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "topics, entities, compressed_at, compression_level, status, source_turns) "
            "VALUES ('event-transactional', 'event', 'Release', "
            "'Release completed with durable evidence.', ?, ?, ?, ?, ?, 0, 'active', '[]')",
            (old, old, json.dumps(["release"]), json.dumps(["project"]), old),
        )
        conn.commit()
    finally:
        conn.close()

    async def escalate(self, **kwargs):
        return "Release scene", "Release completed with durable evidence."

    def fail_rebuild(*args, **kwargs):
        raise RuntimeError("entity graph unavailable")

    monkeypatch.setattr(
        service,
        "_llm_escalate_summary",
        MethodType(escalate, service),
    )
    monkeypatch.setattr(
        "memai.indexes.entity_graph.rebuild_entity_graph",
        fail_rebuild,
    )

    with pytest.raises(RuntimeError, match="entity graph unavailable"):
        await service._apply_compression_lifecycle()

    conn = open_memory_sqlite(service._db_path)
    try:
        original = conn.execute(
            "SELECT status, superseded_by FROM compressed_memories "
            "WHERE memory_id = 'event-transactional'"
        ).fetchone()
        successor_count = conn.execute(
            "SELECT COUNT(*) FROM compressed_memories "
            "WHERE derived_from_id = 'event-transactional'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert original == ("active", None)
    assert successor_count == 0


def test_long_fts_anchor_does_not_mix_two_character_fallback(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.executemany(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "topics, entities, source_turns, compressed_at, status) "
            "VALUES (?, 'event', ?, ?, ?, ?, '[]', '[]', '[]', ?, 'active')",
            (
                (
                    "fts-long-anchor",
                    "记忆系统设计",
                    "记忆系统设计方案与角色定义。",
                    now,
                    now,
                    now,
                ),
                (
                    "fts-short-only",
                    "压缩策略",
                    "记忆的压缩策略把事件凝练为弧线。",
                    now,
                    now,
                    now,
                ),
            ),
        )
        conn.commit()

        matches = search_memory_fts(
            conn,
            ("记忆系统", "记忆"),
            owner_id="local-user",
            workspace_id="default",
            limit=20,
        )
    finally:
        conn.close()

    assert matches["compressed"] == ("fts-long-anchor",)


@pytest.mark.asyncio
async def test_hide_and_unhide_reconcile_entity_graph(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "entities, source_turns, compressed_at, status, owner_id, workspace_id, "
            "memory_domain) VALUES (?, 'event', ?, ?, ?, ?, ?, '[]', ?, 'active', "
            "'local-user', 'default', 'agent_interaction')",
            (
                "graph-visibility-memory",
                "Private project",
                "Private project details",
                now,
                now,
                json.dumps(["private-project-entity"]),
                now,
            ),
        )
        update_entity_graph(
            conn,
            memory_id="graph-visibility-memory",
            memory_type="event",
            entities=["private-project-entity"],
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
            now=now,
        )
        conn.commit()
    finally:
        conn.close()

    await service.hide_memory("graph-visibility-memory")
    conn = open_memory_sqlite(service._db_path)
    try:
        assert not conn.execute(
            "SELECT 1 FROM entity_nodes WHERE entity_id = ?",
            ("private-project-entity",),
        ).fetchone()
    finally:
        conn.close()

    await service.unpin_memory("graph-visibility-memory")
    conn = open_memory_sqlite(service._db_path)
    try:
        assert conn.execute(
            "SELECT 1 FROM entity_nodes WHERE entity_id = ?",
            ("private-project-entity",),
        ).fetchone()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_entity_graph_ports_enforce_domain_authorization(tmp_path):
    service = MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))
    await service.remember(
        DurableMemoryCreate(
            title="Companion graph fact",
            summary="Companion graph authorization marker.",
            entities=["companion-private-entity"],
            memory_actor="stellar_companion",
            memory_domain="companion",
        )
    )

    with pytest.raises(HTTPException) as list_denied:
        await service.list_graph_entities(source_domains="companion")
    assert list_denied.value.status_code == 403

    with pytest.raises(HTTPException) as neighbors_denied:
        await service.get_graph_neighbors(
            "companion-private-entity",
            source_domains="companion",
        )
    assert neighbors_denied.value.status_code == 403

    with pytest.raises(HTTPException) as rebuild_denied:
        await service.rebuild_entity_graph(memory_domain="companion")
    assert rebuild_denied.value.status_code == 403

    visible = await service.list_graph_entities(
        source_domains="companion",
        memory_actor="stellar_companion",
    )
    assert "companion-private-entity" in {
        item["entity_id"] for item in visible["entities"]
    }
