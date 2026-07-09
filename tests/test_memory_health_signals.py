from __future__ import annotations

import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.memory import memory_service as memory_service_module
from systems.memory import tier1_to_tier2_bridge as bridge_module
from systems.memory.memory_service import (
    MemoryService,
    MemoryServiceConfig,
    SessionCreate,
    Tier2CompressRequest,
    TurnCreate,
    _write_compressed_memories,
)
from systems.memory.tier1_to_tier2_bridge import Tier1ToTier2Bridge
from systems.memory.tier1_to_tier2_bridge import open_memory_sqlite


def _make_service(tmp_path: Path) -> MemoryService:
    cfg = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"))
    return MemoryService(cfg)


def test_memory_sqlite_connections_use_busy_timeout_and_wal(tmp_path):
    db_path = tmp_path / "mem.db"
    conn = open_memory_sqlite(db_path)
    try:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()

    assert busy_timeout >= 30000
    assert str(journal_mode).lower() == "wal"


@pytest.mark.asyncio
async def test_summarize_memory_marks_truncation_fallback_as_degraded(tmp_path):
    svc = _make_service(tmp_path)
    svc._resolve_mem_llm_client = lambda: (None, "none")  # type: ignore[method-assign]
    await svc.write_memory(
        {
            "memory_id": "m-summary",
            "namespace": "default",
            "content": "Important memory content. " * 40,
        }
    )

    result = await svc.summarize_memory("m-summary")

    conn = open_memory_sqlite(svc._db_path)
    try:
        metadata_json = conn.execute(
            "SELECT metadata FROM memories WHERE memory_id = ?",
            ("m-summary",),
        ).fetchone()[0]
    finally:
        conn.close()
    metadata = json.loads(metadata_json)

    assert result["status"] == "summarized"
    assert result["summary_degraded"] is True
    assert result["summary_method"] == "truncation_fallback"
    assert metadata["summary_degraded"] is True
    assert metadata["summary_method"] == "truncation_fallback"


@pytest.mark.asyncio
async def test_compress_memories_marks_truncation_fallback_as_degraded(tmp_path):
    svc = _make_service(tmp_path)
    svc._resolve_mem_llm_client = lambda: (None, "none")  # type: ignore[method-assign]
    for idx in range(2):
        await svc.write_memory(
            {
                "memory_id": f"m-compress-{idx}",
                "namespace": "compress-src",
                "content": f"Compressible memory {idx}. " * 40,
                "relevance_score": 1.0 - idx * 0.1,
            }
        )

    result = await svc.compress_memories(
        {"namespace": "compress-src", "max_entries": 2, "target_size": 10}
    )

    conn = open_memory_sqlite(svc._db_path)
    try:
        metadata_json = conn.execute(
            "SELECT metadata FROM memories WHERE namespace = ?",
            ("compress-src_compressed",),
        ).fetchone()[0]
    finally:
        conn.close()
    metadata = json.loads(metadata_json)

    assert result["status"] == "compressed"
    assert result["summary_degraded"] is True
    assert result["summary_method"] == "truncation_fallback"
    assert metadata["summary_degraded"] is True
    assert metadata["summary_method"] == "truncation_fallback"


# ── 4-6.1: memory_active must reflect real write work, not "a rule ran" ──

def test_rule_effective_count_across_return_shapes():
    f = MemoryService._rule_effective_count
    assert f(0) == 0
    assert f(7) == 7
    assert f(-3) == 0
    assert f({"escalated": 2, "purged": 3}) == 5
    assert f({"turns_processed": 4}) == 4
    assert f({"deleted": 6}) == 6
    assert f({"error": "boom"}) == 0
    assert f(None) == 0


@pytest.mark.asyncio
async def test_noop_cycle_does_not_stamp_effective_activity(tmp_path):
    # Empty DB → every rule is a no-op (0 rows). last_run advances, but the
    # effective-activity marker must stay None so the UI does not show active.
    svc = _make_service(tmp_path)
    result = await svc._run_all_rules_internal()
    assert result["_effective_work"] == 0
    assert svc._last_effective_activity_at is None
    # last_run still advances (the rule did execute)
    assert svc._last_rule_run.get("tier1_decay") is not None


@pytest.mark.asyncio
async def test_effective_activity_stamped_when_decay_writes_rows(tmp_path):
    # Seed a live turn so tier1_decay actually updates a row → effective work.
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="s1", metadata={}))
    await svc.add_turn("s1", TurnCreate(speaker="user", text="hello", metadata={}))

    result = await svc._run_all_rules_internal()
    assert result["_effective_work"] >= 1
    assert svc._last_effective_activity_at is not None


@pytest.mark.asyncio
async def test_rules_status_exposes_effective_activity_and_llm_check_marker(tmp_path):
    svc = _make_service(tmp_path)
    status = await svc.rules_status()
    assert "effective_activity_at" in status
    assert "llm_health_checked_at" in status
    assert "llm_healthy" in status


def test_rules_status_route_is_not_shadowed_by_compressed_memory_id(tmp_path):
    svc = _make_service(tmp_path)
    client = TestClient(svc.app)

    response = client.get("/compressed/rules-status")

    assert response.status_code == 200
    assert "llm_healthy" in response.json()


@pytest.mark.asyncio
async def test_llm_health_records_configured_model_when_key_missing(tmp_path):
    svc = _make_service(tmp_path)
    svc._resolve_mem_llm_client = lambda: (None, "deepseek-v4-flash")  # type: ignore[method-assign]

    ok = await svc._check_llm_health()

    assert ok is False
    assert await svc.llm_health() == {
        "healthy": False,
        "model": "deepseek-v4-flash",
        "error": "llm_client_unavailable",
    }


@pytest.mark.asyncio
async def test_llm_health_preserves_model_when_probe_fails(tmp_path, caplog):
    svc = _make_service(tmp_path)

    class _FailingClient:
        def complete_json(self, **kwargs):
            raise RuntimeError("remote auth failed")

    svc._resolve_mem_llm_client = lambda: (_FailingClient(), "deepseek-v4-flash")  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="memory_service"):
        ok = await svc._check_llm_health()

    assert ok is False
    assert await svc.llm_health() == {
        "healthy": False,
        "model": "deepseek-v4-flash",
        "error": "RuntimeError: remote auth failed",
    }
    assert "LLM health check failed for model=deepseek-v4-flash" in caplog.text


@pytest.mark.asyncio
async def test_rule_failure_is_logged_as_warning(tmp_path, caplog):
    svc = _make_service(tmp_path)

    async def failing_decay():
        raise RuntimeError("decay boom")

    svc._tier1_decay_cycle = failing_decay  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="memory_service"):
        result = await svc._run_all_rules_internal()

    assert result["tier1_decay"]["error"] == "decay boom"
    assert "Memory maintenance rule tier1_decay failed" in caplog.text


@pytest.mark.asyncio
async def test_lifecycle_escalation_preserves_original_source_turns_and_parent_link(tmp_path):
    svc = _make_service(tmp_path)
    old_compressed_at = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, parent_id, "
            "compressed_at, compression_level, status, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "event-child-1",
                "event",
                "Child event",
                "Original event summary",
                old_compressed_at,
                old_compressed_at,
                0.8,
                0.9,
                json.dumps(["memory"]),
                json.dumps(["VoidCube"]),
                json.dumps(["turn-1", "turn-2"]),
                None,
                old_compressed_at,
                0,
                "active",
                1.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    async def fake_escalate_summary(**kwargs):
        return "Parent scene", "Escalated scene summary"

    svc._llm_escalate_summary = fake_escalate_summary  # type: ignore[method-assign]

    result = await svc._apply_compression_lifecycle()

    conn = open_memory_sqlite(svc._db_path)
    try:
        parent = conn.execute(
            "SELECT memory_id, parent_id, source_turns, memory_type, compression_level "
            "FROM compressed_memories WHERE memory_type = 'scene'",
        ).fetchone()
        child = conn.execute(
            "SELECT status, superseded_by FROM compressed_memories WHERE memory_id = ?",
            ("event-child-1",),
        ).fetchone()
    finally:
        conn.close()

    assert result["escalated"] == 1
    assert parent[1] == "event-child-1"
    assert json.loads(parent[2]) == ["turn-1", "turn-2"]
    assert parent[3] == "scene"
    assert parent[4] == 1
    assert child[0] == "superseded"
    assert child[1] == parent[0]


@pytest.mark.asyncio
async def test_tier1_add_turn_deduplicates_explicit_idempotency_key(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="dedup", metadata={}))
    first = await svc.add_turn(
        "dedup",
        TurnCreate(
            speaker="agent",
            text="same persisted finding",
            metadata={"turn_dedup_key": "finding-1"},
        ),
    )
    second = await svc.add_turn(
        "dedup",
        TurnCreate(
            speaker="agent",
            text="same persisted finding",
            metadata={"turn_dedup_key": "finding-1"},
        ),
    )

    turns = await svc.get_session_turns("dedup")

    assert first["status"] == "created"
    assert second["status"] == "deduplicated"
    assert second["turn_id"] == first["turn_id"]
    assert turns["total"] == 1


@pytest.mark.asyncio
async def test_tier1_add_turn_derives_dedup_key_from_task_metadata(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="task-dedup", metadata={}))
    payload = TurnCreate(
        speaker="agent",
        text="Autonomous-chain finding written through gateway",
        metadata={"task_id": "task-42", "source": "autonomous_task_finding"},
    )

    first = await svc.add_turn("task-dedup", payload)
    second = await svc.add_turn("task-dedup", payload)

    turns = await svc.get_session_turns("task-dedup")

    assert first["status"] == "created"
    assert second["status"] == "deduplicated"
    assert second["turn_id"] == first["turn_id"]
    assert first["dedup_key"].startswith("auto_")
    assert turns["total"] == 1


@pytest.mark.asyncio
async def test_tier1_add_turn_auto_creates_missing_session_atomically(tmp_path):
    svc = _make_service(tmp_path)

    created = await svc.add_turn(
        "auto-session",
        TurnCreate(speaker="user", text="hello from gateway", metadata={}),
    )
    session = await svc.get_session("auto-session")
    turns = await svc.get_session_turns("auto-session")

    assert created["status"] == "created"
    assert session["session_id"] == "auto-session"
    assert session["turn_count"] == 1
    assert turns["total"] == 1
    assert turns["turns"][0]["text"] == "hello from gateway"


@pytest.mark.asyncio
async def test_semantic_search_reports_keyword_fallback_when_embedding_unavailable(tmp_path):
    svc = _make_service(tmp_path)
    svc._resolve_mem_llm_client = lambda: (None, "none")  # type: ignore[method-assign]
    now = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "compressed_at, compression_level, status, weight) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cmem-keyword-fallback",
                "event",
                "Needle architecture decision",
                "The team chose a simpler memory bridge.",
                now,
                now,
                now,
                0,
                "active",
                1.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = await svc.semantic_search(
        {"query": "Needle", "limit": 5, "min_similarity": 0.9}
    )

    assert result["method"] == "keyword_fallback"
    assert result["semantic_degraded"] is True
    assert result["embedding_method"] == "llm_unavailable"
    assert result["ignored_min_similarity"] == 0.9
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_tier2_compress_force_oldest_handles_recent_overflow(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="overflow", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="user", text="recent turn 1", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="agent", text="recent turn 2", metadata={}))

    age_only = await svc.tier2_compress(
        Tier2CompressRequest(retention_days=30, batch_size=10, min_relevance=0.0, dry_run=True)
    )
    forced = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.0,
            dry_run=True,
            force_oldest=True,
        )
    )

    assert age_only["status"] == "no_candidates"
    assert forced["status"] == "dry_run"
    assert forced["force_oldest"] is True
    assert forced["candidate_count"] == 2


@pytest.mark.asyncio
async def test_tier2_compress_includes_expired_low_relevance_turns(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="low-rel", metadata={}))
    turn = await svc.add_turn(
        "low-rel",
        TurnCreate(speaker="user", text="old low relevance memory", metadata={}),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ?, relevance_score = ? WHERE turn_id = ?",
            (old_timestamp, 0.01, turn["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.5,
            dry_run=True,
        )
    )

    assert result["status"] == "dry_run"
    assert result["candidate_count"] == 1
    assert result["low_relevance_fallback"] is True
    assert result["sample_turn_ids"] == [turn["turn_id"]]


@pytest.mark.asyncio
async def test_tier2_bridge_cycle_uses_force_oldest_when_max_turns_exceeded(tmp_path):
    cfg = MemoryServiceConfig(db_path=str(tmp_path / "mem.db"), tier1_max_turns=2)
    svc = MemoryService(cfg)
    await svc.create_session(SessionCreate(session_id="overflow", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="user", text="recent turn 1", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="agent", text="recent turn 2", metadata={}))
    captured = {}

    async def fake_tier2_compress(request):
        captured["force_oldest"] = request.force_oldest
        captured["batch_size"] = request.batch_size
        return {"status": "compressed", "turns_processed": 2, "events_generated": 0}

    svc.tier2_compress = fake_tier2_compress  # type: ignore[method-assign]

    processed = await svc._tier2_bridge_cycle()

    assert processed == 2
    assert captured["force_oldest"] is True


@pytest.mark.asyncio
async def test_standalone_tier2_bridge_finds_recent_overflow_candidates(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="overflow", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="user", text="recent turn 1", metadata={}))
    await svc.add_turn("overflow", TurnCreate(speaker="agent", text="recent turn 2", metadata={}))

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=2,
    )

    candidates = bridge.find_candidate_turns()

    assert [candidate["text"] for candidate in candidates] == ["recent turn 1", "recent turn 2"]


@pytest.mark.asyncio
async def test_standalone_tier2_bridge_finds_expired_low_relevance_candidates(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="low-rel", metadata={}))
    turn = await svc.add_turn(
        "low-rel",
        TurnCreate(speaker="user", text="old low relevance bridge memory", metadata={}),
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    conn = open_memory_sqlite(svc._db_path)
    try:
        conn.execute(
            "UPDATE turns SET timestamp = ?, relevance_score = ? WHERE turn_id = ?",
            (old_timestamp, 0.01, turn["turn_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.5,
        max_turns=100,
    )

    candidates = bridge.find_candidate_turns()

    assert [candidate["turn_id"] for candidate in candidates] == [turn["turn_id"]]


@pytest.mark.asyncio
async def test_tier2_compress_keeps_turns_uncompressed_when_no_events_generated(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="empty-events", metadata={}))
    await svc.add_turn("empty-events", TurnCreate(speaker="user", text="plain chatter", metadata={}))

    class _EmptyPipeline:
        def ingest(self, turns):
            return SimpleNamespace(events=[], scenes=[], arcs=[], epochs=[], profile_memories=[])

    svc._build_compression_pipeline = lambda: _EmptyPipeline()  # type: ignore[method-assign]

    result = await svc.tier2_compress(
        Tier2CompressRequest(
            retention_days=30,
            batch_size=10,
            min_relevance=0.0,
            force_oldest=True,
        )
    )

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compressed_to_tier2 FROM turns WHERE session_id = ?",
            ("empty-events",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result["status"] == "no_events_generated"
    assert result["turns_processed"] == 0
    assert compressed == 0
    assert archive_count == 0


@pytest.mark.asyncio
async def test_standalone_bridge_keeps_turns_uncompressed_when_no_events_generated(tmp_path):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="empty-events", metadata={}))
    await svc.add_turn("empty-events", TurnCreate(speaker="user", text="plain chatter", metadata={}))

    class _EmptyPipeline:
        def ingest(self, turns):
            return SimpleNamespace(events=[], scenes=[], arcs=[], epochs=[], profile_memories=[])

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=1,
    )
    bridge._build_pipeline = lambda: _EmptyPipeline()  # type: ignore[method-assign]

    result = bridge.run_cycle()

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compressed_to_tier2 FROM turns WHERE session_id = ?",
            ("empty-events",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result.turns_processed == 0
    assert result.candidate_count == 1
    assert compressed == 0
    assert archive_count == 0


@pytest.mark.asyncio
async def test_tier2_compress_rolls_back_archive_when_compressed_write_fails(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="rollback", metadata={}))
    await svc.add_turn("rollback", TurnCreate(speaker="user", text="decision memory", metadata={}))
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    event = SimpleNamespace(
        id="event-random",
        parent_ids=[],
        event_kind="decision",
        title="Decision",
        summary="A durable decision.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _Pipeline:
        def ingest(self, turns):
            event.source_turns = [turns[0].turn_id]
            return SimpleNamespace(events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[])

    def fail_write(conn, pipeline_result, now):
        raise RuntimeError("compressed write failed")

    svc._build_compression_pipeline = lambda: _Pipeline()  # type: ignore[method-assign]
    monkeypatch.setattr(memory_service_module, "_write_compressed_memories", fail_write)

    with pytest.raises(RuntimeError, match="compressed write failed"):
        await svc.tier2_compress(
            Tier2CompressRequest(
                retention_days=30,
                batch_size=10,
                min_relevance=0.0,
                force_oldest=True,
            )
        )

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compressed_to_tier2 FROM turns WHERE session_id = ?",
            ("rollback",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert compressed == 0
    assert archive_count == 0


@pytest.mark.asyncio
async def test_standalone_bridge_rolls_back_archive_when_compressed_write_fails(tmp_path, monkeypatch):
    svc = _make_service(tmp_path)
    await svc.create_session(SessionCreate(session_id="rollback", metadata={}))
    await svc.add_turn("rollback", TurnCreate(speaker="user", text="decision memory", metadata={}))
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    event = SimpleNamespace(
        id="event-random",
        parent_ids=[],
        event_kind="decision",
        title="Decision",
        summary="A durable decision.",
        timespan_start=now_dt,
        timespan_end=now_dt,
        importance=0.8,
        confidence=0.9,
        topics=["memory"],
        entities=["VoidCube"],
        source_turns=[],
    )
    event.to_dict = lambda: {
        "id": event.id,
        "title": event.title,
        "summary": event.summary,
        "source_turns": list(event.source_turns),
    }

    class _Pipeline:
        def ingest(self, turns):
            event.source_turns = [turns[0].turn_id]
            return SimpleNamespace(events=[event], scenes=[], arcs=[], epochs=[], profile_memories=[])

    def fail_write(conn, pipeline_result, now):
        raise RuntimeError("compressed write failed")

    bridge = Tier1ToTier2Bridge(
        svc._db_path,
        retention_days=30,
        batch_size=10,
        min_relevance=0.0,
        max_turns=1,
    )
    bridge._build_pipeline = lambda: _Pipeline()  # type: ignore[method-assign]
    monkeypatch.setattr(bridge_module, "_write_compressed_memories_to_db", fail_write)

    result = bridge.run_cycle()

    conn = sqlite3.connect(str(svc._db_path))
    try:
        compressed = conn.execute(
            "SELECT compressed_to_tier2 FROM turns WHERE session_id = ?",
            ("rollback",),
        ).fetchone()[0]
        archive_count = conn.execute("SELECT COUNT(*) FROM turns_archive").fetchone()[0]
    finally:
        conn.close()

    assert result.turns_processed == 1
    assert result.errors == ["compressed write failed"]
    assert compressed == 0
    assert archive_count == 0


def test_compressed_memory_write_uses_stable_ids_for_duplicate_events(tmp_path):
    svc = _make_service(tmp_path)
    now_dt = datetime(2026, 7, 2, tzinfo=timezone.utc)

    def result_with_event(event_id: str):
        event = SimpleNamespace(
            id=event_id,
            parent_ids=[],
            event_kind="decision",
            title="Stable decision",
            summary="The same source turn produced the same event.",
            timespan_start=now_dt,
            timespan_end=now_dt,
            importance=0.8,
            confidence=0.9,
            topics=["memory"],
            entities=["VoidCube"],
            source_turns=["turn-1"],
        )
        scene = SimpleNamespace(
            id=f"scene_for_{event_id}",
            parent_ids=[],
            child_ids=[event_id],
            evidence_refs=[event_id],
            title="Stable scene",
            summary="The same event group produced the same scene.",
            timespan_start=now_dt,
            timespan_end=now_dt,
            importance=0.7,
            confidence=0.8,
            topics=["memory"],
            entities=["VoidCube"],
        )
        return SimpleNamespace(events=[event], scenes=[scene], arcs=[], epochs=[])

    conn = sqlite3.connect(str(svc._db_path))
    try:
        _write_compressed_memories(conn, result_with_event("event_random_a"), now_dt.isoformat())
        _write_compressed_memories(conn, result_with_event("event_random_b"), now_dt.isoformat())
        conn.commit()
        rows = conn.execute(
            "SELECT memory_id, memory_type, event_kind FROM compressed_memories ORDER BY memory_type"
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 2
    assert [row[1] for row in rows] == ["event", "scene"]
    assert rows[0][0].startswith("event_")
    assert rows[1][0].startswith("scene_")
    assert [row[2] for row in rows] == ["decision", "decision"]
