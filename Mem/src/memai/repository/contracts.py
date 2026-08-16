"""Repository ports consumed by the Mem application layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol, runtime_checkable

from .backup import MemoryBackupManager


@runtime_checkable
class MemoryRepository(Protocol):
    """Persistent-store boundary required by the Mem application service."""

    @property
    def db_path(self) -> Path: ...

    @property
    def backup_manager(self) -> MemoryBackupManager: ...

    def initialize(self) -> None: ...

    def reconcile_schema(self) -> None: ...

    def connect(self) -> sqlite3.Connection: ...
