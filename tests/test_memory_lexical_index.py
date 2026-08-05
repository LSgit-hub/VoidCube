from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from systems.memory.config import MemoryServiceConfig
from systems.memory.lexical_index import search_memory_fts
from systems.memory.memory_service import MemoryService, RecallRequest
from systems.memory.database import open_memory_sqlite


pytestmark = [pytest.mark.unit]


def _service(tmp_path) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            recall_candidate_limit=10,
        )
    )


def _insert_turns(service: MemoryService) -> None:
    now = datetime.now(timezone.utc)
    conn = open_memory_sqlite(service._db_path)
    try:
        for owner_id, workspace_id, suffix in (
            ("owner-a", "workspace-a", "a"),
            ("owner-b", "workspace-b", "b"),
        ):
            conn.execute(
                "INSERT INTO sessions "
                "(session_id, owner_id, workspace_id, created_at, updated_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, '{}')",
                (
                    f"fts-session-{suffix}",
                    owner_id,
                    workspace_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
            "VALUES ('fts-old-target', 'fts-session-a', 'user', ?, ?, 1.0, "
            "0.01, '[]', '{}', 0, 'owner-a', 'workspace-a')",
            (
                "Zephyr protocol uses checksum qx-4821 before deployment.",
                (now - timedelta(days=500)).isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
            "VALUES ('fts-private-b', 'fts-session-b', 'user', ?, ?, 1.0, "
            "0.01, '[]', '{}', 0, 'owner-b', 'workspace-b')",
            ("Zephyr protocol private owner B copy.", now.isoformat()),
        )
        conn.executemany(
            "INSERT INTO turns "
            "(turn_id, session_id, speaker, text, timestamp, relevance_score, "
            "decay_factor, tags, metadata, compressed_to_tier2, owner_id, workspace_id) "
            "VALUES (?, 'fts-session-a', 'user', ?, ?, 1.0, 0.01, '[]', '{}', 0, "
            "'owner-a', 'workspace-a')",
            [
                (
                    f"fts-noise-{index}",
                    f"Unrelated interface observation number {index}.",
                    (now - timedelta(minutes=index)).isoformat(),
                )
                for index in range(250)
            ],
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fts_recalls_old_match_beyond_candidate_window_and_filters_scope(tmp_path):
    service = _service(tmp_path)
    _insert_turns(service)

    result = await service.recall(
        RecallRequest(
            query="Zephyr protocol checksum qx-4821",
            include_tier2=False,
            owner_id="owner-a",
            workspace_id="workspace-a",
        )
    )

    assert [item["id"] for item in result["results"]] == ["fts-old-target"]
    assert result["candidate_count"] == 1


def test_fts_triggers_track_updates_deletes_and_keep_private_scopes_separate(tmp_path):
    service = _service(tmp_path)
    _insert_turns(service)
    conn = open_memory_sqlite(service._db_path)
    try:
        owner_a = search_memory_fts(
            conn,
            ["zephyr protocol"],
            owner_id="owner-a",
            workspace_id="workspace-a",
            limit=20,
        )
        assert "fts-old-target" in owner_a["turn"]
        assert "fts-private-b" not in owner_a["turn"]

        conn.execute(
            "UPDATE turns SET text = ? WHERE turn_id = 'fts-old-target'",
            ("Aurora ledger replacement record.",),
        )
        conn.commit()
        assert search_memory_fts(
            conn,
            ["zephyr protocol"],
            owner_id="owner-a",
            workspace_id="workspace-a",
            limit=20,
        ) == {}
        assert "fts-old-target" in search_memory_fts(
            conn,
            ["aurora ledger"],
            owner_id="owner-a",
            workspace_id="workspace-a",
            limit=20,
        )["turn"]

        conn.execute("DELETE FROM turns WHERE turn_id = 'fts-old-target'")
        conn.commit()
        assert search_memory_fts(
            conn,
            ["aurora ledger"],
            owner_id="owner-a",
            workspace_id="workspace-a",
            limit=20,
        ) == {}
    finally:
        conn.close()
