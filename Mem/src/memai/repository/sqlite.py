"""Canonical SQLite connection policy for Mem repositories."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path


logger = logging.getLogger(__name__)


def open_memory_sqlite(
    db_path: str | Path,
    *,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=timeout)
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as exc:
        logger.debug("SQLite WAL pragma was not applied for %s: %s", db_path, exc)
    try:
        import sqlite_vec

        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
    except (ImportError, sqlite3.DatabaseError, AttributeError) as exc:
        logger.debug("Optional sqlite-vec was not loaded for %s: %s", db_path, exc)
    return connection
