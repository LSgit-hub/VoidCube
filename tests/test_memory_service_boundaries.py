from __future__ import annotations

import inspect
from pathlib import Path
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from memai.application.config import MemoryServiceConfig
from memai.migrations.schema import MemoryDatabaseBootstrap
from memai.repository.sqlite import open_memory_sqlite
from memai.repository.sqlite_repository import (
    MemoryWriteReceiptConflict,
    SQLiteMemoryRepository,
    _STOP,
)
from memai.repository.sqlite_repository import MemoryWriteBackpressure
from memai.transport.http_adapter import MEMORY_HTTP_ROUTES, build_memory_http_app
from memai.application.memory_service import (
    MemoryApplicationService,
    MemoryService,
    SessionCreate,
    TurnPairCreate,
    TurnCreate,
)
from memai.repository.backup import MemoryBackupManager
from plugins.memory.mem.outbox import MemoryWriteOutbox


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
async def test_identity_cycle_respects_repository_transaction_boundary(tmp_path: Path):
    service = MemoryApplicationService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )

    result = await service._identity_experience_cycle()

    assert result["updated_count"] == 0
    assert service._repository.execution_stats()["write_batch_failures"] == 0
    service._repository.close()


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
        "memory_write_receipts",
    } <= tables


def test_idempotent_write_receipt_replays_after_restart_and_rejects_conflict(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    repository = SQLiteMemoryRepository(db_path)
    repository.initialize()
    first = repository.execute_idempotent_write(
        receipt_key="receipt-1",
        operation="test_write",
        fingerprint="fingerprint-a",
        owner_id="owner-a",
        workspace_id="workspace-a",
        memory_domain="agent_interaction",
        callback=lambda conn: {"created_id": "row-1"},
    )
    revision = first.commit_revision
    repository.close()

    reopened = SQLiteMemoryRepository(db_path)
    reopened.initialize()
    replay = reopened.execute_idempotent_write(
        receipt_key="receipt-1",
        operation="test_write",
        fingerprint="fingerprint-a",
        owner_id="owner-a",
        workspace_id="workspace-a",
        memory_domain="agent_interaction",
        callback=lambda conn: {"created_id": "should-not-run"},
    )
    assert replay.value == {"created_id": "row-1"}
    assert replay.replay is True
    assert replay.commit_revision == revision
    with pytest.raises(MemoryWriteReceiptConflict):
        reopened.execute_idempotent_write(
            receipt_key="receipt-1",
            operation="test_write",
            fingerprint="fingerprint-b",
            owner_id="owner-a",
            workspace_id="workspace-a",
            memory_domain="agent_interaction",
            callback=lambda conn: {"created_id": "row-2"},
        )
    reopened.close()


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


def test_memory_http_adapter_binds_token_to_actor_and_preserves_health_probe():
    handlers = {
        route.handler: (lambda: {"status": "ok"})
        for route in MEMORY_HTTP_ROUTES
    }
    app = build_memory_http_app(
        handlers,
        lifespan=lambda _app: _null_lifespan(),
        service_tokens={"api_a": "api-token", "stellar_companion": "companion-token"},
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/remember").status_code == 401
        assert client.post(
            "/remember",
            headers={
                "Authorization": "Bearer api-token",
                "X-VoidCube-Memory-Actor": "stellar_companion",
            },
        ).status_code == 401
        assert client.post(
            "/remember",
            headers={
                "Authorization": "Bearer api-token",
                "X-VoidCube-Memory-Actor": "api_a",
            },
            json={"memory_actor": "stellar_companion"},
        ).status_code == 422
        assert client.post(
            "/remember",
            headers={
                "Authorization": "Bearer api-token",
                "X-VoidCube-Memory-Actor": "api_a",
                "X-VoidCube-Owner-Id": "local-user",
                "X-VoidCube-Workspace-Id": "default",
            },
            json={"memory_actor": "api_a", "owner_id": "local-user"},
        ).status_code == 200


def test_memory_http_adapter_single_token_is_bound_to_configured_actor():
    handlers = {
        route.handler: (lambda: {"status": "ok"})
        for route in MEMORY_HTTP_ROUTES
    }
    app = build_memory_http_app(
        handlers,
        lifespan=lambda _app: _null_lifespan(),
        service_token="shared-token",
        service_actor="api_a",
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        common = {"Authorization": "Bearer shared-token"}
        assert client.get(
            "/mem/usage",
            headers={**common, "X-VoidCube-Memory-Actor": "api_a"},
        ).status_code == 200
        assert client.get(
            "/mem/usage",
            headers={**common, "X-VoidCube-Memory-Actor": "governor"},
        ).status_code == 401
        assert client.get(
            "/mem/usage",
            headers={**common, "X-VoidCube-Memory-Actor": "memory_maintenance"},
        ).status_code == 401


def test_memory_service_rejects_non_loopback_bindings_and_assignment():
    with pytest.raises(ValueError, match="loopback"):
        MemoryServiceConfig(host="0.0.0.0")

    config = MemoryServiceConfig()
    with pytest.raises(ValueError, match="loopback"):
        config.host = "192.168.1.10"


def test_sqlite_repository_write_boundary_serializes_and_retries_busy(tmp_path: Path):
    repository = SQLiteMemoryRepository(tmp_path / "memory.db")
    repository.initialize()
    original_connect = repository.connect
    attempts = {"count": 0}

    class BusyOnceConnection:
        def __init__(self):
            self.closed = False

        def execute(self, statement, *args, **kwargs):
            if statement == "BEGIN IMMEDIATE":
                raise sqlite3.OperationalError("database is locked")
            return None

        def commit(self):
            raise AssertionError("busy connection must not commit")

        def rollback(self):
            pass

        def close(self):
            self.closed = True

    def flaky_connect():
        attempts["count"] += 1
        if attempts["count"] == 1:
            return BusyOnceConnection()
        return original_connect()

    repository.connect = flaky_connect  # type: ignore[method-assign]

    def write(conn):
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
            "VALUES ('s1', 'local-user', 'default', 'agent_interaction', 'now', 'now', '{}')"
        )
        return "committed"

    assert repository.execute_write(write) == "committed"

    stats = repository.execution_stats()
    assert attempts["count"] == 2
    assert stats["write_operations"] == 1
    assert stats["write_busy_retries"] == 1
    assert stats["write_busy_failures"] == 0


def test_sqlite_repository_commit_revision_persists_across_restarts(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    repository = SQLiteMemoryRepository(db_path)
    repository.initialize()

    initial_revision = repository.commit_revision

    def write(conn):
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
            "VALUES ('s2', 'local-user', 'default', 'agent_interaction', 'now', 'now', '{}')"
        )
        return "written"

    assert repository.execute_write(write) == "written"
    assert repository.commit_revision == initial_revision + 1

    restarted = SQLiteMemoryRepository(db_path)
    restarted.initialize()
    assert restarted.commit_revision == repository.commit_revision
    repository.close()
    restarted.close()


def test_sqlite_repository_batches_concurrent_writes_in_one_commit(tmp_path: Path):
    repository = SQLiteMemoryRepository(
        tmp_path / "memory.db",
        write_batch_size=8,
        write_batch_wait_ms=20,
    )
    repository.initialize()
    results: list[str] = []

    def write(session_id: str):
        def operation(conn):
            conn.execute(
                "INSERT INTO sessions "
                "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
                "VALUES (?, 'local-user', 'default', 'agent_interaction', 'now', 'now', '{}')",
                (session_id,),
            )
            return session_id

        results.append(repository.execute_write(operation))

    threads = [
        threading.Thread(target=write, args=(f"batch-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(results) == ["batch-0", "batch-1"]
    stats = repository.execution_stats()
    assert stats["write_batch_operations"] == 2
    assert stats["write_batch_commits"] == 1
    assert stats["write_queue_completed"] == 2
    assert repository.commit_revision == 1
    repository.close()


def test_sqlite_repository_reports_backpressure_for_full_queue(tmp_path: Path):
    repository = SQLiteMemoryRepository(
        tmp_path / "memory.db",
        write_queue_max_size=1,
        write_batch_size=1,
    )
    repository.initialize()
    started = threading.Event()
    release = threading.Event()

    def blocked(conn):
        started.set()
        release.wait(timeout=3)
        return "blocked"

    first = threading.Thread(target=lambda: repository.execute_write(blocked))
    first.start()
    assert started.wait(timeout=2)

    second = threading.Thread(
        target=lambda: repository.execute_write(lambda conn: "queued")
    )
    second.start()
    time.sleep(0.05)
    with pytest.raises(MemoryWriteBackpressure):
        repository.execute_write(lambda conn: "rejected")

    release.set()
    first.join(timeout=3)
    second.join(timeout=3)
    assert repository.execution_stats()["write_queue_backpressure"] == 1
    repository.close()


def test_sqlite_repository_close_waits_for_inflight_enqueue_before_stop(tmp_path: Path, monkeypatch):
    repository = SQLiteMemoryRepository(
        tmp_path / "memory.db",
        write_queue_max_size=1,
        write_batch_size=1,
        write_enqueue_timeout_ms=1000,
    )
    repository.initialize()

    request_started = threading.Event()
    release_request = threading.Event()
    stop_requested = threading.Event()
    request_finished = threading.Event()
    result: dict[str, object] = {}

    original_put = repository._write_queue.put

    def wrapped_put(item, block=True, timeout=None):
        if item is _STOP:
            stop_requested.set()
            return original_put(item, block=block, timeout=timeout)
        request_started.set()
        assert release_request.wait(timeout=3)
        return original_put(item, block=block, timeout=timeout)

    monkeypatch.setattr(repository._write_queue, "put", wrapped_put)

    def run_write():
        try:
            result["value"] = repository.execute_write(lambda conn: "queued")
        except Exception as exc:  # pragma: no cover - diagnostic fallback
            result["error"] = exc
        finally:
            request_finished.set()

    writer = threading.Thread(target=run_write)
    writer.start()
    assert request_started.wait(timeout=2)

    close_done = threading.Event()

    def run_close():
        repository.close(timeout=0.5)
        close_done.set()

    closer = threading.Thread(target=run_close)
    closer.start()
    time.sleep(0.05)
    assert stop_requested.is_set() is False
    assert close_done.is_set() is False

    release_request.set()
    assert request_finished.wait(timeout=3)
    assert close_done.wait(timeout=3)
    writer.join(timeout=3)
    closer.join(timeout=3)

    assert stop_requested.is_set()
    assert result["value"] == "queued"


def test_sqlite_repository_concurrent_idempotent_retries_commit_once(tmp_path: Path):
    repository = SQLiteMemoryRepository(
        tmp_path / "memory.db",
        write_batch_size=8,
        write_batch_wait_ms=10,
    )
    repository.initialize()
    calls = {"count": 0}
    lock = threading.Lock()

    def callback(conn):
        with lock:
            calls["count"] += 1
        conn.execute(
            "INSERT INTO sessions "
            "(session_id, owner_id, workspace_id, memory_domain, created_at, updated_at, metadata) "
            "VALUES ('concurrent-receipt', 'local-user', 'default', 'agent_interaction', 'now', 'now', '{}')"
        )
        return {"session_id": "concurrent-receipt"}

    def invoke():
        return repository.execute_idempotent_write(
            receipt_key="concurrent-receipt-key",
            operation="create_session",
            fingerprint="same-payload",
            owner_id="local-user",
            workspace_id="default",
            memory_domain="agent_interaction",
            callback=callback,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: invoke(), range(8)))

    assert calls["count"] == 1
    assert all(result.value == {"session_id": "concurrent-receipt"} for result in results)
    assert sum(result.replay for result in results) == 7
    assert len({result.commit_revision for result in results}) == 1
    count = repository.execute_read(
        lambda conn: conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE session_id = 'concurrent-receipt'"
        ).fetchone()[0]
    )
    assert count == 1
    repository.close()


@pytest.mark.asyncio
async def test_memory_outbox_retry_after_commit_is_deduplicated(tmp_path: Path):
    service = MemoryApplicationService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    outbox = MemoryWriteOutbox(
        tmp_path / "write-outbox.sqlite3",
        lease_seconds=1.0,
    )
    outbox.enqueue(
        {
            "write_id": "crash-window-write",
            "session_id": "crash-window-session",
            "user_content": "durable question",
            "assistant_content": "durable answer",
            "owner_id": "local-user",
            "workspace_id": "default",
            "memory_domain": "agent_interaction",
        }
    )

    first = outbox.next_due()
    assert first is not None
    await service.add_turn_pair(
        TurnPairCreate(
            session_id=first["session_id"],
            user_content=first["user_content"],
            assistant_content=first["assistant_content"],
            write_id=first["write_id"],
        )
    )
    # Simulate a client crash after the service committed but before the
    # outbox row was acknowledged as delivered.
    service_revision = service._repository.commit_revision
    service._repository.close()

    reopened_outbox = MemoryWriteOutbox(tmp_path / "write-outbox.sqlite3")
    time.sleep(1.05)
    retry = reopened_outbox.next_due()
    assert retry is not None
    restarted = MemoryApplicationService(
        MemoryServiceConfig(db_path=str(tmp_path / "memory.db"))
    )
    repeated = await restarted.add_turn_pair(
        TurnPairCreate(
            session_id=retry["session_id"],
            user_content=retry["user_content"],
            assistant_content=retry["assistant_content"],
            write_id=retry["write_id"],
        )
    )
    assert repeated["replayed"] is True
    assert repeated["commit_revision"] == service_revision
    # Startup reconciliation may perform its own committed maintenance write;
    # the replay response must still point at the original durable commit.
    assert restarted._repository.commit_revision >= service_revision
    count = restarted._repository.execute_read(
        lambda conn: conn.execute(
            "SELECT COUNT(*) FROM turns WHERE session_id = 'crash-window-session'"
        ).fetchone()[0]
    )
    assert count == 2
    reopened_outbox.mark_delivered(
        retry["write_id"],
        lease_token=retry["_outbox_lease_token"],
    )
    assert reopened_outbox.pending_count() == 0
    restarted._repository.close()
