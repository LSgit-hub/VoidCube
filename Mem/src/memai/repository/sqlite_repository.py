"""Canonical SQLite implementation of the Mem repository port."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .backup import MemoryBackupManager
from .contracts import MemoryRepository
from memai.migrations.schema import MemoryDatabaseBootstrap

from .sqlite import open_memory_sqlite


class SQLiteMemoryRepository(MemoryRepository):
    def __init__(self, db_path: str | Path, *, backup_retention_count: int = 5) -> None:
        self._db_path = Path(db_path)
        self._backup_manager = MemoryBackupManager(
            self._db_path,
            retention_count=backup_retention_count,
        )
        self._bootstrap = MemoryDatabaseBootstrap(
            db_path=self._db_path,
            backup_manager=self._backup_manager,
        )

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def backup_manager(self) -> MemoryBackupManager:
        return self._backup_manager

    def initialize(self) -> None:
        self._bootstrap.initialize()

    def reconcile_schema(self) -> None:
        self._bootstrap.reconcile_schema()

    def connect(self) -> sqlite3.Connection:
        return open_memory_sqlite(self._db_path)
