"""Owner-scoped SQLite storage for text-only perception timeline segments."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...infrastructure.persistence.sqlite_owner import SQLiteOwnerLease
from .models import TimelineSegment


class TimelineStore:
    """Persist only timeline summaries, never frames or raw screen records."""

    def __init__(self, path: str | Path, *, owner: str = "perception-timeline-owner") -> None:
        self.path = Path(path).expanduser()
        self.owner = owner
        self._lock = threading.RLock()
        self._lease: SQLiteOwnerLease | None = None
        self._opened = False
        self.open()

    def open(self) -> None:
        with self._lock:
            if self._opened:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._lease = SQLiteOwnerLease(self.path, self.owner)
            try:
                with sqlite3.connect(str(self.path), timeout=30.0) as connection:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS perception_timeline_segments (
                            segment_id TEXT PRIMARY KEY,
                            start_at TEXT NOT NULL,
                            end_at TEXT NOT NULL,
                            scene TEXT NOT NULL,
                            application TEXT NOT NULL,
                            summary TEXT NOT NULL,
                            key_events_json TEXT NOT NULL,
                            source_record_ids_json TEXT NOT NULL,
                            source_count INTEGER NOT NULL,
                            confidence REAL NOT NULL,
                            facts_json TEXT NOT NULL,
                            inferences_json TEXT NOT NULL,
                            unknowns_json TEXT NOT NULL,
                            coverage_gaps_json TEXT NOT NULL,
                            provisional INTEGER NOT NULL,
                            schema_version TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_perception_timeline_range
                            ON perception_timeline_segments(start_at, end_at);
                        """
                    )
            except BaseException:
                if self._lease is not None:
                    self._lease.close()
                self._lease = None
                raise
            self._opened = True

    def close(self) -> None:
        with self._lock:
            if self._lease is not None:
                self._lease.close()
            self._lease = None
            self._opened = False

    def __enter__(self) -> "TimelineStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def put(self, segment: TimelineSegment) -> bool:
        """Insert one segment idempotently; return whether a row was inserted."""

        payload = segment.as_dict()
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO perception_timeline_segments (
                    segment_id, start_at, end_at, scene, application, summary,
                    key_events_json, source_record_ids_json, source_count,
                    confidence, facts_json, inferences_json, unknowns_json,
                    coverage_gaps_json, provisional, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["segment_id"], payload["start_at"], payload["end_at"],
                    payload["scene"], payload["application"], payload["summary"],
                    _dump(payload["key_events"]), _dump(payload["source_record_ids"]),
                    int(payload["source_count"]), float(payload["confidence"]),
                    _dump(payload["facts"]), _dump(payload["inferences"]),
                    _dump(payload["unknowns"]), _dump(payload["coverage_gaps"]),
                    int(bool(payload["provisional"])), payload["schema_version"], now,
                ),
            )
            return cursor.rowcount == 1

    def query(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        limit: int = 500,
    ) -> list[TimelineSegment]:
        if _utc(start_at) >= _utc(end_at):
            return []
        bounded = max(1, min(5000, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT segment_id, start_at, end_at, scene, application, summary,
                       key_events_json, source_record_ids_json, source_count,
                       confidence, facts_json, inferences_json, unknowns_json,
                       coverage_gaps_json, provisional, schema_version
                FROM perception_timeline_segments
                WHERE start_at < ? AND end_at >= ?
                ORDER BY start_at ASC, segment_id ASC
                LIMIT ?
                """,
                (_iso(end_at), _iso(start_at), bounded),
            ).fetchall()
        return [_row_to_segment(row) for row in rows]

    def delete_before(self, cutoff: datetime) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM perception_timeline_segments WHERE end_at < ?",
                (_iso(cutoff),),
            )
            return max(0, int(cursor.rowcount))

    def close_expired(self, *, retention_days: int, now: datetime | None = None) -> int:
        cutoff = _utc(now or datetime.now(timezone.utc)) - timedelta(days=max(0, int(retention_days)))
        return self.delete_before(cutoff)

    def count(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM perception_timeline_segments"
            ).fetchone()
        return int(row[0] if row else 0)

    def put_many(self, segments: list[TimelineSegment] | tuple[TimelineSegment, ...]) -> int:
        """Insert a bounded batch in one transaction and return new rows."""
        inserted = 0
        with self._lock, self._connect() as connection:
            for segment in segments:
                payload = segment.as_dict()
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO perception_timeline_segments (
                        segment_id, start_at, end_at, scene, application, summary,
                        key_events_json, source_record_ids_json, source_count,
                        confidence, facts_json, inferences_json, unknowns_json,
                        coverage_gaps_json, provisional, schema_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["segment_id"], payload["start_at"], payload["end_at"],
                        payload["scene"], payload["application"], payload["summary"],
                        _dump(payload["key_events"]), _dump(payload["source_record_ids"]),
                        int(payload["source_count"]), float(payload["confidence"]),
                        _dump(payload["facts"]), _dump(payload["inferences"]),
                        _dump(payload["unknowns"]), _dump(payload["coverage_gaps"]),
                        int(bool(payload["provisional"])), payload["schema_version"],
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                inserted += max(0, int(cursor.rowcount))
        return inserted

    def _connect(self) -> sqlite3.Connection:
        if not self._opened:
            self.open()
        connection = sqlite3.connect(str(self.path), timeout=30.0)
        connection.row_factory = sqlite3.Row
        return connection


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(list(value or ()), ensure_ascii=False, separators=(",", ":"))


def _load(value: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = []
    return tuple(str(item) for item in decoded) if isinstance(decoded, list) else ()


def _row_to_segment(row: sqlite3.Row) -> TimelineSegment:
    return TimelineSegment(
        segment_id=row["segment_id"],
        start_at=datetime.fromisoformat(row["start_at"]),
        end_at=datetime.fromisoformat(row["end_at"]),
        scene=row["scene"],
        application=row["application"],
        summary=row["summary"],
        key_events=_load(row["key_events_json"]),
        source_record_ids=_load(row["source_record_ids_json"]),
        source_count=int(row["source_count"]),
        confidence=float(row["confidence"]),
        facts=_load(row["facts_json"]),
        inferences=_load(row["inferences_json"]),
        unknowns=_load(row["unknowns_json"]),
        coverage_gaps=_load(row["coverage_gaps_json"]),
        provisional=bool(row["provisional"]),
        schema_version=row["schema_version"],
    )


__all__ = ["TimelineStore"]
