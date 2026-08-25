"""Repository ports consumed by the Mem application layer."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, Protocol, TypeVar, runtime_checkable

from .backup import MemoryBackupManager


T = TypeVar("T")


@runtime_checkable
class MemoryRepository(Protocol):
    """Persistent-store boundary required by the Mem application service."""

    @property
    def db_path(self) -> Path: ...

    @property
    def commit_revision(self) -> int: ...

    @property
    def backup_manager(self) -> MemoryBackupManager: ...

    def initialize(self) -> None: ...

    def reconcile_schema(self) -> None: ...

    def connect(self) -> sqlite3.Connection: ...

    def execute_read(self, operation: Callable[[sqlite3.Connection], T]) -> T: ...

    def execute_write(self, operation: Callable[[sqlite3.Connection], T]) -> T: ...

    async def execute_read_async(self, operation: Callable[[sqlite3.Connection], T]) -> T: ...

    async def execute_write_async(self, operation: Callable[[sqlite3.Connection], T]) -> T: ...

    def execute_idempotent_write(self, **kwargs: object): ...
