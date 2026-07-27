from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest
from fastapi import HTTPException

from systems.memory.config import MemoryServiceConfig
from systems.memory.memory_service import MemoryService, SessionCreate, TurnCreate


def _make_service(tmp_path: Path, *, retention: int = 5) -> MemoryService:
    return MemoryService(
        MemoryServiceConfig(
            db_path=str(tmp_path / "memory.db"),
            backup_retention_count=retention,
        )
    )


async def _write_turn(service: MemoryService, marker: str, content: str) -> str:
    session_id = f"backup-{marker}"
    await service.create_session(SessionCreate(session_id=session_id))
    result = await service.add_turn(
        session_id,
        TurnCreate(speaker="user", text=content),
    )
    return result["turn_id"]


@pytest.mark.asyncio
async def test_online_backup_is_valid_and_rotation_is_bounded(tmp_path):
    service = _make_service(tmp_path, retention=2)
    await _write_turn(service, "m1", "first")

    created = [await service.create_backup() for _ in range(3)]
    listed = await service.list_backups()

    assert all(item["status"] == "created" for item in created)
    assert all(item["integrity_check"] == "ok" for item in created)
    assert listed["count"] == 2
    assert created[0]["backup_id"] not in {
        item["backup_id"] for item in listed["backups"]
    }
    for item in listed["backups"]:
        backup_path = Path(item["path"])
        assert backup_path.parent == tmp_path / "backups"
        conn = sqlite3.connect(backup_path)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()


@pytest.mark.asyncio
async def test_restore_replaces_database_with_valid_backup(tmp_path):
    service = _make_service(tmp_path)
    await _write_turn(service, "before", "preserved by backup")
    backup = await service.create_backup()
    await _write_turn(service, "after", "removed by restore")

    result = await service.restore_backup(backup["backup_id"])

    assert result["status"] == "restored"
    assert result["integrity_check"] == "ok"
    conn = sqlite3.connect(service._db_path)
    try:
        texts = {
            row[0] for row in conn.execute("SELECT text FROM turns").fetchall()
        }
    finally:
        conn.close()
    assert texts == {"preserved by backup"}


@pytest.mark.asyncio
async def test_restore_rolls_back_when_restore_operation_fails(tmp_path, monkeypatch):
    service = _make_service(tmp_path)
    await _write_turn(service, "backup-state", "older")
    backup = await service.create_backup()
    await _write_turn(service, "live-state", "must survive failed restore")
    manager = service._backup_manager
    real_restore = manager._restore_database
    calls = 0

    def fail_after_first_restore(source, target):
        nonlocal calls
        calls += 1
        real_restore(source, target)
        if calls == 1:
            raise RuntimeError("injected restore failure")

    monkeypatch.setattr(manager, "_restore_database", fail_after_first_restore)

    with pytest.raises(HTTPException, match="previous database restored"):
        await service.restore_backup(backup["backup_id"])

    conn = sqlite3.connect(service._db_path)
    try:
        texts = {
            row[0] for row in conn.execute("SELECT text FROM turns").fetchall()
        }
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    assert texts == {"older", "must survive failed restore"}
    assert integrity == "ok"


@pytest.mark.asyncio
async def test_restore_rejects_invalid_or_out_of_scope_backup_id(tmp_path):
    service = _make_service(tmp_path)

    with pytest.raises(HTTPException, match="Invalid backup_id"):
        await service.restore_backup("../outside.db")


@pytest.mark.asyncio
async def test_explicit_json_export_is_consistent_and_scoped(tmp_path):
    service = _make_service(tmp_path)
    await _write_turn(service, "exported", "explicit export content")

    result = await service.export_memory()
    export_path = Path(result["path"])
    payload = json.loads(export_path.read_text(encoding="utf-8"))

    assert result["status"] == "exported"
    assert export_path.parent == tmp_path / "exports"
    assert payload["format"] == "voidcube.memory.export"
    assert payload["format_version"] == 1
    assert {row["text"] for row in payload["tables"]["turns"]} == {
        "explicit export content"
    }
    assert result["table_counts"]["turns"] == 1
    assert "memories" not in payload["tables"]
    assert "profile_memories" in payload["tables"]
    assert "profile_memory_tombstones" in payload["tables"]
    assert "memory_promotion_candidates" in payload["tables"]
    assert "memory_promotion_refs" in payload["tables"]
    assert "identity_revision_proposals" in payload["tables"]
