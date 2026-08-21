from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException


SCHEDULE_TYPES = frozenset({"once", "daily", "weekly"})
TERMINAL_SCHEDULE_STATUSES = frozenset({"completed", "failed", "cancelled"})
INTERNAL_SCHEDULE_REQUEST_SOURCES = frozenset(
    {"companion_delegate", "companion_media", "provider_pool_test"}
)


class ScheduledRunLeaseExpiredError(ValueError):
    """A scheduled run attempted to write back after its lease expired."""

    code = "scheduled_run_lease_expired"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc)


def _parse_time_of_day(value: Any) -> time:
    text = str(value or "").strip()
    try:
        return time.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("time_of_day must use HH:MM or HH:MM:SS") from exc


def _timezone(name: Any):
    normalized = str(name or "").strip()
    if normalized:
        try:
            return ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            fixed_offsets = {
                "Asia/Shanghai": timedelta(hours=8),
                "UTC": timedelta(0),
                "Etc/UTC": timedelta(0),
            }
            if normalized in fixed_offsets:
                return timezone(fixed_offsets[normalized], name=normalized)
            raise ValueError(f"unknown timezone: {normalized}") from exc
    return datetime.now().astimezone().tzinfo or timezone.utc


class ScheduledTaskStore:
    """Transactional owner store for user schedules and API-B employee work."""

    _TASK_COLUMNS = (
        "schedule_id", "title", "instruction", "schedule_type", "timezone",
        "status", "created_by", "requested_via", "created_at", "updated_at",
        "next_run_at", "last_run_at", "last_run_status", "active_run_id",
        "run_at", "time_of_day", "worker_role", "autonomous_task_id", "weekdays_json",
    )
    _RUN_COLUMNS = (
        "run_id", "schedule_id", "due_at", "status", "owner_session_id",
        "claimed_at", "lease_expires_at", "completed_at", "result_summary", "error",
        "execution_provider", "execution_model", "elapsed_ms", "rate_limited",
        "error_code",
    )

    def __init__(
        self,
        path: str | Path,
        *,
        legacy_json_path: str | Path | None = None,
        run_history_limit: int = 1000,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
        self.run_history_limit = max(100, int(run_history_limit))
        self._lock = threading.RLock()
        self._initialize_schema()
        self._migrate_legacy_json_once()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._reader() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
        with self._transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_task_meta ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_tasks ("
                "schedule_id TEXT PRIMARY KEY, title TEXT NOT NULL, instruction TEXT NOT NULL, "
                "schedule_type TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT '', "
                "status TEXT NOT NULL, created_by TEXT NOT NULL, requested_via TEXT NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, next_run_at TEXT, "
                "last_run_at TEXT, last_run_status TEXT, active_run_id TEXT, run_at TEXT, "
                "time_of_day TEXT, worker_role TEXT NOT NULL DEFAULT '', "
                "autonomous_task_id TEXT NOT NULL DEFAULT '', "
                "weekdays_json TEXT NOT NULL DEFAULT '[]')"
            )
            task_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(scheduled_tasks)")
            }
            if "worker_role" not in task_columns:
                connection.execute(
                    "ALTER TABLE scheduled_tasks "
                    "ADD COLUMN worker_role TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    "UPDATE scheduled_tasks SET worker_role = 'general' "
                    "WHERE lower(created_by) = 'api_b'"
                )
            if "autonomous_task_id" not in task_columns:
                connection.execute(
                    "ALTER TABLE scheduled_tasks "
                    "ADD COLUMN autonomous_task_id TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_task_runs ("
                "run_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, due_at TEXT NOT NULL, "
                "status TEXT NOT NULL, owner_session_id TEXT NOT NULL, claimed_at TEXT NOT NULL, "
                "lease_expires_at TEXT NOT NULL, completed_at TEXT, "
                "result_summary TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', "
                "execution_provider TEXT NOT NULL DEFAULT '', "
                "execution_model TEXT NOT NULL DEFAULT '', elapsed_ms INTEGER, "
                "rate_limited INTEGER NOT NULL DEFAULT 0, error_code INTEGER)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduled_provider_cooldowns ("
                "provider_key TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL, "
                "failure_count INTEGER NOT NULL DEFAULT 1, "
                "last_status INTEGER NOT NULL DEFAULT 429, updated_at TEXT NOT NULL)"
            )
            run_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(scheduled_task_runs)")
            }
            for column, definition in (
                ("execution_provider", "TEXT NOT NULL DEFAULT ''"),
                ("execution_model", "TEXT NOT NULL DEFAULT ''"),
                ("elapsed_ms", "INTEGER"),
                ("rate_limited", "INTEGER NOT NULL DEFAULT 0"),
                ("error_code", "INTEGER"),
            ):
                if column not in run_columns:
                    connection.execute(
                        f"ALTER TABLE scheduled_task_runs ADD COLUMN {column} {definition}"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due "
                "ON scheduled_tasks(status, active_run_id, next_run_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_runs_status_lease "
                "ON scheduled_task_runs(status, lease_expires_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_runs_claimed "
                "ON scheduled_task_runs(claimed_at DESC)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO scheduled_task_meta(key, value) VALUES('schema_version', '6')"
            )

    def _migrate_legacy_json_once(self) -> None:
        legacy = self.legacy_json_path
        if legacy is None or not legacy.exists():
            return
        with self._reader() as connection:
            migrated = connection.execute(
                "SELECT value FROM scheduled_task_meta WHERE key = 'legacy_json_migrated'"
            ).fetchone()
        if migrated:
            self._archive_legacy_json(legacy)
            return
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"scheduled task migration source is unreadable: {legacy}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"scheduled task migration source must contain an object: {legacy}")
        schedules = list(raw.get("schedules") or [])
        runs = list(raw.get("runs") or [])
        if not all(isinstance(item, dict) for item in schedules + runs):
            raise RuntimeError(f"scheduled task migration source contains invalid records: {legacy}")

        with self._transaction() as connection:
            migrated = connection.execute(
                "SELECT value FROM scheduled_task_meta WHERE key = 'legacy_json_migrated'"
            ).fetchone()
            if not migrated:
                task_count = int(connection.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0])
                run_count = int(connection.execute("SELECT COUNT(*) FROM scheduled_task_runs").fetchone()[0])
                if task_count or run_count:
                    raise RuntimeError(
                        "scheduled task SQLite store already contains data while an unmigrated JSON store exists"
                    )
                for task in schedules:
                    self._insert_task(connection, dict(task))
                for run in runs:
                    self._insert_run(connection, dict(run))
                connection.execute(
                    "INSERT INTO scheduled_task_meta(key, value) VALUES('legacy_json_migrated', ?)",
                    (_iso_utc(_utc_now()),),
                )
                self._prune_runs(connection)

        self._archive_legacy_json(legacy)

    @staticmethod
    def _archive_legacy_json(legacy: Path) -> None:
        migrated_path = legacy.with_name(f"{legacy.name}.migrated")
        if migrated_path.exists():
            migrated_path = legacy.with_name(f"{legacy.name}.{uuid.uuid4().hex[:8]}.migrated")
        os.replace(legacy, migrated_path)

    @staticmethod
    def _next_recurring_run(task: Dict[str, Any], *, after: datetime) -> datetime:
        zone = _timezone(task.get("timezone"))
        local_after = after.astimezone(zone)
        clock = _parse_time_of_day(task.get("time_of_day"))
        candidate = datetime.combine(local_after.date(), clock, tzinfo=zone)
        schedule_type = str(task.get("schedule_type") or "")

        if schedule_type == "daily":
            if candidate <= local_after:
                candidate += timedelta(days=1)
            return candidate.astimezone(timezone.utc)

        try:
            weekdays = sorted({int(day) for day in task.get("weekdays") or []})
        except (TypeError, ValueError) as exc:
            raise ValueError("weekly schedules require weekdays between 0 and 6") from exc
        if not weekdays or any(day < 0 or day > 6 for day in weekdays):
            raise ValueError("weekly schedules require weekdays between 0 and 6")
        for day_offset in range(8):
            date_candidate = local_after.date() + timedelta(days=day_offset)
            if date_candidate.weekday() not in weekdays:
                continue
            candidate = datetime.combine(date_candidate, clock, tzinfo=zone)
            if candidate > local_after:
                return candidate.astimezone(timezone.utc)
        raise ValueError("unable to calculate next weekly run")

    def _normalize_create(self, request: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
        title = str(request.get("title") or "").strip()
        instruction = str(request.get("instruction") or "").strip()
        if not title:
            raise ValueError("title is required")
        if not instruction:
            raise ValueError("instruction is required")

        schedule_type = str(request.get("schedule_type") or "once").strip().lower()
        if schedule_type not in SCHEDULE_TYPES:
            raise ValueError("schedule_type must be once, daily, or weekly")
        zone_name = str(request.get("timezone") or "").strip()
        _timezone(zone_name)

        created_by = str(request.get("created_by") or "api_a")[:40]
        worker_role = str(request.get("worker_role") or "").strip().lower()[:40]
        autonomous_task_id = str(request.get("autonomous_task_id") or "").strip()[:80]
        if not worker_role and created_by == "api_b":
            worker_role = "general"
        if worker_role and not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", worker_role):
            raise ValueError("worker_role must use lowercase letters, digits, _ or -")
        task: Dict[str, Any] = {
            "schedule_id": str(uuid.uuid4()),
            "title": title[:200],
            "instruction": instruction[:12000],
            "schedule_type": schedule_type,
            "timezone": zone_name,
            "status": "active",
            "created_by": created_by,
            "requested_via": str(request.get("requested_via") or "cli")[:40],
            "created_at": _iso_utc(now),
            "updated_at": _iso_utc(now),
            "next_run_at": None,
            "last_run_at": None,
            "last_run_status": None,
            "active_run_id": None,
            "worker_role": worker_role,
            "autonomous_task_id": autonomous_task_id,
        }
        if schedule_type == "once":
            task["run_at"] = _iso_utc(_parse_datetime(request.get("run_at"), field="run_at"))
            task["next_run_at"] = task["run_at"]
        else:
            clock = _parse_time_of_day(request.get("time_of_day"))
            task["time_of_day"] = clock.isoformat(timespec="minutes")
            if schedule_type == "weekly":
                try:
                    weekdays = sorted({int(day) for day in request.get("weekdays") or []})
                except (TypeError, ValueError) as exc:
                    raise ValueError("weekly schedules require weekdays between 0 and 6") from exc
                if not weekdays or any(day < 0 or day > 6 for day in weekdays):
                    raise ValueError("weekly schedules require weekdays between 0 and 6")
                task["weekdays"] = weekdays
            task["next_run_at"] = _iso_utc(self._next_recurring_run(task, after=now))
        return task

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        task = {key: row[key] for key in ScheduledTaskStore._TASK_COLUMNS if key != "weekdays_json"}
        weekdays = json.loads(row["weekdays_json"] or "[]")
        if task.get("schedule_type") == "weekly":
            task["weekdays"] = list(weekdays)
        if task.get("schedule_type") != "once":
            task.pop("run_at", None)
        else:
            task.pop("time_of_day", None)
        return task

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {key: row[key] for key in ScheduledTaskStore._RUN_COLUMNS}

    @staticmethod
    def _task_values(task: Dict[str, Any]) -> tuple[Any, ...]:
        values = []
        for column in ScheduledTaskStore._TASK_COLUMNS:
            if column == "weekdays_json":
                values.append(json.dumps(task.get("weekdays") or [], ensure_ascii=False))
            elif column == "worker_role":
                values.append(
                    task.get("worker_role")
                    or ("general" if task.get("created_by") == "api_b" else "")
                )
            else:
                values.append(task.get(column))
        return tuple(values)

    @staticmethod
    def _run_values(run: Dict[str, Any]) -> tuple[Any, ...]:
        values: list[Any] = []
        for column in ScheduledTaskStore._RUN_COLUMNS:
            if column in {"execution_provider", "execution_model"}:
                values.append(run.get(column) or "")
            elif column == "rate_limited":
                values.append(1 if run.get(column) else 0)
            else:
                values.append(run.get(column))
        return tuple(values)

    def _insert_task(self, connection: sqlite3.Connection, task: Dict[str, Any]) -> None:
        columns = ", ".join(self._TASK_COLUMNS)
        placeholders = ", ".join("?" for _ in self._TASK_COLUMNS)
        connection.execute(
            f"INSERT INTO scheduled_tasks ({columns}) VALUES ({placeholders})",
            self._task_values(task),
        )

    def _replace_task(self, connection: sqlite3.Connection, task: Dict[str, Any]) -> None:
        assignments = ", ".join(f"{column} = ?" for column in self._TASK_COLUMNS[1:])
        values = self._task_values(task)
        connection.execute(
            f"UPDATE scheduled_tasks SET {assignments} WHERE schedule_id = ?",
            (*values[1:], values[0]),
        )

    def _insert_run(self, connection: sqlite3.Connection, run: Dict[str, Any]) -> None:
        columns = ", ".join(self._RUN_COLUMNS)
        placeholders = ", ".join("?" for _ in self._RUN_COLUMNS)
        connection.execute(
            f"INSERT INTO scheduled_task_runs ({columns}) VALUES ({placeholders})",
            self._run_values(run),
        )

    def _task(self, connection: sqlite3.Connection, schedule_id: str) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM scheduled_tasks WHERE schedule_id = ?",
            (schedule_id,),
        ).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        return self._task_from_row(row)

    def _run(self, connection: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM scheduled_task_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_from_row(row)

    def create(self, request: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
        current = (now or _utc_now()).astimezone(timezone.utc)
        task = self._normalize_create(dict(request or {}), now=current)
        with self._transaction() as connection:
            self._insert_task(connection, task)
        return dict(task)

    def list(self, *, include_completed: bool = True) -> list[Dict[str, Any]]:
        where = "" if include_completed else "WHERE status != 'completed'"
        with self._reader() as connection:
            rows = connection.execute(
                f"SELECT * FROM scheduled_tasks {where} "
                "ORDER BY CASE WHEN next_run_at IS NULL THEN 1 ELSE 0 END, next_run_at, created_at"
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def get(self, schedule_id: str) -> Dict[str, Any]:
        with self._reader() as connection:
            return self._task(connection, schedule_id)

    def update(
        self,
        schedule_id: str,
        request: Dict[str, Any],
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current = (now or _utc_now()).astimezone(timezone.utc)
        allowed = {
            "title", "instruction", "schedule_type", "run_at", "time_of_day",
            "weekdays", "timezone", "worker_role", "autonomous_task_id",
        }
        with self._transaction() as connection:
            existing = self._task(connection, schedule_id)
            if existing.get("active_run_id"):
                raise ValueError("a running schedule cannot be modified")
            merged = {**existing, **{key: value for key, value in request.items() if key in allowed}}
            normalized = self._normalize_create(merged, now=current)
            normalized.update(
                {
                    "schedule_id": schedule_id,
                    "created_at": existing.get("created_at"),
                    "created_by": existing.get("created_by"),
                    "requested_via": existing.get("requested_via"),
                    "worker_role": normalized.get("worker_role"),
                    "autonomous_task_id": normalized.get(
                        "autonomous_task_id", existing.get("autonomous_task_id", "")
                    ),
                    "updated_at": _iso_utc(current),
                    "status": (
                        "active"
                        if existing.get("status") in TERMINAL_SCHEDULE_STATUSES
                        else existing.get("status")
                    ),
                    "last_run_at": existing.get("last_run_at"),
                    "last_run_status": existing.get("last_run_status"),
                    "active_run_id": None,
                }
            )
            self._replace_task(connection, normalized)
            return dict(normalized)

    def set_status(self, schedule_id: str, status: str) -> Dict[str, Any]:
        normalized = str(status or "").strip().lower()
        if normalized not in {"active", "paused"}:
            raise ValueError("status must be active or paused")
        with self._transaction() as connection:
            task = self._task(connection, schedule_id)
            if task.get("active_run_id"):
                raise ValueError("a running schedule cannot be paused or resumed")
            if normalized == "active" and task.get("status") in TERMINAL_SCHEDULE_STATUSES:
                raise ValueError("a finished schedule must be updated before it can run again")
            task["status"] = normalized
            task["updated_at"] = _iso_utc(_utc_now())
            self._replace_task(connection, task)
            return dict(task)

    def cancel(
        self,
        schedule_id: str,
        *,
        reason: str = "cancelled by user",
        pause: bool = False,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Cancel a queued/running task and release its dispatch slot."""
        current = (now or _utc_now()).astimezone(timezone.utc)
        completed_at = _iso_utc(current)
        with self._transaction() as connection:
            task = self._task(connection, schedule_id)
            run_id = str(task.get("active_run_id") or "").strip()
            if run_id:
                run = self._run(connection, run_id)
                if run.get("status") == "running":
                    connection.execute(
                        "UPDATE scheduled_task_runs SET status = 'cancelled', error = ?, completed_at = ? "
                        "WHERE run_id = ? AND status = 'running'",
                        (str(reason or "cancelled by user")[:2000], completed_at, run_id),
                    )
            task["active_run_id"] = None
            task["last_run_at"] = completed_at if run_id else task.get("last_run_at")
            task["last_run_status"] = "cancelled" if run_id else task.get("last_run_status")
            task["updated_at"] = completed_at
            task["status"] = "paused" if pause else "cancelled"
            if not pause:
                task["next_run_at"] = None
            self._replace_task(connection, task)
            return dict(task)

    def delete(self, schedule_id: str) -> Dict[str, Any]:
        with self._transaction() as connection:
            task = self._task(connection, schedule_id)
            if task.get("active_run_id"):
                raise ValueError("a running schedule cannot be deleted")
            connection.execute("DELETE FROM scheduled_tasks WHERE schedule_id = ?", (schedule_id,))
            return task

    def _recover_expired_claims(self, connection: sqlite3.Connection, *, now: datetime) -> None:
        rows = connection.execute(
            "SELECT * FROM scheduled_task_runs WHERE status = 'running'"
        ).fetchall()
        completed_at = _iso_utc(now)
        for row in rows:
            run = self._run_from_row(row)
            lease = run.get("lease_expires_at")
            if not lease or _parse_datetime(lease, field="lease_expires_at") > now:
                continue
            connection.execute(
                "UPDATE scheduled_task_runs SET status = 'failed', error = ?, completed_at = ? "
                "WHERE run_id = ? AND status = 'running'",
                ("execution lease expired before writeback", completed_at, run["run_id"]),
            )
            connection.execute(
                "UPDATE scheduled_tasks SET active_run_id = NULL, last_run_at = ?, "
                "last_run_status = 'failed', updated_at = ? "
                "WHERE schedule_id = ? AND active_run_id = ?",
                (completed_at, completed_at, run["schedule_id"], run["run_id"]),
            )

    def claim_due(
        self,
        *,
        owner_session_id: str,
        now: Optional[datetime] = None,
        lease_seconds: int = 300,
        max_concurrent: int = 1,
        role_limits: Optional[Dict[str, int]] = None,
        role_providers: Optional[Dict[str, str]] = None,
        provider_limits: Optional[Dict[str, int]] = None,
        exclude_companion_work: bool = False,
        exclude_autonomous_work: bool = False,
    ) -> Optional[Dict[str, Any]]:
        owner = str(owner_session_id or "").strip()
        if not owner:
            raise ValueError("owner_session_id is required")
        current = (now or _utc_now()).astimezone(timezone.utc)
        bounded_lease = max(60, min(int(lease_seconds), 3600))
        bounded_total = max(1, min(int(max_concurrent), 16))
        limits = dict(role_limits or {})
        providers = dict(role_providers or {})
        upstream_limits = dict(provider_limits or {})
        with self._transaction() as connection:
            self._recover_expired_claims(connection, now=current)
            self._prune_runs(connection)
            running_rows = connection.execute(
                "SELECT t.worker_role, r.execution_provider FROM scheduled_task_runs r "
                "JOIN scheduled_tasks t ON t.schedule_id = r.schedule_id "
                "WHERE r.status = 'running'"
            ).fetchall()
            if len(running_rows) >= bounded_total:
                return None
            active_by_role: Dict[str, int] = {}
            active_by_provider: Dict[str, int] = {}
            for running in running_rows:
                role = str(running["worker_role"] or "").strip().lower()
                provider = str(
                    running["execution_provider"] or providers.get(role) or ""
                ).strip().lower()
                active_by_role[role] = active_by_role.get(role, 0) + 1
                if provider:
                    active_by_provider[provider] = active_by_provider.get(provider, 0) + 1
            cooling_providers = {
                str(item["provider_key"] or "").strip().lower()
                for item in connection.execute(
                    "SELECT provider_key FROM scheduled_provider_cooldowns "
                    "WHERE cooldown_until > ?",
                    (_iso_utc(current),),
                ).fetchall()
            }
            rows = connection.execute(
                "SELECT * FROM scheduled_tasks WHERE status = 'active' "
                "AND active_run_id IS NULL AND next_run_at IS NOT NULL AND next_run_at <= ? "
                "ORDER BY next_run_at, created_at",
                (_iso_utc(current),),
            ).fetchall()

            def candidate_available(candidate: sqlite3.Row) -> bool:
                requested_via = str(
                    candidate["requested_via"] or ""
                ).strip().lower()
                companion_work = requested_via in {
                    "companion_delegate",
                    "companion_media",
                } or (
                    str(candidate["created_by"] or "").strip().lower() == "api_b"
                    and requested_via not in {"provider_pool_test", "autonomous_worker"}
                )
                # The canonical task id is shared by Assist and Auto.  The
                # dispatch source, rather than the presence of that id, is
                # the mode boundary used by the claim gate.
                autonomous_work = requested_via == "autonomous_worker"
                if (
                    exclude_companion_work
                    and companion_work
                ):
                    return False
                if exclude_autonomous_work and autonomous_work:
                    return False
                role = str(candidate["worker_role"] or "").strip().lower()
                try:
                    role_limit = max(1, min(int(limits.get(role, 1)), 8))
                except (TypeError, ValueError):
                    role_limit = 1
                if active_by_role.get(role, 0) >= role_limit:
                    return False

                provider = str(providers.get(role) or "").strip().lower()
                if not provider:
                    return True
                try:
                    provider_limit = max(
                        1, min(int(upstream_limits.get(provider, 2)), 16)
                    )
                except (TypeError, ValueError):
                    provider_limit = 2
                return (
                    provider not in cooling_providers
                    and active_by_provider.get(provider, 0) < provider_limit
                )

            row = next(
                (candidate for candidate in rows if candidate_available(candidate)),
                None,
            )
            if row is None:
                return None
            task = self._task_from_row(row)
            worker_role = str(task.get("worker_role") or "").strip().lower()
            run_id = str(uuid.uuid4())
            run = {
                "run_id": run_id,
                "schedule_id": task["schedule_id"],
                "due_at": task["next_run_at"],
                "status": "running",
                "owner_session_id": owner,
                "claimed_at": _iso_utc(current),
                "lease_expires_at": _iso_utc(current + timedelta(seconds=bounded_lease)),
                "completed_at": None,
                "result_summary": "",
                "error": "",
                "execution_provider": str(providers.get(worker_role) or "")[:120],
                "execution_model": "",
                "elapsed_ms": None,
                "rate_limited": 0,
                "error_code": None,
            }
            self._insert_run(connection, run)
            task["active_run_id"] = run_id
            task["updated_at"] = _iso_utc(current)
            self._replace_task(connection, task)
            return {"task": dict(task), "run": dict(run)}

    def dispatch_state(
        self,
        *,
        now: Optional[datetime] = None,
        max_concurrent: int = 1,
        role_limits: Optional[Dict[str, int]] = None,
        role_providers: Optional[Dict[str, str]] = None,
        provider_limits: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        current = (now or _utc_now()).astimezone(timezone.utc)
        limits = dict(role_limits or {})
        providers = dict(role_providers or {})
        upstream_limits = dict(provider_limits or {})
        with self._transaction() as connection:
            self._recover_expired_claims(connection, now=current)
            running_rows = connection.execute(
                "SELECT t.worker_role, r.execution_provider FROM scheduled_task_runs r "
                "JOIN scheduled_tasks t ON t.schedule_id = r.schedule_id "
                "WHERE r.status = 'running'"
            ).fetchall()
            queued_rows = connection.execute(
                "SELECT worker_role FROM scheduled_tasks WHERE status = 'active' "
                "AND active_run_id IS NULL AND next_run_at IS NOT NULL AND next_run_at <= ? "
                "ORDER BY next_run_at, created_at",
                (_iso_utc(current),),
            ).fetchall()
            cooldown_rows = connection.execute(
                "SELECT provider_key, cooldown_until, failure_count, last_status "
                "FROM scheduled_provider_cooldowns"
            ).fetchall()
            metric_rows = connection.execute(
                "SELECT r.status, r.execution_provider, r.elapsed_ms, "
                "r.rate_limited, r.completed_at FROM scheduled_task_runs r "
                "JOIN scheduled_tasks t ON t.schedule_id = r.schedule_id "
                "WHERE r.status IN ('completed', 'failed') "
                "AND r.execution_provider != '' AND t.worker_role != '' "
                "ORDER BY r.claimed_at DESC LIMIT ?",
                (self.run_history_limit,),
            ).fetchall()

        role_state: Dict[str, Dict[str, Any]] = {}
        provider_state: Dict[str, Dict[str, Any]] = {}

        def role_bucket(role: str) -> Dict[str, Any]:
            return role_state.setdefault(
                role,
                {
                    "role": role,
                    "active": 0,
                    "queued": 0,
                    "limit": max(1, min(int(limits.get(role, 1)), 8)),
                },
            )

        def provider_bucket(provider: str) -> Dict[str, Any]:
            try:
                provider_limit = max(
                    1, min(int(upstream_limits.get(provider, 2)), 16)
                )
            except (TypeError, ValueError):
                provider_limit = 2
            return provider_state.setdefault(
                provider,
                {
                    "provider": provider,
                    "active": 0,
                    "queued": 0,
                    "limit": provider_limit,
                    "cooldown_until": "",
                    "cooldown_remaining_seconds": 0,
                    "failure_count": 0,
                    "last_status": None,
                    "metrics": {
                        "sample_size": 0,
                        "success_count": 0,
                        "success_rate_percent": None,
                        "average_elapsed_ms": None,
                        "rate_limit_count": 0,
                        "last_completed_at": "",
                    },
                },
            )

        for row in running_rows:
            role = str(row["worker_role"] or "").strip().lower()
            provider = str(row["execution_provider"] or providers.get(role) or "").strip().lower()
            role_bucket(role)["active"] += 1
            provider_bucket(provider)["active"] += 1
        for row in queued_rows:
            role = str(row["worker_role"] or "").strip().lower()
            provider = str(providers.get(role) or "").strip().lower()
            role_bucket(role)["queued"] += 1
            provider_bucket(provider)["queued"] += 1
        for role in limits:
            role_bucket(role)
        for provider in providers.values():
            provider_bucket(str(provider or "").strip().lower())
        for provider in upstream_limits:
            provider_bucket(str(provider or "").strip().lower())
        for row in cooldown_rows:
            provider = str(row["provider_key"] or "").strip().lower()
            if not provider:
                continue
            bucket = provider_bucket(provider)
            cooldown_until = _parse_datetime(
                row["cooldown_until"], field="cooldown_until"
            )
            remaining = max(0, int((cooldown_until - current).total_seconds() + 0.999))
            bucket.update(
                {
                    "cooldown_until": row["cooldown_until"] if remaining else "",
                    "cooldown_remaining_seconds": remaining,
                    "failure_count": int(row["failure_count"] or 0),
                    "last_status": int(row["last_status"] or 0) or None,
                }
            )
        metric_accumulators: Dict[str, Dict[str, Any]] = {}
        for row in metric_rows:
            provider = str(row["execution_provider"] or "").strip().lower()
            if not provider:
                continue
            metrics = metric_accumulators.setdefault(
                provider,
                {
                    "sample_size": 0,
                    "success_count": 0,
                    "elapsed_total": 0,
                    "elapsed_count": 0,
                    "rate_limit_count": 0,
                    "last_completed_at": "",
                },
            )
            if metrics["sample_size"] >= 50:
                continue
            metrics["sample_size"] += 1
            metrics["success_count"] += int(row["status"] == "completed")
            metrics["rate_limit_count"] += int(bool(row["rate_limited"]))
            if row["elapsed_ms"] is not None:
                metrics["elapsed_total"] += int(row["elapsed_ms"])
                metrics["elapsed_count"] += 1
            if not metrics["last_completed_at"]:
                metrics["last_completed_at"] = str(row["completed_at"] or "")
        for provider, metrics in metric_accumulators.items():
            sample_size = int(metrics["sample_size"])
            elapsed_count = int(metrics["elapsed_count"])
            provider_bucket(provider)["metrics"] = {
                "sample_size": sample_size,
                "success_count": int(metrics["success_count"]),
                "success_rate_percent": (
                    round(int(metrics["success_count"]) * 100 / sample_size, 1)
                    if sample_size else None
                ),
                "average_elapsed_ms": (
                    round(int(metrics["elapsed_total"]) / elapsed_count)
                    if elapsed_count else None
                ),
                "rate_limit_count": int(metrics["rate_limit_count"]),
                "last_completed_at": metrics["last_completed_at"],
            }
        provider_state.pop("", None)
        return {
            "max_concurrent": max(1, min(int(max_concurrent), 16)),
            "active_count": len(running_rows),
            "queued_count": len(queued_rows),
            "roles": list(role_state.values()),
            "providers": list(provider_state.values()),
        }

    def renew_run(
        self,
        run_id: str,
        *,
        owner_session_id: str,
        lease_seconds: int = 300,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        owner = str(owner_session_id or "").strip()
        if not owner:
            raise ValueError("owner_session_id is required")
        current = (now or _utc_now()).astimezone(timezone.utc)
        bounded_lease = max(60, min(int(lease_seconds), 3600))
        with self._transaction() as connection:
            self._recover_expired_claims(connection, now=current)
            run = self._run(connection, run_id)
            if run.get("status") != "running":
                raise ValueError("run is already finished")
            if str(run.get("owner_session_id") or "") != owner:
                raise ValueError("run belongs to another CLI session")
            task = self._task(connection, str(run.get("schedule_id") or ""))
            if task.get("active_run_id") != run_id:
                raise ValueError("schedule/run ownership is inconsistent")
            run["lease_expires_at"] = _iso_utc(current + timedelta(seconds=bounded_lease))
            connection.execute(
                "UPDATE scheduled_task_runs SET lease_expires_at = ? WHERE run_id = ?",
                (run["lease_expires_at"], run_id),
            )
            return {"task": task, "run": run}

    def finish_run(
        self,
        run_id: str,
        *,
        owner_session_id: str,
        success: bool,
        result_summary: str = "",
        error: str = "",
        execution_provider: str = "",
        execution_model: str = "",
        elapsed_ms: int | None = None,
        rate_limited: bool = False,
        retry_after_seconds: float | None = None,
        error_code: int | None = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        current = (now or _utc_now()).astimezone(timezone.utc)
        owner = str(owner_session_id or "").strip()
        expected_status = "completed" if success else "failed"
        # Commit lease recovery separately so rejecting a stale finish cannot
        # roll the recovery mutation back with the caller's failed writeback.
        with self._transaction() as recovery_connection:
            self._recover_expired_claims(recovery_connection, now=current)
        with self._transaction() as connection:
            run = self._run(connection, run_id)
            if str(run.get("owner_session_id") or "") != owner:
                raise ValueError("run belongs to another CLI session")
            if run.get("status") != "running":
                if (
                    run.get("status") == "failed"
                    and str(run.get("error") or "")
                    == "execution lease expired before writeback"
                ):
                    raise ScheduledRunLeaseExpiredError(
                        "scheduled run lease expired before writeback"
                    )
                if run.get("status") == "cancelled":
                    task = self._task(connection, str(run.get("schedule_id") or ""))
                    return {"task": task, "run": run}
                if run.get("status") != expected_status:
                    raise ValueError("run is already finished with a different result")
                try:
                    task = self._task(connection, str(run.get("schedule_id") or ""))
                except KeyError:
                    task = {"schedule_id": run.get("schedule_id"), "status": "deleted"}
                return {"task": task, "run": run}

            task = self._task(connection, str(run.get("schedule_id") or ""))
            if task.get("active_run_id") != run_id:
                raise ValueError("schedule/run ownership is inconsistent")

            completed_at = _iso_utc(current)
            provider_key = str(
                execution_provider or run.get("execution_provider") or ""
            ).strip().lower()[:120]
            run.update(
                {
                    "status": expected_status,
                    "result_summary": str(result_summary or "")[:12000],
                    "error": str(error or "")[:2000],
                    "completed_at": completed_at,
                    "execution_provider": provider_key,
                    "execution_model": str(execution_model or "")[:300],
                    "elapsed_ms": (
                        max(0, min(int(elapsed_ms), 86_400_000))
                        if elapsed_ms is not None else None
                    ),
                    "rate_limited": int(
                        not success and bool(rate_limited or error_code == 429)
                    ),
                    "error_code": error_code,
                }
            )
            if provider_key and success:
                connection.execute(
                    "DELETE FROM scheduled_provider_cooldowns WHERE provider_key = ?",
                    (provider_key,),
                )
            elif provider_key and run["rate_limited"]:
                previous = connection.execute(
                    "SELECT failure_count FROM scheduled_provider_cooldowns "
                    "WHERE provider_key = ?",
                    (provider_key,),
                ).fetchone()
                failure_count = int(previous["failure_count"] or 0) + 1 if previous else 1
                if retry_after_seconds is None:
                    delay = min(30.0 * (2 ** (failure_count - 1)), 900.0)
                else:
                    delay = max(1.0, min(float(retry_after_seconds), 900.0))
                connection.execute(
                    "INSERT INTO scheduled_provider_cooldowns "
                    "(provider_key, cooldown_until, failure_count, last_status, updated_at) "
                    "VALUES (?, ?, ?, 429, ?) "
                    "ON CONFLICT(provider_key) DO UPDATE SET "
                    "cooldown_until = excluded.cooldown_until, "
                    "failure_count = excluded.failure_count, "
                    "last_status = excluded.last_status, updated_at = excluded.updated_at",
                    (
                        provider_key,
                        _iso_utc(current + timedelta(seconds=delay)),
                        failure_count,
                        completed_at,
                    ),
                )
            connection.execute(
                "UPDATE scheduled_task_runs SET status = ?, result_summary = ?, error = ?, "
                "completed_at = ?, execution_provider = ?, execution_model = ?, "
                "elapsed_ms = ?, rate_limited = ?, error_code = ? WHERE run_id = ?",
                (
                    run["status"], run["result_summary"], run["error"],
                    run["completed_at"], run["execution_provider"],
                    run["execution_model"], run["elapsed_ms"], run["rate_limited"],
                    run["error_code"], run_id,
                ),
            )
            task["active_run_id"] = None
            task["last_run_at"] = completed_at
            task["last_run_status"] = run["status"]
            task["updated_at"] = completed_at
            if task.get("schedule_type") == "once":
                task["status"] = "completed" if success else "failed"
                task["next_run_at"] = None
            else:
                task["next_run_at"] = _iso_utc(
                    self._next_recurring_run(
                        task,
                        after=max(current, _parse_datetime(run["due_at"], field="due_at")),
                    )
                )
            self._replace_task(connection, task)
            self._prune_runs(connection)
            return {"task": dict(task), "run": dict(run)}

    def clear_provider_cooldown(self, provider_key: str) -> bool:
        provider = str(provider_key or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", provider):
            raise ValueError("invalid Provider key")
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM scheduled_provider_cooldowns WHERE provider_key = ?",
                (provider,),
            )
        return bool(cursor.rowcount)

    def _prune_runs(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM scheduled_task_runs WHERE status != 'running' AND run_id NOT IN ("
            "SELECT run_id FROM scheduled_task_runs WHERE status != 'running' "
            "ORDER BY claimed_at DESC LIMIT ?)",
            (self.run_history_limit,),
        )

    def recent_runs(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        bounded_limit = max(0, min(int(limit), 200))
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT * FROM scheduled_task_runs ORDER BY claimed_at DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]


class ScheduledTaskRuntimeMixin:
    def _scheduled_store_call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._scheduled_task_store, method)(*args, **kwargs)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="scheduled task not found") from exc
        except ScheduledRunLeaseExpiredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _scheduled_task_snapshot(self, *, include_completed: bool = True) -> Dict[str, Any]:
        now = _utc_now()
        all_tasks = self._scheduled_task_store.list(include_completed=True)
        hidden_schedule_ids = {
            str(task.get("schedule_id") or "")
            for task in all_tasks
            if task.get("requested_via") in INTERNAL_SCHEDULE_REQUEST_SOURCES
        }
        tasks = [
            task
            for task in all_tasks
            if str(task.get("schedule_id") or "") not in hidden_schedule_ids
            and (include_completed or task.get("status") != "completed")
        ]
        recent_runs = [
            run
            for run in self._scheduled_task_store.recent_runs(limit=200)
            if str(run.get("schedule_id") or "") not in hidden_schedule_ids
        ][:20]
        due_count = sum(
            1
            for task in tasks
            if task.get("status") == "active"
            and task.get("next_run_at")
            and _parse_datetime(task["next_run_at"], field="next_run_at") <= now
        )
        employee_tasks = [
            task
            for task in all_tasks
            if str(task.get("created_by") or "").strip().lower() == "api_b"
            and task.get("requested_via") != "provider_pool_test"
        ]
        employee_active_count = sum(
            1 for task in employee_tasks if task.get("active_run_id")
        )
        employee_queued_count = sum(
            1
            for task in employee_tasks
            if task.get("status") == "active" and not task.get("active_run_id")
        )
        employee_executor_status = (
            "running"
            if employee_active_count
            else "waiting_for_employee_executor"
            if employee_queued_count
            else "idle"
        )
        return {
            "status": "ok",
            "tasks": tasks,
            "count": len(tasks),
            "due_count": due_count,
            "recent_runs": recent_runs,
            "employee_executor": {
                "status": employee_executor_status,
                "claim_capability": "scheduled_task_claim",
                "active_count": employee_active_count,
                "queued_count": employee_queued_count,
            },
            "generated_at": _iso_utc(now),
        }

    async def list_scheduled_tasks(self, include_completed: bool = True) -> Dict[str, Any]:
        return self._scheduled_task_snapshot(include_completed=include_completed)

    async def create_scheduled_task(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "created", "task": self._scheduled_store_call("create", request)}

    async def get_scheduled_task(self, schedule_id: str) -> Dict[str, Any]:
        return {"status": "ok", "task": self._scheduled_store_call("get", schedule_id)}

    async def update_scheduled_task(self, schedule_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "updated",
            "task": self._scheduled_store_call("update", schedule_id, request),
        }

    async def pause_scheduled_task(self, schedule_id: str) -> Dict[str, Any]:
        return {
            "status": "paused",
            "task": self._scheduled_store_call("cancel", schedule_id, pause=True),
        }

    async def cancel_scheduled_task(self, schedule_id: str) -> Dict[str, Any]:
        return {
            "status": "cancelled",
            "task": self._scheduled_store_call("cancel", schedule_id),
        }

    async def resume_scheduled_task(self, schedule_id: str) -> Dict[str, Any]:
        return {
            "status": "active",
            "task": self._scheduled_store_call("set_status", schedule_id, "active"),
        }

    async def delete_scheduled_task(self, schedule_id: str) -> Dict[str, Any]:
        return {
            "status": "deleted",
            "task": self._scheduled_store_call("delete", schedule_id),
        }

    async def claim_scheduled_task(self, request: Dict[str, Any]) -> Dict[str, Any]:
        policy = self._provider_pool_service.dispatch_policy()
        owner_session_id = str(request.get("owner_session_id") or "")
        lease_seconds = int(request.get("lease_seconds") or 300)
        auto_gate_active = bool(
            getattr(getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False)
        )
        claimed = self._scheduled_store_call(
            "claim_due",
            owner_session_id=owner_session_id,
            lease_seconds=lease_seconds,
            exclude_companion_work=auto_gate_active
            or bool(request.get("exclude_companion_work", False)),
            exclude_autonomous_work=(not auto_gate_active)
            or bool(request.get("exclude_autonomous_work", False)),
            **policy,
        )
        if claimed:
            task = dict(claimed.get("task") or {})
            autonomous_task_id = str(task.get("autonomous_task_id") or "").strip()
            if autonomous_task_id:
                try:
                    autonomous_task = self._autonomous_task_state.claim_execution(
                        autonomous_task_id,
                        owner_session_id=owner_session_id,
                        lease_seconds=lease_seconds,
                        actor="employee_scheduler",
                        reason="员工 scheduler 已认领 API-B 自主链路任务。",
                        context={
                            "employee_task_id": task.get("schedule_id"),
                            "employee_run_id": (claimed.get("run") or {}).get("run_id"),
                        },
                    )
                except Exception as exc:
                    try:
                        self._scheduled_task_store.cancel(
                            str(task.get("schedule_id") or ""),
                            reason="自主任务 execution lease 获取失败，已释放员工派工。",
                        )
                    except Exception:
                        pass
                    raise HTTPException(
                        status_code=409,
                        detail=f"autonomous task claim failed: {exc}",
                    ) from exc
                claimed["autonomous_task"] = autonomous_task.model_dump(mode="json")
        return {"status": "claimed" if claimed else "idle", "claim": claimed}

    async def renew_scheduled_task_run(self, run_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        result = self._scheduled_store_call(
            "renew_run",
            run_id,
            owner_session_id=str(request.get("owner_session_id") or ""),
            lease_seconds=int(request.get("lease_seconds") or 300),
        )
        return {"status": "renewed", **result}

    async def finish_scheduled_task_run(self, run_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        success = request.get("success")
        if not isinstance(success, bool):
            raise HTTPException(status_code=400, detail="success must be a boolean")
        rate_limited = request.get("rate_limited", False)
        if not isinstance(rate_limited, bool):
            raise HTTPException(status_code=400, detail="rate_limited must be a boolean")
        try:
            error_code = (
                int(request["error_code"])
                if request.get("error_code") is not None else None
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="error_code must be an integer"
            ) from exc
        retry_after = request.get("retry_after_seconds")
        result = self._scheduled_store_call(
            "finish_run",
            run_id,
            owner_session_id=str(request.get("owner_session_id") or ""),
            success=success,
            result_summary=str(request.get("result_summary") or ""),
            error=str(request.get("error") or ""),
            execution_provider=str(request.get("execution_provider") or ""),
            execution_model=str(request.get("execution_model") or ""),
            elapsed_ms=request.get("elapsed_ms"),
            rate_limited=rate_limited,
            retry_after_seconds=retry_after,
            error_code=error_code,
        )
        return {"status": "completed" if success else "failed", **result}
