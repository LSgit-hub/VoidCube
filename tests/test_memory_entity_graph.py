from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import DurableMemoryCreate, MemoryService, RecallRequest
from systems.memory.database import open_memory_sqlite


pytestmark = [pytest.mark.unit]


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_default_limit=5,
            recall_candidate_limit=100,
            recall_max_context_chars=1200,
        )
    )


def _insert_compressed(
    service: MemoryService,
    *,
    memory_id: str,
    title: str,
    summary: str,
    timestamp: datetime,
    topics: list[str] | None = None,
    entities: list[str] | None = None,
    memory_type: str = "event",
    status: str = "active",
    superseded_by: str | None = None,
    created_at: datetime | None = None,
    owner_id: str = "local-user",
    workspace_id: str = "default",
) -> None:
    conn = open_memory_sqlite(service._db_path)
    try:
        stamp = timestamp.isoformat()
        created = (created_at or timestamp).isoformat()
        conn.execute(
            "INSERT INTO compressed_memories "
            "(memory_id, memory_type, title, summary, timespan_start, timespan_end, "
            "importance, confidence, topics, entities, source_turns, compressed_at, "
            "compression_level, status, superseded_by, weight, event_kind, "
            "owner_id, workspace_id, memory_domain, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 0.8, 0.9, ?, ?, '[]', ?, "
            "0, ?, ?, 0.8, 'decision', ?, ?, 'agent_interaction', ?)",
            (
                memory_id,
                memory_type,
                title,
                summary,
                stamp,
                stamp,
                json.dumps(topics or [], ensure_ascii=False),
                json.dumps(entities or [], ensure_ascii=False),
                stamp,
                status,
                superseded_by,
                owner_id,
                workspace_id,
                created,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _rebuild_graph(service: MemoryService) -> int:
    from systems.memory.entity_graph import rebuild_entity_graph

    conn = open_memory_sqlite(service._db_path)
    try:
        linked = rebuild_entity_graph(conn, owner_id="*", workspace_id="*")
        conn.commit()
    finally:
        conn.close()
    return linked


def test_scoped_graph_rebuild_does_not_delete_other_scopes(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_compressed(
        service, memory_id="scope-a", title="A", summary="A",
        timestamp=now, entities=["alpha"], owner_id="owner-a", workspace_id="ws-a",
    )
    _insert_compressed(
        service, memory_id="scope-b", title="B", summary="B",
        timestamp=now, entities=["beta"], owner_id="owner-b", workspace_id="ws-b",
    )
    from systems.memory.entity_graph import rebuild_entity_graph
    conn = open_memory_sqlite(service._db_path)
    try:
        rebuild_entity_graph(conn, owner_id="*", workspace_id="*")
        conn.commit()
        rebuild_entity_graph(conn, owner_id="owner-a", workspace_id="ws-a")
        conn.commit()
        linked = {row[0] for row in conn.execute("SELECT memory_id FROM entity_memory_links")}
    finally:
        conn.close()
    assert {"scope-a", "scope-b"} <= linked


@pytest.mark.asyncio
async def test_graph_build_creates_nodes_edges_and_links(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_compressed(
        service,
        memory_id="mem-a",
        title="记忆系统设计",
        summary="记忆系统设计方案。",
        timestamp=now,
        topics=["记忆系统"],
        entities=["星子", "记忆系统"],
    )
    _insert_compressed(
        service,
        memory_id="mem-b",
        title="压缩策略",
        summary="记忆的压缩策略。",
        timestamp=now,
        topics=["压缩"],
        entities=["记忆系统", "压缩"],
    )

    linked = _rebuild_graph(service)

    # The MemoryService constructor also seeds identity founding memories, so
    # the graph links those too — we assert our memories are covered.
    assert linked >= 2
    conn = open_memory_sqlite(service._db_path)
    try:
        nodes = conn.execute("SELECT entity_id FROM entity_nodes").fetchall()
        assert {"星子", "记忆系统", "压缩"} <= {row[0] for row in nodes}
        edges = conn.execute("SELECT source_entity, target_entity FROM entity_edges").fetchall()
        pairs = {frozenset((str(a), str(b))) for a, b in edges}
        assert frozenset(("星子", "记忆系统")) in pairs
        assert frozenset(("记忆系统", "压缩")) in pairs
        links = conn.execute(
            "SELECT memory_id FROM entity_memory_links WHERE entity_id = '星子'"
        ).fetchall()
        assert {row[0] for row in links} >= {"mem-a"}
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_explicit_remember_updates_entity_graph_in_same_write(tmp_path):
    service = _service(tmp_path)

    result = await service.remember(
        DurableMemoryCreate(
            title="作用域修复",
            summary="CLI 与 Mem 使用同一工作区。",
            entities=["CLI", "Mem"],
            evidence_refs=["code:scope-contract"],
            event_kind="correction",
            owner_id="local-user",
            workspace_id="VoidCube",
        )
    )

    memory_id = result["memory"]["memory_id"]
    conn = open_memory_sqlite(service._db_path)
    try:
        nodes = {
            row[0]
            for row in conn.execute(
                "SELECT entity_id FROM entity_nodes WHERE owner_id = ? AND workspace_id = ?",
                ("local-user", "VoidCube"),
            )
        }
        links = {
            row[0]
            for row in conn.execute(
                "SELECT entity_id FROM entity_memory_links WHERE memory_id = ?",
                (memory_id,),
            )
        }
        created_at = conn.execute(
            "SELECT created_at FROM compressed_memories WHERE memory_id = ?", (memory_id,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert {"cli", "mem"} <= nodes
    assert links == {"cli", "mem"}
    assert created_at


@pytest.mark.asyncio
async def test_graph_recall_surfaces_neighbor_memory_beyond_lexical_match(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    # mem-b references 星子 but NOT 记忆系统 in its content, so a lexical query
    # for "记忆系统" misses it; the graph surfaces it via the 记忆系统-星子 edge.
    _insert_compressed(
        service,
        memory_id="mem-a",
        title="记忆系统设计",
        summary="记忆系统设计方案与角色定义。",
        timestamp=now,
        topics=["记忆系统"],
        entities=["记忆系统", "星子"],
    )
    _insert_compressed(
        service,
        memory_id="mem-b",
        title="压缩策略",
        summary="记忆的压缩策略把事件凝练为弧线。",
        timestamp=now,
        topics=["压缩"],
        entities=["星子", "压缩"],
    )
    _insert_compressed(
        service,
        memory_id="mem-c",
        title="无关主题",
        summary="完全无关的内容。",
        timestamp=now,
        topics=["其他"],
        entities=["无关"],
    )
    _rebuild_graph(service)

    result = await service.recall(RecallRequest(query="记忆系统", limit=5))

    returned = [item["id"] for item in result["results"]]
    assert "mem-a" in returned
    assert "mem-b" in returned
    assert "mem-c" not in returned
    graph_matches = [
        item["signals"]["graph_proximity"]
        for item in result["results"]
        if item.get("tier") == "graph"
    ]
    assert graph_matches
    assert max(graph_matches) <= 0.6


@pytest.mark.asyncio
async def test_graph_introspection_routes_and_scope(tmp_path):
    service = _service(tmp_path)
    now = datetime.now(timezone.utc)
    _insert_compressed(
        service,
        memory_id="mem-a",
        title="记忆系统设计",
        summary="记忆系统设计方案。",
        timestamp=now,
        topics=["记忆系统"],
        entities=["星子", "记忆系统"],
    )
    _insert_compressed(
        service,
        memory_id="mem-x",
        title="专属主题",
        summary="owner-x 的专属记忆。",
        timestamp=now,
        topics=["专属"],
        entities=["专属实体"],
        owner_id="owner-x",
        workspace_id="workspace-x",
    )
    _rebuild_graph(service)

    entities = await service.list_graph_entities()
    ids = {e["entity_id"] for e in entities["entities"]}
    assert "星子" in ids

    neighbors = await service.get_graph_neighbors("星子")
    assert any(n["neighbor"] == "记忆系统" for n in neighbors["neighbors"])

    # Cross-scope: owner-x sees its own + shared global entities, but not the
    # local-user entity 记忆系统.
    other = await service.list_graph_entities(owner_id="owner-x", workspace_id="workspace-x")
    other_ids = {e["entity_id"] for e in other["entities"]}
    assert "专属实体" in other_ids
    assert "记忆系统" not in other_ids
