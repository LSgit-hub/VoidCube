"""Durable, append-only event journal for application turns.

The journal is intentionally independent from transcript messages.  A turn can
therefore be replayed after a process interruption without guessing state from
the last assistant message.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from ...domain.contracts.events import ApplicationEvent
from ...infrastructure.config.runtime_paths import get_VoidCube_home


DEFAULT_DB_PATH = get_VoidCube_home() / "turn_events.db"


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return value


class TurnEventJournal:
    """SQLite-backed append-only event stream with per-session ordering."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path or DEFAULT_DB_PATH).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS turn_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                sequence_no INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                occurred_at REAL NOT NULL,
                UNIQUE(session_id, sequence_no)
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_events_turn "
            "ON turn_events(session_id, turn_id, sequence_no)"
        )
        self._conn.commit()

    def append(self, event: ApplicationEvent) -> int:
        session_id = str(getattr(event, "session_id", "") or "")
        turn_id = str(getattr(event, "turn_id", "") or "")
        event_type = getattr(getattr(event, "kind", None), "value", None)
        event_type = str(event_type or getattr(event, "event_type", "") or type(event).__name__)
        payload = json.dumps(_jsonable(event), ensure_ascii=False, sort_keys=True)
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM turn_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sequence_no = int(row[0]) + 1
            cursor = self._conn.execute(
                "INSERT INTO turn_events(session_id, turn_id, sequence_no, event_type, payload, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, turn_id, sequence_no, event_type, payload, time.time()),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list(self, session_id: str, *, turn_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM turn_events WHERE session_id = ?"
        params: list[Any] = [str(session_id)]
        if turn_id is not None:
            query += " AND turn_id = ?"
            params.append(str(turn_id))
        query += " ORDER BY sequence_no"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": int(row["id"]),
                "session_id": row["session_id"],
                "turn_id": row["turn_id"],
                "sequence_no": int(row["sequence_no"]),
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"]),
                "occurred_at": float(row["occurred_at"]),
            }
            for row in rows
        ]

    def recover_active_turn(self, session_id: str) -> dict[str, Any] | None:
        """Return the last started turn without a terminal event."""
        events = self.list(session_id)
        started: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        completed_turns: set[str] = set()
        terminal = {"turn.completed", "turn.failed", "turn.interrupted"}
        for event in events:
            if event["event_type"] == "turn.started":
                turn_id = str(event["turn_id"])
                started[turn_id] = event
                order.append(turn_id)
            elif event["event_type"] in terminal:
                completed_turns.add(str(event["turn_id"]))
        for turn_id in reversed(order):
            if turn_id not in completed_turns:
                return started[turn_id]
        return None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def session_summary(self, session_id: str) -> dict[str, Any]:
        """Return bounded lifecycle counts for startup and diagnostics."""
        events = self.list(session_id)
        counts: dict[str, int] = {}
        for event in events:
            kind = str(event["event_type"])
            counts[kind] = counts.get(kind, 0) + 1
        return {
            "session_id": str(session_id),
            "event_count": len(events),
            "event_types": counts,
            "active_turn": self.recover_active_turn(session_id),
        }


@dataclasses.dataclass(frozen=True, slots=True)
class JournalEvent:
    """Adapter event used by subsystems that do not own an application event."""

    event_type: str
    session_id: str
    turn_id: str = ""
    payload: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    kind: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", self.event_type)


__all__ = ["JournalEvent", "TurnEventJournal"]
