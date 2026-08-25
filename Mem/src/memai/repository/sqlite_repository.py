"""Canonical SQLite implementation of the Mem repository port.

The repository owns the only write executor used by Memory Service. Callers
submit short SQLite callbacks; a bounded worker serializes them and coalesces
nearby callbacks into one transaction. Durable/replayable requests remain the
responsibility of the client outbox because Python callbacks are not portable
queue payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import asyncio
import logging
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, TypeVar

from .backup import MemoryBackupManager
from .contracts import MemoryRepository
from memai.migrations.schema import MemoryDatabaseBootstrap

from .sqlite import open_memory_sqlite


logger = logging.getLogger(__name__)
T = TypeVar("T")

_RUNTIME_STATE_KEY = "memory_commit_revision"
_STOP = object()


class MemoryWriteBackpressure(RuntimeError):
    """The bounded Memory write queue cannot accept another request."""


class MemoryWriteReceiptConflict(RuntimeError):
    """An idempotency key was reused for a different write payload."""


@dataclass(frozen=True, slots=True)
class IdempotentWriteResult:
    value: object
    commit_revision: int
    replay: bool = False


@dataclass(frozen=True, slots=True)
class _ReceiptEnvelope:
    value: object
    receipt_key: str
    operation: str
    fingerprint: str
    owner_id: str
    workspace_id: str
    memory_domain: str
    replay: bool = False
    previous_revision: int = 0


@dataclass(slots=True)
class _ReceiptOperation:
    callback: Callable[[sqlite3.Connection], object]
    receipt_key: str
    operation: str
    fingerprint: str
    owner_id: str
    workspace_id: str
    memory_domain: str

    def __call__(self, connection: sqlite3.Connection) -> _ReceiptEnvelope:
        row = connection.execute(
            "SELECT operation, fingerprint, owner_id, workspace_id, memory_domain, "
            "status, commit_revision, result_json FROM memory_write_receipts "
            "WHERE receipt_key = ?",
            (self.receipt_key,),
        ).fetchone()
        expected = (
            self.operation,
            self.fingerprint,
            self.owner_id,
            self.workspace_id,
            self.memory_domain,
        )
        if row:
            if tuple(str(item) for item in row[:5]) != expected:
                raise MemoryWriteReceiptConflict(
                    f"Idempotency key already exists with a different payload: {self.receipt_key}"
                )
            try:
                value = json.loads(row[7])
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Corrupt Memory write receipt: {self.receipt_key}"
                ) from exc
            return _ReceiptEnvelope(
                value=value,
                receipt_key=self.receipt_key,
                operation=self.operation,
                fingerprint=self.fingerprint,
                owner_id=self.owner_id,
                workspace_id=self.workspace_id,
                memory_domain=self.memory_domain,
                replay=True,
                previous_revision=int(row[6] or 0),
            )
        return _ReceiptEnvelope(
            value=self.callback(connection),
            receipt_key=self.receipt_key,
            operation=self.operation,
            fingerprint=self.fingerprint,
            owner_id=self.owner_id,
            workspace_id=self.workspace_id,
            memory_domain=self.memory_domain,
        )


@dataclass(slots=True)
class _WriteRequest:
    operation: Callable[[sqlite3.Connection], object]
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None
    enqueued_at: float = field(default_factory=time.monotonic)


class SQLiteMemoryRepository(MemoryRepository):
    def __init__(
        self,
        db_path: str | Path,
        *,
        backup_retention_count: int = 5,
        write_queue_max_size: int = 256,
        write_batch_size: int = 16,
        write_batch_wait_ms: float = 2.0,
        write_enqueue_timeout_ms: float = 0.0,
    ) -> None:
        self._db_path = Path(db_path)
        self._backup_manager = MemoryBackupManager(
            self._db_path,
            retention_count=backup_retention_count,
        )
        self._bootstrap = MemoryDatabaseBootstrap(
            db_path=self._db_path,
            backup_manager=self._backup_manager,
        )
        self._write_queue_max_size = max(1, int(write_queue_max_size))
        self._write_batch_size = max(1, int(write_batch_size))
        self._write_batch_wait_seconds = max(0.0, float(write_batch_wait_ms) / 1000.0)
        self._write_enqueue_timeout_seconds = max(
            0.0, float(write_enqueue_timeout_ms) / 1000.0
        )
        self._write_queue: queue.Queue[_WriteRequest | object] = queue.Queue(
            maxsize=self._write_queue_max_size
        )
        self._writer_lock = threading.Lock()
        self._writer_started = False
        self._writer_thread: threading.Thread | None = None
        self._closed = False
        self._commit_revision = 0
        self._stats_lock = threading.Lock()
        self.write_operations = 0
        self.write_busy_retries = 0
        self.write_busy_failures = 0
        self.write_queue_enqueued = 0
        self.write_queue_completed = 0
        self.write_queue_backpressure = 0
        self.write_queue_max_depth = 0
        self.write_batches = 0
        self.write_batch_operations = 0
        self.write_batch_commits = 0
        self.write_batch_failures = 0
        self.write_queue_oldest_age_seconds = 0.0

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def commit_revision(self) -> int:
        return self._commit_revision

    @property
    def backup_manager(self) -> MemoryBackupManager:
        return self._backup_manager

    def initialize(self) -> None:
        self._bootstrap.initialize()
        self._commit_revision = self._read_commit_revision()

    def reconcile_schema(self) -> None:
        self._bootstrap.reconcile_schema()
        self._commit_revision = self._read_commit_revision()

    def connect(self) -> sqlite3.Connection:
        return open_memory_sqlite(self._db_path)

    def _read_commit_revision(self) -> int:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT state_value FROM memory_runtime_state WHERE state_key = ?",
                (_RUNTIME_STATE_KEY,),
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        except sqlite3.DatabaseError:
            return 0
        finally:
            connection.close()

    def _advance_commit_revision(self, connection: sqlite3.Connection) -> int:
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT OR IGNORE INTO memory_runtime_state "
            "(state_key, state_value, updated_at) VALUES (?, 0, ?)",
            (_RUNTIME_STATE_KEY, now),
        )
        connection.execute(
            "UPDATE memory_runtime_state SET state_value = state_value + 1, "
            "updated_at = ? WHERE state_key = ?",
            (now, _RUNTIME_STATE_KEY),
        )
        row = connection.execute(
            "SELECT state_value FROM memory_runtime_state WHERE state_key = ?",
            (_RUNTIME_STATE_KEY,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def execute_read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        connection = self.connect()
        try:
            return operation(connection)
        finally:
            connection.close()

    def _ensure_writer(self) -> None:
        with self._writer_lock:
            if self._closed:
                raise RuntimeError("Memory repository is closed")
            if self._writer_started:
                return
            self._writer_started = True
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name="voidcube-memory-writer",
                daemon=True,
            )
            self._writer_thread.start()

    def _enqueue_write(
        self, operation: Callable[[sqlite3.Connection], T]
    ) -> _WriteRequest:
        self._ensure_writer()
        request = _WriteRequest(operation=operation)
        try:
            if self._write_enqueue_timeout_seconds > 0:
                self._write_queue.put(
                    request, block=True, timeout=self._write_enqueue_timeout_seconds
                )
            else:
                self._write_queue.put_nowait(request)
        except queue.Full as exc:
            with self._stats_lock:
                self.write_queue_backpressure += 1
            raise MemoryWriteBackpressure(
                "Memory write queue is full; retry after backpressure"
            ) from exc
        with self._stats_lock:
            self.write_queue_enqueued += 1
            self.write_queue_max_depth = max(
                self.write_queue_max_depth, self._write_queue.qsize()
            )
        return request

    def execute_write(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Queue one callback and wait until its transaction is committed."""
        if threading.current_thread() is self._writer_thread:
            request = _WriteRequest(operation=operation)
            self._execute_batch([request])
        else:
            request = self._enqueue_write(operation)
            request.done.wait()
        if request.error is not None:
            raise request.error
        result = request.result
        if isinstance(result, IdempotentWriteResult):
            return result.value  # type: ignore[return-value]
        return result  # type: ignore[return-value]

    def execute_idempotent_write(
        self,
        *,
        receipt_key: str,
        operation: str,
        fingerprint: str,
        owner_id: str,
        workspace_id: str,
        memory_domain: str,
        callback: Callable[[sqlite3.Connection], T],
    ) -> IdempotentWriteResult:
        """Execute a write once and persist its response for safe retries."""
        key = str(receipt_key).strip()
        if not key:
            raise ValueError("receipt_key is required")
        wrapped = _ReceiptOperation(
            callback=callback,
            receipt_key=key,
            operation=str(operation),
            fingerprint=str(fingerprint),
            owner_id=str(owner_id),
            workspace_id=str(workspace_id),
            memory_domain=str(memory_domain),
        )
        result = self._execute_write_internal(wrapped, unwrap=False)
        if not isinstance(result, IdempotentWriteResult):
            raise RuntimeError("Memory idempotent write returned an invalid result")
        return result

    def _execute_write_internal(
        self, operation: Callable[[sqlite3.Connection], T], *, unwrap: bool
    ) -> T:
        if threading.current_thread() is self._writer_thread:
            request = _WriteRequest(operation=operation)
            self._execute_batch([request])
        else:
            request = self._enqueue_write(operation)
            request.done.wait()
        if request.error is not None:
            raise request.error
        result = request.result
        if unwrap and isinstance(result, IdempotentWriteResult):
            return result.value  # type: ignore[return-value]
        return result  # type: ignore[return-value]

    def _take_batch(self, first: _WriteRequest) -> list[_WriteRequest]:
        batch = [first]
        if self._write_batch_size <= 1:
            return batch
        deadline = time.monotonic() + self._write_batch_wait_seconds
        while len(batch) < self._write_batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = self._write_queue.get(timeout=remaining)
            except queue.Empty:
                break
            if item is _STOP:
                self._write_queue.put(_STOP)
                break
            batch.append(item)  # type: ignore[arg-type]
        return batch

    @staticmethod
    def _is_busy(exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and (
            "locked" in str(exc).lower() or "busy" in str(exc).lower()
        )

    def _execute_batch(self, batch: list[_WriteRequest]) -> None:
        retries = 4
        delay = 0.05
        for attempt in range(retries + 1):
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                successful: list[_WriteRequest] = []
                receipt_envelopes: list[tuple[_WriteRequest, _ReceiptEnvelope]] = []
                batch_receipts: dict[str, _ReceiptEnvelope] = {}
                for index, request in enumerate(batch):
                    savepoint = f"memory_write_{index}"
                    try:
                        connection.execute(f"SAVEPOINT {savepoint}")
                        operation = request.operation
                        if isinstance(operation, _ReceiptOperation) and operation.receipt_key in batch_receipts:
                            previous = batch_receipts[operation.receipt_key]
                            expected = (
                                operation.operation,
                                operation.fingerprint,
                                operation.owner_id,
                                operation.workspace_id,
                                operation.memory_domain,
                            )
                            actual = (
                                previous.operation,
                                previous.fingerprint,
                                previous.owner_id,
                                previous.workspace_id,
                                previous.memory_domain,
                            )
                            if expected != actual:
                                raise MemoryWriteReceiptConflict(
                                    f"Idempotency key already exists with a different payload: {operation.receipt_key}"
                                )
                            raw_result = _ReceiptEnvelope(
                                value=previous.value,
                                receipt_key=previous.receipt_key,
                                operation=previous.operation,
                                fingerprint=previous.fingerprint,
                                owner_id=previous.owner_id,
                                workspace_id=previous.workspace_id,
                                memory_domain=previous.memory_domain,
                                replay=True,
                                previous_revision=previous.previous_revision,
                            )
                        else:
                            raw_result = operation(connection)
                        envelope = raw_result if isinstance(raw_result, _ReceiptEnvelope) else None
                        if envelope is not None:
                            batch_receipts.setdefault(envelope.receipt_key, envelope)
                            request.result = envelope.value
                            receipt_envelopes.append((request, envelope))
                            if envelope.replay:
                                request.result = IdempotentWriteResult(
                                    value=envelope.value,
                                    commit_revision=envelope.previous_revision,
                                    replay=True,
                                )
                            else:
                                successful.append(request)
                        else:
                            request.result = raw_result
                            successful.append(request)
                        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    except BaseException as exc:
                        try:
                            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                        except sqlite3.DatabaseError:
                            logger.debug(
                                "Failed to rollback Memory write savepoint",
                                exc_info=True,
                            )
                        request.error = exc
                revision = (
                    self._advance_commit_revision(connection)
                    if successful
                    else self._commit_revision
                )
                for request, envelope in receipt_envelopes:
                    if envelope.replay:
                        request.result = IdempotentWriteResult(
                            value=envelope.value,
                            commit_revision=(envelope.previous_revision or revision),
                            replay=True,
                        )
                        continue
                    connection.execute(
                        "INSERT INTO memory_write_receipts "
                        "(receipt_key, operation, fingerprint, owner_id, workspace_id, "
                        "memory_domain, status, commit_revision, result_json, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'committed', ?, ?, ?, ?)",
                        (
                            envelope.receipt_key,
                            envelope.operation,
                            envelope.fingerprint,
                            envelope.owner_id,
                            envelope.workspace_id,
                            envelope.memory_domain,
                            revision,
                            json.dumps(envelope.value, ensure_ascii=False, sort_keys=True),
                            datetime.now(timezone.utc).isoformat(),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
                    request.result = IdempotentWriteResult(
                        value=envelope.value,
                        commit_revision=revision,
                        replay=False,
                    )
                connection.commit()
                if successful:
                    self._commit_revision = revision
                    with self._stats_lock:
                        self.write_batch_commits += 1
                with self._stats_lock:
                    self.write_queue_completed += len(batch)
                return
            except BaseException as exc:
                connection.rollback()
                if self._is_busy(exc) and attempt < retries:
                    with self._stats_lock:
                        self.write_busy_retries += 1
                    time.sleep(delay * (2**attempt))
                    continue
                with self._stats_lock:
                    if self._is_busy(exc):
                        self.write_busy_failures += 1
                    self.write_batch_failures += 1
                for request in batch:
                    if request.error is None:
                        request.error = exc
                return
            finally:
                connection.close()

    def _writer_loop(self) -> None:
        while True:
            item = self._write_queue.get()
            if item is _STOP:
                self._write_queue.task_done()
                return
            try:
                batch = self._take_batch(item)  # type: ignore[arg-type]
                with self._stats_lock:
                    self.write_batches += 1
                    self.write_batch_operations += len(batch)
                    self.write_operations += len(batch)
                    if batch:
                        self.write_queue_oldest_age_seconds = max(
                            self.write_queue_oldest_age_seconds,
                            max(0.0, time.monotonic() - batch[0].enqueued_at),
                        )
                self._execute_batch(batch)
                for request in batch:
                    request.done.set()
                    self._write_queue.task_done()
            except BaseException as exc:
                logger.exception("Memory writer loop failed")
                item.error = exc  # type: ignore[attr-defined]
                item.done.set()  # type: ignore[attr-defined]
                self._write_queue.task_done()

    def close(self, *, timeout: float = 5.0) -> None:
        with self._writer_lock:
            if self._closed:
                return
            self._closed = True
            thread = self._writer_thread
            if not self._writer_started:
                return
            try:
                self._write_queue.put(_STOP, timeout=max(0.1, float(timeout)))
            except queue.Full:
                logger.warning("Memory writer queue did not accept shutdown marker")
                return
        if thread is not None:
            thread.join(timeout=max(0.1, float(timeout)))

    async def execute_read_async(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Run repository-owned reads off the Memory Service event loop."""
        return await asyncio.to_thread(self.execute_read, operation)

    async def execute_write_async(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """Queue serialized writes off the Memory Service event loop."""
        return await asyncio.to_thread(self.execute_write, operation)

    async def execute_idempotent_write_async(self, **kwargs: object) -> IdempotentWriteResult:
        return await asyncio.to_thread(self.execute_idempotent_write, **kwargs)

    def execution_stats(self) -> dict[str, int | float]:
        with self._stats_lock:
            return {
                "write_operations": self.write_operations,
                "write_busy_retries": self.write_busy_retries,
                "write_busy_failures": self.write_busy_failures,
                "write_queue_depth": self._write_queue.qsize(),
                "write_queue_capacity": self._write_queue_max_size,
                "write_queue_enqueued": self.write_queue_enqueued,
                "write_queue_completed": self.write_queue_completed,
                "write_queue_backpressure": self.write_queue_backpressure,
                "write_queue_max_depth": self.write_queue_max_depth,
                "write_queue_oldest_age_seconds": round(
                    self.write_queue_oldest_age_seconds, 6
                ),
                "write_batches": self.write_batches,
                "write_batch_operations": self.write_batch_operations,
                "write_batch_commits": self.write_batch_commits,
                "write_batch_failures": self.write_batch_failures,
                "write_batch_size": self._write_batch_size,
            }
