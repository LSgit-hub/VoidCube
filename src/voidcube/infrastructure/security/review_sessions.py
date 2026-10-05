"""Cross-process short-lived reviewer sessions backed by SQLite."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from ..config.runtime_paths import get_VoidCube_home


def resolve_review_session_path(path: str | Path | None = None) -> Path:
    """Resolve review storage paths independently of each service's cwd."""
    configured = path or (get_VoidCube_home() / "runtime" / "goals" / "review_sessions.db")
    resolved = Path(configured).expanduser()
    if not resolved.is_absolute():
        resolved = get_VoidCube_home() / resolved
    return resolved.resolve()


class SQLiteReviewSessionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = resolve_review_session_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS review_sessions (token TEXT PRIMARY KEY, expires_at REAL NOT NULL)"
            )
            connection.commit()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def prune(self, now: float | None = None) -> None:
        with closing(self._connection()) as connection:
            connection.execute("DELETE FROM review_sessions WHERE expires_at <= ?", (time.time() if now is None else now,))
            connection.commit()

    def put(self, token: str, expires_at: float) -> None:
        with closing(self._connection()) as connection:
            connection.execute("INSERT OR REPLACE INTO review_sessions(token, expires_at) VALUES (?, ?)", (token, expires_at))
            connection.commit()

    def get(self, token: str, now: float | None = None) -> float | None:
        self.prune(now)
        with closing(self._connection()) as connection:
            row = connection.execute("SELECT expires_at FROM review_sessions WHERE token = ?", (token,)).fetchone()
        return float(row[0]) if row else None

    def pop(self, token: str, default: Any = None) -> Any:
        with closing(self._connection()) as connection:
            cursor = connection.execute("DELETE FROM review_sessions WHERE token = ?", (token,))
            connection.commit()
        return True if cursor.rowcount > 0 else default

    def __contains__(self, token: object) -> bool:
        return isinstance(token, str) and self.get(token) is not None

    def __getitem__(self, token: str) -> float:
        value = self.get(token)
        if value is None:
            raise KeyError(token)
        return value

    def __setitem__(self, token: str, expires_at: float) -> None:
        self.put(token, expires_at)

    def close(self) -> None:
        """Keep the store reusable; each operation owns its SQLite connection."""
        return None

    def __enter__(self) -> "SQLiteReviewSessionStore":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


__all__ = ["SQLiteReviewSessionStore", "resolve_review_session_path"]
