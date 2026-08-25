"""Persistent cadence state for expensive Memory maintenance rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from memai.repository.contracts import MemoryRepository
from memai.repository.sqlite import open_memory_sqlite


@dataclass(frozen=True, slots=True)
class RuleCadenceDecision:
    due: bool
    last_succeeded_at: str | None
    next_due_at: str | None
    skip_reason: str | None = None


def setup_memory_rule_state(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS memory_rule_state ("
        "rule_name TEXT PRIMARY KEY, last_attempted_at TEXT, "
        "last_succeeded_at TEXT, last_error TEXT, lease_until TEXT)"
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(memory_rule_state)").fetchall()
    }
    if "lease_until" not in columns:
        conn.execute("ALTER TABLE memory_rule_state ADD COLUMN lease_until TEXT")


def _connect(db_path: str | Path, repository: MemoryRepository | None):
    if repository is not None:
        return repository.connect()
    return open_memory_sqlite(db_path)


def _execute_read(db_path: str | Path, repository: MemoryRepository | None, operation):
    if repository is not None:
        return repository.execute_read(operation)
    conn = _connect(db_path, repository)
    try:
        return operation(conn)
    finally:
        conn.close()


def _execute_write(db_path: str | Path, repository: MemoryRepository | None, operation):
    if repository is not None:
        return repository.execute_write(operation)
    conn = _connect(db_path, repository)
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = operation(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_rule_execution(
    db_path: str | Path,
    *,
    rule_name: str,
    cadence_days: int,
    lease_minutes: int = 120,
    now: datetime | None = None,
    repository: MemoryRepository | None = None,
) -> RuleCadenceDecision:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    def write(conn):
        setup_memory_rule_state(conn)
        row = conn.execute(
            "SELECT last_succeeded_at, lease_until FROM memory_rule_state "
            "WHERE rule_name = ?",
            (rule_name,),
        ).fetchone()
        last_succeeded_at = str(row[0]) if row and row[0] else None
        next_due = None
        if last_succeeded_at:
            try:
                last_succeeded = datetime.fromisoformat(last_succeeded_at)
                if last_succeeded.tzinfo is None:
                    last_succeeded = last_succeeded.replace(tzinfo=timezone.utc)
                next_due = last_succeeded.astimezone(timezone.utc) + timedelta(
                    days=cadence_days
                )
            except ValueError:
                next_due = None
        if next_due is not None and reference < next_due:
            return RuleCadenceDecision(
                False, last_succeeded_at, next_due.isoformat(), "cadence"
            )
        lease_until_text = str(row[1]) if row and row[1] else None
        if lease_until_text:
            try:
                lease_until = datetime.fromisoformat(lease_until_text)
                if lease_until.tzinfo is None:
                    lease_until = lease_until.replace(tzinfo=timezone.utc)
                if reference < lease_until.astimezone(timezone.utc):
                    return RuleCadenceDecision(
                        False, last_succeeded_at, next_due.isoformat() if next_due else None,
                        "in_progress",
                    )
            except ValueError:
                pass
        lease_until = reference + timedelta(minutes=lease_minutes)
        conn.execute(
            "INSERT INTO memory_rule_state "
            "(rule_name, last_attempted_at, lease_until) VALUES (?, ?, ?) "
            "ON CONFLICT(rule_name) DO UPDATE SET "
            "last_attempted_at = excluded.last_attempted_at, "
            "lease_until = excluded.lease_until",
            (rule_name, reference.isoformat(), lease_until.isoformat()),
        )
        return RuleCadenceDecision(
            True, last_succeeded_at, next_due.isoformat() if next_due else None
        )

    return _execute_write(db_path, repository, write)


def record_rule_result(
    db_path: str | Path,
    *,
    rule_name: str,
    succeeded: bool,
    attempted_at: datetime | None = None,
    error: str = "",
    repository: MemoryRepository | None = None,
) -> None:
    timestamp = (attempted_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    ).isoformat()
    def write(conn):
        setup_memory_rule_state(conn)
        if succeeded:
            conn.execute(
                "INSERT INTO memory_rule_state "
                "(rule_name, last_attempted_at, last_succeeded_at, last_error) "
                "VALUES (?, ?, ?, NULL) ON CONFLICT(rule_name) DO UPDATE SET "
                "last_attempted_at = excluded.last_attempted_at, "
                "last_succeeded_at = excluded.last_succeeded_at, last_error = NULL, "
                "lease_until = NULL",
                (rule_name, timestamp, timestamp),
            )
        else:
            conn.execute(
                "INSERT INTO memory_rule_state "
                "(rule_name, last_attempted_at, last_succeeded_at, last_error) "
                "VALUES (?, ?, NULL, ?) ON CONFLICT(rule_name) DO UPDATE SET "
                "last_attempted_at = excluded.last_attempted_at, "
                "last_error = excluded.last_error, lease_until = NULL",
                (rule_name, timestamp, str(error)[:1000]),
            )

    _execute_write(db_path, repository, write)


def get_rule_state(db_path: str | Path, rule_name: str, *, repository: MemoryRepository | None = None) -> dict[str, str | None]:
    def read(conn):
        return conn.execute(
            "SELECT last_attempted_at, last_succeeded_at, last_error "
            "FROM memory_rule_state WHERE rule_name = ?",
            (rule_name,),
        ).fetchone()

    row = _execute_read(db_path, repository, read)
    return {
        "last_attempted_at": str(row[0]) if row and row[0] else None,
        "last_succeeded_at": str(row[1]) if row and row[1] else None,
        "last_error": str(row[2]) if row and row[2] else None,
    }
