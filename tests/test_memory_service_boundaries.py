from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from memai.application.config import MemoryServiceConfig
from memai.migrations.schema import MemoryDatabaseBootstrap
from memai.repository.sqlite import open_memory_sqlite
from memai.repository.sqlite_repository import SQLiteMemoryRepository
from memai.transport.http_adapter import MEMORY_HTTP_ROUTES, build_memory_http_app
from memai.application.memory_service import (
    MemoryApplicationService,
    MemoryService,
    SessionCreate,
    TurnCreate,
)
from memai.repository.backup import MemoryBackupManager


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_memory_use_cases_run_without_constructing_http_adapter(tmp_path: Path):
    service = MemoryApplicationService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )

    assert not hasattr(service, "app")
    created = await service.create_session(SessionCreate(session_id="direct-use-case"))
    turn = await service.add_turn(
        created["session_id"],
        TurnCreate(speaker="user", text="direct call"),
    )

    assert turn["session_id"] == "direct-use-case"
    assert turn["status"] == "created"


@pytest.mark.asyncio
async def test_memory_application_uses_injected_repository(tmp_path: Path):
    class RecordingRepository(SQLiteMemoryRepository):
        def __init__(self, db_path: Path):
            super().__init__(db_path)
            self.initialize_calls = 0
            self.connect_calls = 0

        def initialize(self) -> None:
            self.initialize_calls += 1
            super().initialize()

        def connect(self):
            self.connect_calls += 1
            return super().connect()

    repository = RecordingRepository(tmp_path / "memory.db")
    service = MemoryApplicationService(
        MemoryServiceConfig(db_path=str(tmp_path / "ignored.db")),
        repository=repository,
    )

    await service.create_session(SessionCreate(session_id="repository-port"))

    assert service._db_path == repository.db_path
    assert repository.initialize_calls == 1
    assert repository.connect_calls >= 1


def test_schema_and_migration_sql_are_not_owned_by_memory_service():
    source = inspect.getsource(MemoryApplicationService)

    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "PRAGMA table_info" not in source
    assert "_setup_database" not in source
    assert "_migrate_legacy_default_database" not in source


def test_database_bootstrap_is_idempotent_and_owns_required_schema(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    bootstrap = MemoryDatabaseBootstrap(
        db_path=db_path,
        backup_manager=MemoryBackupManager(db_path),
    )

    bootstrap.initialize()
    bootstrap.reconcile_schema()

    connection = open_memory_sqlite(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert {
        "sessions",
        "turns",
        "compressed_memories",
        "profile_memories",
        "identity_revision_proposals",
    } <= tables


def test_http_adapter_requires_one_explicit_handler_for_every_route():
    handlers = {
        route.handler: (lambda: None)
        for route in MEMORY_HTTP_ROUTES
    }

    app = build_memory_http_app(
        handlers,
        lifespan=lambda _app: _null_lifespan(),
    )

    route_contract = {
        (route.path, method)
        for route in app.routes
        for method in (route.methods or set())
    }
    assert ("/recall", "POST") in route_contract
    assert ("/sessions/{session_id}/close", "POST") in route_contract
    assert ("/admin/backups", "POST") in route_contract
    assert ("/outbox/health", "POST") in route_contract
    assert ("/health", "GET") in route_contract

    handlers.pop("recall")
    with pytest.raises(ValueError, match="missing=.*recall"):
        build_memory_http_app(
            handlers,
            lifespan=lambda _app: _null_lifespan(),
        )


class _null_lifespan:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_http_service_is_only_the_composition_root(tmp_path: Path):
    service = MemoryService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )

    assert service.app is not None
    assert isinstance(service, MemoryApplicationService)


def test_memory_service_rejects_non_loopback_bindings_and_assignment():
    with pytest.raises(ValueError, match="loopback"):
        MemoryServiceConfig(host="0.0.0.0")

    config = MemoryServiceConfig()
    with pytest.raises(ValueError, match="loopback"):
        config.host = "192.168.1.10"
