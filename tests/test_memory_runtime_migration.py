from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from voidcube.infrastructure.config.system import SystemConfig, load_config_from_env
from memai.application.config import MemoryServiceConfig
from memai.migrations.schema import MemoryDatabaseBootstrap
from memai.application.memory_service import MemoryService
from memai.repository.backup import MemoryBackupManager
from memai.migrations.runtime_migration import (
    MemoryDatabaseMigrationConflict,
    migrate_memory_database,
)
from voidcube.infrastructure.gateway.service_launcher import _build_service_config
from memai.host_integration import get_mem_host_integration, configure_mem_host_integration


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.fixture(autouse=True)
def _restore_mem_host_integration():
    """Keep service-config construction from leaking host state to later tests."""
    previous = get_mem_host_integration()
    try:
        yield
    finally:
        configure_mem_host_integration(previous)


def _create_database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker(value) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _read_marker(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT value FROM marker").fetchone()
    finally:
        connection.close()
    return str(row[0])


def test_migration_backups_verifies_and_removes_legacy_database(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project" / "memory.db"
    target = tmp_path / "home" / "runtime" / "memory" / "memory.db"
    _create_database(source, "preserved")
    source_wal = Path(f"{source}-wal")
    source_shm = Path(f"{source}-shm")
    source_wal.touch()
    source_shm.touch()

    result = migrate_memory_database(source=source, target=target)

    assert result.status == "migrated"
    assert result.integrity_check == "ok"
    assert target.exists()
    assert source.exists() is False
    assert source_wal.exists() is False
    assert source_shm.exists() is False
    assert _read_marker(target) == "preserved"


def test_migration_refuses_to_choose_when_both_databases_exist(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project" / "memory.db"
    target = tmp_path / "home" / "runtime" / "memory" / "memory.db"
    _create_database(source, "legacy")
    _create_database(target, "canonical")

    with pytest.raises(MemoryDatabaseMigrationConflict, match="Both canonical"):
        migrate_memory_database(source=source, target=target)

    assert _read_marker(source) == "legacy"
    assert _read_marker(target) == "canonical"


def test_corrupt_source_is_not_published_or_deleted(tmp_path: Path) -> None:
    source = tmp_path / "project" / "memory.db"
    target = tmp_path / "home" / "runtime" / "memory" / "memory.db"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        migrate_memory_database(source=source, target=target)

    assert source.read_bytes() == b"not a sqlite database"
    assert target.exists() is False
    assert list(target.parent.glob("*.migrating-*")) == []


def test_default_memory_service_migrates_before_schema_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    source = project / "memory.db"
    _create_database(source, "service-data")
    monkeypatch.chdir(project)
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)

    service = MemoryService()
    target = home / "runtime" / "memory" / "memory.db"

    assert service._db_path == target
    assert source.exists() is False
    assert _read_marker(target) == "service-data"


def test_explicit_memory_path_never_scans_or_migrates_project_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    home = tmp_path / "home"
    source = project / "memory.db"
    custom = tmp_path / "custom" / "memory.sqlite"
    _create_database(source, "legacy")
    monkeypatch.chdir(project)
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))

    service = MemoryService(MemoryServiceConfig(db_path=str(custom)))

    assert service._db_path == custom
    assert custom.exists()
    assert source.exists()
    assert _read_marker(source) == "legacy"
    assert (home / "runtime" / "memory" / "memory.db").exists() is False


def test_system_and_service_use_one_memory_config_with_canonical_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VOIDCUBE_HOME", str(home))
    monkeypatch.delenv("MEMORY_DB_PATH", raising=False)

    system_config = load_config_from_env()
    service_config = _build_service_config("memory", 6124, system_config)

    assert type(system_config.memory) is MemoryServiceConfig
    assert type(service_config) is MemoryServiceConfig
    assert service_config.db_path == str(
        home / "runtime" / "memory" / "memory.db"
    )
    assert service_config.port == 6124
    assert system_config.memory.port == 6001


def test_memory_db_environment_override_wins_over_canonical_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom = tmp_path / "external" / "memory.db"
    monkeypatch.setenv("VOIDCUBE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MEMORY_DB_PATH", str(custom))

    config = load_config_from_env()

    assert config.memory.db_path == str(custom)


def test_schema_reconciliation_quarantines_evaluation_sourced_memories(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    backup_manager = MemoryBackupManager(db_path)
    bootstrap = MemoryDatabaseBootstrap(db_path, backup_manager)
    bootstrap.initialize()

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO sessions(session_id, created_at) VALUES ('session-a', '2026-08-16')"
        )
        connection.executemany(
            "INSERT INTO turns(turn_id, session_id, speaker, text, timestamp, tags) "
            "VALUES (?, 'session-a', 'agent', 'text', '2026-08-16', ?)",
            (("evaluation-turn", '["evaluation"]'), ("normal-turn", "[]")),
        )
        memory_values = (
            "event",
            "title",
            "summary",
            "2026-08-16",
            "2026-08-16",
            "2026-08-16",
        )
        connection.executemany(
            "INSERT INTO compressed_memories(memory_id, memory_type, title, summary, "
            "timespan_start, timespan_end, compressed_at, source_turns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ("evaluation-memory", *memory_values, '["evaluation-turn"]'),
                ("normal-memory", *memory_values, '["normal-turn"]'),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    bootstrap.reconcile_schema()
    bootstrap.reconcile_schema()

    connection = sqlite3.connect(db_path)
    try:
        visibility = dict(
            connection.execute(
                "SELECT memory_id, hidden FROM compressed_memories "
                "WHERE memory_id IN ('evaluation-memory', 'normal-memory')"
            ).fetchall()
        )
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM memory_deletion_audit "
            "WHERE target_kind = 'compressed_memory_quarantine'"
        ).fetchone()[0]
    finally:
        connection.close()

    assert visibility == {"evaluation-memory": 1, "normal-memory": 0}
    assert audit_count == 1


def test_memory_recall_environment_overrides_are_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_RECALL_LIMIT", "7")
    monkeypatch.setenv("MEMORY_RECALL_CANDIDATE_LIMIT", "321")
    monkeypatch.setenv("MEMORY_RECALL_MAX_CONTEXT_CHARS", "4096")
    monkeypatch.setenv("MEMORY_RECALL_MIN_SCORE", "0.35")

    config = load_config_from_env()

    assert config.memory.recall_default_limit == 7
    assert config.memory.recall_candidate_limit == 321
    assert config.memory.recall_max_context_chars == 4096
    assert config.memory.recall_min_score == pytest.approx(0.35)
