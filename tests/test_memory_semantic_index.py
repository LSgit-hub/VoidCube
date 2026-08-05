from __future__ import annotations

import hashlib
import http.server
import json
import threading
from datetime import datetime, timezone

import pytest

from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.semantic_index import SemanticIndexConfig, SemanticMemoryIndex
from systems.memory.database import open_memory_sqlite
from systems.memory.config import MemoryServiceConfig


pytestmark = [pytest.mark.unit]


def _vectors(texts):
    vectors = []
    for text in texts:
        value = str(text).lower()
        if "database migration" in value or "数据库迁移" in value:
            vectors.append([1.0, 0.0, 0.0])
        elif "private-b" in value:
            vectors.append([0.0, 1.0, 0.0])
        else:
            vectors.append([0.0, 0.0, 1.0])
    return vectors


def _service(tmp_path) -> MemoryService:
    return MemoryService(MemoryServiceConfig(db_path=str(tmp_path / "memory.db")))


def _insert_turn(
    service: MemoryService,
    *,
    turn_id: str,
    text: str,
    owner_id: str = "owner-a",
    workspace_id: str = "workspace-a",
) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sessions "
            "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, '{}')",
            (f"session-{turn_id}", owner_id, workspace_id, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
            "VALUES (?, ?, 'user', ?, ?, 1.0, 0.01, '[]', '{}', 0, ?, ?)",
            (
                turn_id,
                f"session-{turn_id}",
                text,
                stamp,
                owner_id,
                workspace_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _index(service: MemoryService) -> SemanticMemoryIndex:
    return SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(
            enabled=True,
            provider="test-provider",
            model="test-embedding-v2",
            dimensions=3,
        ),
        transport=_vectors,
    )


def test_semantic_index_excludes_founding_identity(tmp_path):
    service = _service(tmp_path)
    index = _index(service)

    assert index.index_pending() == 0
    conn = open_memory_sqlite(service._db_path)
    try:
        indexed = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE source_type = 'compressed' "
            "AND memory_id LIKE 'identity-founding-%'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert indexed == 0


def test_semantic_index_primary_key_contains_full_scope(tmp_path):
    service = _service(tmp_path)
    _index(service)
    conn = open_memory_sqlite(service._db_path)
    try:
        table_info = conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()
    finally:
        conn.close()

    primary_key = [
        str(row[1])
        for row in sorted(table_info, key=lambda item: int(item[5] or 0))
        if int(row[5] or 0) > 0
    ]
    assert primary_key == [
        "source_type",
        "memory_id",
        "owner_id",
        "workspace_id",
        "memory_domain",
        "provider",
        "model",
    ]


def test_semantic_index_rebuilds_legacy_scope_primary_key(tmp_path):
    service = _service(tmp_path)
    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute("DROP TABLE memory_embeddings")
        conn.execute(
            "CREATE TABLE memory_embeddings ("
            "source_type TEXT NOT NULL, memory_id TEXT NOT NULL, "
            "owner_id TEXT NOT NULL, workspace_id TEXT NOT NULL, "
            "memory_domain TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "provider TEXT NOT NULL, model TEXT NOT NULL, dimensions INTEGER NOT NULL, "
            "vector TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "PRIMARY KEY(source_type, memory_id, memory_domain, provider, model))"
        )
        conn.execute(
            "INSERT INTO memory_embeddings VALUES "
            "('turn', 'legacy', 'owner-a', 'workspace-a', 'agent_interaction', "
            "'hash', 'test-provider', 'test-model', 3, '[1,0,0]', 'now')"
        )
        conn.commit()
    finally:
        conn.close()

    _index(service)
    conn = open_memory_sqlite(service._db_path)
    try:
        table_info = conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()
        row_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    finally:
        conn.close()

    primary_key = [
        str(row[1])
        for row in sorted(table_info, key=lambda item: int(item[5] or 0))
        if int(row[5] or 0) > 0
    ]
    assert primary_key == [
        "source_type",
        "memory_id",
        "owner_id",
        "workspace_id",
        "memory_domain",
        "provider",
        "model",
    ]
    assert row_count == 0


def test_semantic_index_persists_version_dimensions_and_rebuilds_changed_content(
    tmp_path,
):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="changing", text="数据库迁移采用蓝绿方案。")
    index = _index(service)

    assert index.index_pending() >= 1
    conn = open_memory_sqlite(service._db_path)
    try:
        before = conn.execute(
            "SELECT content_hash, model, dimensions, vector, provider FROM memory_embeddings "
            "WHERE source_type = 'turn' AND memory_id = 'changing'"
        ).fetchone()
        conn.execute(
            "UPDATE turns SET text = ? WHERE turn_id = ?",
            ("改为完全无关的界面主题记录。", "changing"),
        )
        conn.commit()
    finally:
        conn.close()

    assert before[1:3] == ("test-embedding-v2", 3)
    assert json.loads(before[3]) == [1.0, 0.0, 0.0]
    assert before[4] == "test-provider"
    assert index.index_pending() == 1

    conn = open_memory_sqlite(service._db_path)
    try:
        after = conn.execute(
            "SELECT content_hash, vector FROM memory_embeddings "
            "WHERE source_type = 'turn' AND memory_id = 'changing'"
        ).fetchone()
    finally:
        conn.close()
    assert after[0] != before[0]
    assert json.loads(after[1]) == [0.0, 0.0, 1.0]


def test_semantic_index_backfill_advances_beyond_initial_candidate_window(tmp_path):
    service = _service(tmp_path)
    for index in range(11):
        _insert_turn(
            service,
            turn_id=f"backfill-{index:02d}",
            text=f"durable record {index}",
        )
    index = _index(service)

    batch_counts = []
    while count := index.index_pending(limit=2):
        batch_counts.append(count)

    conn = open_memory_sqlite(service._db_path)
    try:
        indexed = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE source_type = 'turn'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert batch_counts
    assert all(count <= 2 for count in batch_counts)
    assert indexed == 11


def test_semantic_index_rebuilds_scope_without_content_change(tmp_path):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="scope-change", text="数据库迁移采用蓝绿方案。")
    index = _index(service)
    assert index.index_pending() >= 1

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE turns SET owner_id = 'owner-b', workspace_id = 'workspace-b' "
            "WHERE turn_id = 'scope-change'"
        )
        conn.commit()
    finally:
        conn.close()

    assert index.index_pending() == 1
    assert ("turn", "scope-change") not in index.search(
        "database migration",
        owner_id="owner-a",
        workspace_id="workspace-a",
    )
    assert ("turn", "scope-change") in index.search(
        "database migration",
        owner_id="owner-b",
        workspace_id="workspace-b",
    )


def test_semantic_index_skips_embedding_for_stale_snapshot(tmp_path):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="stale", text="old database migration")

    def transport(texts):
        conn = open_memory_sqlite(service._db_path)
        try:
            conn.execute(
                "UPDATE turns SET text = ? WHERE turn_id = ?",
                ("new unrelated content", "stale"),
            )
            conn.commit()
        finally:
            conn.close()
        return _vectors(texts)

    index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(
            enabled=True,
            provider="test-provider",
            model="test-embedding-v2",
            dimensions=3,
        ),
        transport=transport,
    )

    assert index.index_pending() == 0
    conn = open_memory_sqlite(service._db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0
    finally:
        conn.close()
    assert index.index_pending() == 1


def test_semantic_index_serializes_concurrent_backfills(tmp_path):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="concurrent", text="database migration")
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def transport(texts):
        calls.append(list(texts))
        entered.set()
        release.wait(timeout=2)
        return _vectors(texts)

    index = SemanticMemoryIndex(
        service._db_path,
        SemanticIndexConfig(
            enabled=True,
            provider="test-provider",
            model="test-embedding-v2",
            dimensions=3,
        ),
        transport=transport,
    )
    results = []
    first = threading.Thread(target=lambda: results.append(index.index_pending()))
    second = threading.Thread(target=lambda: results.append(index.index_pending()))
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert sorted(results) == [0, 1]
    assert len(calls) == 1


def test_native_vec0_refresh_uses_existing_embedding_rowid(tmp_path):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="vec0-refresh", text="database migration")
    index = _index(service)
    if not index._vec0_ready:
        pytest.skip("sqlite-vec extension is not available")

    assert index.index_pending() == 1
    conn = open_memory_sqlite(service._db_path)
    try:
        metadata_rowid = conn.execute(
            "SELECT rowid FROM memory_embeddings WHERE memory_id = ?",
            ("vec0-refresh",),
        ).fetchone()[0]
        vec_rowid = conn.execute(
            "SELECT rowid FROM memory_embeddings_vec WHERE rowid = ?",
            (metadata_rowid,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert vec_rowid == metadata_rowid

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "UPDATE turns SET text = ? WHERE turn_id = ?",
            ("private-b", "vec0-refresh"),
        )
        conn.commit()
    finally:
        conn.close()
    assert index.index_pending() == 1

    conn = open_memory_sqlite(service._db_path)
    try:
        refreshed_rowid = conn.execute(
            "SELECT rowid FROM memory_embeddings_vec WHERE rowid = ?",
            (metadata_rowid,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert refreshed_rowid == metadata_rowid


def test_native_vec0_scope_update_removes_all_source_versions(tmp_path):
    service = _service(tmp_path)
    _insert_turn(service, turn_id="vec0-scope", text="database migration")
    index = _index(service)
    if not index._vec0_ready:
        pytest.skip("sqlite-vec extension is not available")

    conn = open_memory_sqlite(service._db_path)
    try:
        conn.execute(
            "INSERT INTO memory_embeddings "
            "(source_type, memory_id, owner_id, workspace_id, memory_domain, "
            "content_hash, provider, model, dimensions, vector, updated_at) "
            "VALUES ('turn', 'vec0-scope', 'owner-a', 'workspace-a', "
            "'agent_interaction', 'alternate', 'alternate-provider', "
            "'alternate-model', 3, '[0,0,1]', 'now')"
        )
        conn.commit()
    finally:
        conn.close()

    assert index.index_pending() == 1
    conn = open_memory_sqlite(service._db_path)
    try:
        indexed_rowid = conn.execute(
            "SELECT rowid FROM memory_embeddings WHERE source_type = 'turn' "
            "AND memory_id = 'vec0-scope' AND provider = 'test-provider'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE turns SET owner_id = 'owner-b', workspace_id = 'workspace-b' "
            "WHERE turn_id = 'vec0-scope'"
        )
        conn.commit()
        metadata_count = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE source_type = 'turn' "
            "AND memory_id = 'vec0-scope'"
        ).fetchone()[0]
        vec_count = conn.execute(
            "SELECT COUNT(*) FROM memory_embeddings_vec WHERE rowid = ?",
            (indexed_rowid,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert metadata_count == 0
    assert vec_count == 0


@pytest.mark.asyncio
async def test_semantic_recall_hydrates_nonlexical_match_and_respects_scope(tmp_path):
    service = _service(tmp_path)
    _insert_turn(
        service,
        turn_id="semantic-a",
        text="数据库迁移必须保留可验证的回滚快照。",
    )
    _insert_turn(
        service,
        turn_id="semantic-b",
        text="private-b",
        owner_id="owner-b",
        workspace_id="workspace-b",
    )
    service._semantic_index = _index(service)
    assert service._semantic_index.index_pending() >= 2

    result = await service.recall(
        RecallRequest(
            query="database migration",
            include_tier2=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert [item["id"] for item in result["results"]] == ["semantic-a"]
    assert result["results"][0]["matched_terms"] == []
    assert result["results"][0]["signals"]["semantic"] == 1.0
    assert ("turn", "semantic-b") not in service._semantic_index.search(
        "private-b",
        owner_id="owner-a",
        workspace_id="workspace-a",
    )


class _EmbeddingsHandler(http.server.BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /embeddings endpoint for the HTTP path test."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        inputs = body.get("input") or []
        data = []
        for index, text in enumerate(inputs):
            digest = hashlib.blake2b(str(text).encode("utf-8"), digest_size=4).digest()
            vector = [float(b / 255.0) for b in digest[:4]]
            data.append({"index": index, "embedding": vector})
        payload = json.dumps({"data": data}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence
        pass


def test_semantic_index_real_http_transport_end_to_end(tmp_path):
    """Prove the external-provider plumbing works over the real HTTP path.

    This is what ``--semantic-base-url/--semantic-model`` on the LongMemEval
    harness exercises: a real HTTP /embeddings call driving index + search, so
    pointing config.yaml at a real model (Ollama / any OpenAI-compatible API)
    works end-to-end.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _EmbeddingsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        service = _service(tmp_path)
        config = SemanticIndexConfig(
            enabled=True,
            provider="mock-http",
            model="mock-embed",
            base_url=f"http://127.0.0.1:{port}",
            api_key="mock-key",
            dimensions=4,
            timeout_seconds=5.0,
        )
        index = SemanticMemoryIndex(service._db_path, config)
        _insert_turn(service, turn_id="http-turn", text="数据库迁移必须保留备份。")
        assert index.index_pending(limit=1000) >= 1
        matches = index.search(
            "数据库迁移",
            owner_id="owner-a",
            workspace_id="workspace-a",
            source_domains=("agent_interaction",),
        )
        assert ("turn", "http-turn") in matches
        assert matches[("turn", "http-turn")] > 0.0
    finally:
        server.shutdown()
        thread.join(timeout=5)
