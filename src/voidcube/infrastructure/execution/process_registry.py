"""Persistent background-command registry with bounded output spools."""

from __future__ import annotations

import json
import codecs
import os
import queue
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import psutil

from ...domain.contracts.execution import ExecutionState, state_from_exit_code, utc_now
from ..runtime.layout import get_runtime_layout


_IS_WINDOWS = os.name == "nt"
_FINAL_STATUSES = {state.value for state in ExecutionState}
_PROVEN_TERMINAL_STATUSES = _FINAL_STATUSES - {ExecutionState.UNKNOWN.value}
_CLEANABLE_STATUSES = _FINAL_STATUSES - {ExecutionState.UNKNOWN.value}
_DEFAULT_SPOOL_BYTES = 1_000_000
_DEFAULT_TOTAL_SPOOL_BYTES = 32_000_000
_DEFAULT_RETAINED_SESSIONS = 128
_DEFAULT_RETENTION_DAYS = 7


def _parse_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass
class ProcessSession:
    id: str
    command: str
    cwd: str
    task_id: str
    kind: str
    spool_path: Path
    marker_path: Path
    pid: int | None = None
    process_create_time: float | None = None
    notify_on_complete: bool = False
    watch_patterns: tuple[str, ...] = ()
    status: str = "running"
    exit_code: int | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    output_cursor: int = 0
    notification_consumed: bool = False
    output_truncated: bool = False
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _env: Any = field(default=None, repr=False)
    _output: str = field(default="", repr=False)
    _watch_buffer: str = field(default="", repr=False)
    _observed_bytes: int = field(default=0, repr=False)
    _pending_utf8: bytes = field(default=b"", repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def snapshot(self, *, incremental: bool = False) -> dict[str, Any]:
        with self._lock:
            output = self._output[self.output_cursor:] if incremental else self._output
            if incremental:
                self.output_cursor = len(self._output)
            return {
                "session_id": self.id,
                "status": self.status,
                "pid": self.pid,
                "command": self.command,
                "output": output,
                "output_truncated": self.output_truncated,
                "exit_code": self.exit_code,
                "error": self.error,
                "started_at": self.started_at.isoformat(),
                "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            }


class ProcessRegistry:
    """Thread-safe lifecycle manager backed by SQLite and bounded spool files."""

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        *,
        max_spool_bytes: int = _DEFAULT_SPOOL_BYTES,
        max_total_spool_bytes: int = _DEFAULT_TOTAL_SPOOL_BYTES,
        max_retained_sessions: int = _DEFAULT_RETAINED_SESSIONS,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ) -> None:
        root = Path(storage_dir) if storage_dir is not None else (
            get_runtime_layout().runtime_root / "processes"
        )
        self.storage_dir = root.resolve()
        self.spool_dir = self.storage_dir / "spool"
        self.db_path = self.storage_dir / "registry.db"
        self.max_spool_bytes = max(1, int(max_spool_bytes))
        self.max_total_spool_bytes = max(self.max_spool_bytes, int(max_total_spool_bytes))
        self.max_retained_sessions = max(1, int(max_retained_sessions))
        self.retention = timedelta(days=max(0, int(retention_days)))
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, ProcessSession] = {}
        self._lock = threading.RLock()
        self.completion_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._initialize_database()
        self._recover_sessions()
        self.cleanup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS process_registry_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO process_registry_meta(key, value)
                VALUES ('schema_version', '1');
                CREATE TABLE IF NOT EXISTS process_sessions (
                    session_id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    pid INTEGER,
                    process_create_time REAL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    exit_code INTEGER,
                    error TEXT,
                    spool_path TEXT NOT NULL,
                    marker_path TEXT NOT NULL,
                    output_cursor INTEGER NOT NULL DEFAULT 0,
                    output_truncated INTEGER NOT NULL DEFAULT 0,
                    notify_on_complete INTEGER NOT NULL DEFAULT 0,
                    notification_consumed INTEGER NOT NULL DEFAULT 0,
                    watch_patterns_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_process_sessions_task
                ON process_sessions(task_id, started_at);
                """
            )

    @staticmethod
    def _new_id() -> str:
        return f"proc_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _patterns(patterns: list[str] | None) -> tuple[str, ...]:
        return tuple(dict.fromkeys(p for p in (patterns or []) if p))

    @staticmethod
    def _process_identity(pid: int | None) -> float | None:
        if not pid:
            return None
        try:
            return float(psutil.Process(pid).create_time())
        except (psutil.Error, OSError, ValueError):
            return None

    @classmethod
    def _identity_matches(cls, session: ProcessSession) -> bool:
        actual = cls._process_identity(session.pid)
        expected = session.process_create_time
        return actual is not None and expected is not None and abs(actual - expected) < 0.01

    def _persist(self, session: ProcessSession) -> None:
        with session._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO process_sessions(
                    session_id, command, cwd, task_id, kind, pid,
                    process_create_time, started_at, finished_at, status,
                    exit_code, error, spool_path, marker_path, output_cursor,
                    output_truncated, notify_on_complete, notification_consumed,
                    watch_patterns_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    finished_at=CASE
                        WHEN process_sessions.status IN
                            ('succeeded', 'failed', 'cancelled', 'timed_out')
                        THEN process_sessions.finished_at
                        ELSE excluded.finished_at
                    END,
                    status=CASE
                        WHEN process_sessions.status IN
                            ('succeeded', 'failed', 'cancelled', 'timed_out')
                        THEN process_sessions.status
                        ELSE excluded.status
                    END,
                    exit_code=CASE
                        WHEN process_sessions.status IN
                            ('succeeded', 'failed', 'cancelled', 'timed_out')
                        THEN process_sessions.exit_code
                        ELSE excluded.exit_code
                    END,
                    error=CASE
                        WHEN process_sessions.status IN
                            ('succeeded', 'failed', 'cancelled', 'timed_out')
                        THEN process_sessions.error
                        ELSE excluded.error
                    END,
                    output_cursor=MAX(
                        process_sessions.output_cursor,
                        excluded.output_cursor
                    ),
                    output_truncated=CASE
                        WHEN process_sessions.status IN
                            ('succeeded', 'failed', 'cancelled', 'timed_out')
                        THEN process_sessions.output_truncated
                        ELSE MAX(
                            process_sessions.output_truncated,
                            excluded.output_truncated
                        )
                    END,
                    notification_consumed=MAX(
                        process_sessions.notification_consumed,
                        excluded.notification_consumed
                    )
                """,
                (
                    session.id,
                    session.command[:4000],
                    session.cwd,
                    session.task_id,
                    session.kind,
                    session.pid,
                    session.process_create_time,
                    session.started_at.isoformat(),
                    session.finished_at.isoformat() if session.finished_at else None,
                    session.status,
                    session.exit_code,
                    session.error,
                    str(session.spool_path),
                    str(session.marker_path),
                    session.output_cursor,
                    int(session.output_truncated),
                    int(session.notify_on_complete),
                    int(session.notification_consumed),
                    json.dumps(session.watch_patterns),
                ),
            )

    def _store(self, session: ProcessSession) -> None:
        with self._lock:
            active = sum(
                item.status not in _PROVEN_TERMINAL_STATUSES
                for item in self._sessions.values()
            )
            if active * self.max_spool_bytes >= self.max_total_spool_bytes:
                raise RuntimeError("Process spool capacity is exhausted by active sessions")
            self._sessions[session.id] = session
        self._persist(session)

    def _load_output(self, session: ProcessSession) -> None:
        try:
            raw = session.spool_path.read_bytes()
        except FileNotFoundError:
            raw = b""
        with session._lock:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            session._output = decoder.decode(raw, final=False)
            session._pending_utf8 = decoder.getstate()[0]
            session._observed_bytes = len(raw)
            session.output_cursor = min(session.output_cursor, len(session._output))

    def _recover_sessions(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM process_sessions ORDER BY started_at"
            ).fetchall()
        for row in rows:
            session = ProcessSession(
                id=row["session_id"],
                command=row["command"],
                cwd=row["cwd"],
                task_id=row["task_id"],
                kind=row["kind"],
                pid=row["pid"],
                process_create_time=row["process_create_time"],
                started_at=_parse_datetime(row["started_at"]) or utc_now(),
                finished_at=_parse_datetime(row["finished_at"]),
                status=row["status"],
                exit_code=row["exit_code"],
                error=row["error"],
                spool_path=Path(row["spool_path"]),
                marker_path=Path(row["marker_path"]),
                output_cursor=int(row["output_cursor"]),
                output_truncated=bool(row["output_truncated"]),
                notify_on_complete=bool(row["notify_on_complete"]),
                notification_consumed=bool(row["notification_consumed"]),
                watch_patterns=tuple(json.loads(row["watch_patterns_json"])),
            )
            self._load_output(session)
            was_terminal = session.status in _PROVEN_TERMINAL_STATUSES
            with self._lock:
                self._sessions[session.id] = session
            if was_terminal:
                session._done.set()
            elif session.marker_path.exists():
                self._finish_from_marker(session)
            elif session.kind == "remote":
                self._finish(
                    session,
                    None,
                    ExecutionState.UNKNOWN,
                    "Remote execution has no persistent resume token",
                )
            else:
                identity_valid = self._identity_matches(session)
                session.status = (
                    "running"
                    if identity_valid
                    else ExecutionState.UNKNOWN.value
                )
                session.error = (
                    "Recovered process control restored from verified PID identity"
                    if identity_valid
                    else "Recovered process identity could not be verified"
                )
                self._persist(session)
                if identity_valid:
                    self._start_recovery_monitor(session)
                else:
                    session._done.set()
            if (
                was_terminal
                and session.notify_on_complete
                and not session.notification_consumed
            ):
                self.completion_queue.put(self._completion_event(session))

    def _start_recovery_monitor(self, session: ProcessSession) -> None:
        threading.Thread(
            target=self._monitor_recovered_local,
            args=(session,),
            name=f"process-recover-{session.id}",
            daemon=True,
        ).start()

    def _monitor_recovered_local(self, session: ProcessSession) -> None:
        while self._identity_matches(session):
            self._observe_spool(session)
            if session.marker_path.exists():
                break
            time.sleep(0.05)
        self._observe_spool(session)
        if session.marker_path.exists():
            self._finish_from_marker(session)
        else:
            session._done.set()
            self._persist(session)

    def get(self, session_id: str) -> ProcessSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def spawn_local(
        self,
        *,
        command: str,
        cwd: str,
        task_id: str,
        env_vars: dict | None = None,
        notify_on_complete: bool = False,
        watch_patterns: list[str] | None = None,
    ) -> ProcessSession:
        from .environments.local import _find_persistent_bash, _make_run_env

        self.cleanup()
        session_id = self._new_id()
        spool_path = self.spool_dir / f"{session_id}.log"
        marker_path = self.spool_dir / f"{session_id}.done.json"
        effective_cwd = cwd or os.getcwd()
        script = (
            f"if builtin cd -- {shlex.quote(effective_cwd)}; then\n"
            f"{command}\n"
            "else exit 126; fi"
        )
        wrapper_command = [
            sys.executable,
            "-m",
            "voidcube.infrastructure.execution.process_spool_wrapper",
            "--spool",
            str(spool_path),
            "--marker",
            str(marker_path),
            "--max-bytes",
            str(self.max_spool_bytes),
            "--",
            _find_persistent_bash(),
            "-l",
            "-c",
            script,
        ]
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "env": _make_run_env(env_vars or {}),
            "bufsize": 0,
        }
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["preexec_fn"] = os.setsid
        process = subprocess.Popen(wrapper_command, **popen_kwargs)
        session = ProcessSession(
            id=session_id,
            command=command,
            cwd=effective_cwd,
            task_id=task_id,
            kind="local",
            pid=process.pid,
            process_create_time=self._process_identity(process.pid),
            notify_on_complete=notify_on_complete,
            watch_patterns=self._patterns(watch_patterns),
            spool_path=spool_path,
            marker_path=marker_path,
            _process=process,
        )
        try:
            self._store(session)
        except Exception:
            process.kill()
            raise
        observer = threading.Thread(
            target=self._observe_until_exit,
            args=(session,),
            name=f"process-output-{session.id}",
            daemon=True,
        )
        observer.start()
        threading.Thread(
            target=self._wait_local,
            args=(session, observer),
            name=f"process-wait-{session.id}",
            daemon=True,
        ).start()
        return session

    def spawn_via_env(
        self,
        *,
        env: Any,
        command: str,
        cwd: str,
        task_id: str,
        notify_on_complete: bool = False,
        watch_patterns: list[str] | None = None,
    ) -> ProcessSession:
        self.cleanup()
        session_id = self._new_id()
        session = ProcessSession(
            id=session_id,
            command=command,
            cwd=cwd,
            task_id=task_id,
            kind="remote",
            notify_on_complete=notify_on_complete,
            watch_patterns=self._patterns(watch_patterns),
            spool_path=self.spool_dir / f"{session_id}.log",
            marker_path=self.spool_dir / f"{session_id}.done.json",
            _env=env,
        )
        self._store(session)
        threading.Thread(
            target=self._run_remote,
            args=(session,),
            name=f"process-remote-{session.id}",
            daemon=True,
        ).start()
        return session

    def _observe_until_exit(self, session: ProcessSession) -> None:
        process = session._process
        while process is not None and process.poll() is None:
            self._observe_spool(session)
            time.sleep(0.02)
        self._observe_spool(session)

    def _observe_spool(self, session: ProcessSession) -> None:
        with session._lock:
            try:
                with session.spool_path.open("rb") as handle:
                    handle.seek(session._observed_bytes)
                    raw = handle.read()
            except FileNotFoundError:
                return
            if not raw:
                return
            session._observed_bytes += len(raw)
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            text = decoder.decode(session._pending_utf8 + raw, final=False)
            session._pending_utf8 = decoder.getstate()[0]
            if text:
                self._append_output(session, text)

    def _refresh_proven_terminal_state(self, session: ProcessSession) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, exit_code, error, finished_at, output_truncated "
                "FROM process_sessions WHERE session_id = ?",
                (session.id,),
            ).fetchone()
        if row is None or str(row["status"]) not in _PROVEN_TERMINAL_STATUSES:
            return
        with session._lock:
            session.status = str(row["status"])
            session.exit_code = row["exit_code"]
            session.error = row["error"]
            session.finished_at = _parse_datetime(row["finished_at"])
            session.output_truncated = bool(row["output_truncated"])
            session._done.set()

    def _snapshot_incremental(self, session: ProcessSession) -> dict[str, Any]:
        with session._lock:
            output_end = len(session._output)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT output_cursor FROM process_sessions "
                    "WHERE session_id = ?",
                    (session.id,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"Unknown process session: {session.id}")
                output_start = min(int(row["output_cursor"] or 0), output_end)
                connection.execute(
                    "UPDATE process_sessions SET output_cursor = MAX(output_cursor, ?) "
                    "WHERE session_id = ?",
                    (output_end, session.id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        with session._lock:
            output = session._output[output_start:output_end]
            session.output_cursor = max(session.output_cursor, output_end)
            result = session.snapshot()
            result["output"] = output
            return result

    def _wait_local(self, session: ProcessSession, observer: threading.Thread) -> None:
        process = session._process
        if process is None:
            self._finish(session, None, ExecutionState.FAILED, "Process was not started")
            return
        try:
            process.wait()
            observer.join(timeout=2)
            self._observe_spool(session)
            if session.marker_path.exists():
                self._finish_from_marker(session)
            else:
                with session._lock:
                    cancelled = session.status == ExecutionState.CANCELLED.value
                self._finish(
                    session,
                    None,
                    ExecutionState.CANCELLED if cancelled else ExecutionState.UNKNOWN,
                    None if cancelled else "Process exited without a completion marker",
                )
        except Exception as exc:
            self._finish(session, None, ExecutionState.UNKNOWN, str(exc))
        finally:
            try:
                process.stdin.close()
            except (AttributeError, OSError, ValueError):
                pass

    def _finish_from_marker(self, session: ProcessSession) -> None:
        try:
            marker = json.loads(session.marker_path.read_text(encoding="utf-8"))
            exit_code = marker.get("exit_code")
            exit_code = int(exit_code) if exit_code is not None else None
            session.output_truncated = bool(marker.get("output_truncated"))
            state = state_from_exit_code(exit_code)
            error = str(marker.get("error") or "") or None
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._finish(session, None, ExecutionState.UNKNOWN, f"Invalid completion marker: {exc}")
            return
        with session._lock:
            cancelled = session.status == ExecutionState.CANCELLED.value
            if session.status == ExecutionState.UNKNOWN.value:
                session._done.clear()
        self._finish(
            session,
            exit_code,
            ExecutionState.CANCELLED if cancelled else state,
            error,
        )

    def _run_remote(self, session: ProcessSession) -> None:
        try:
            result = session._env.execute(session.command, cwd=session.cwd)
            output = str(result.get("output", ""))
            if output:
                encoded = output.encode("utf-8")
                accepted = encoded[: self.max_spool_bytes]
                session.spool_path.write_bytes(accepted)
                session._observed_bytes = len(accepted)
                session.output_truncated = len(encoded) > len(accepted)
                self._append_output(session, accepted.decode("utf-8", errors="replace"))
            with session._lock:
                cancelled = session.status == ExecutionState.CANCELLED.value
            self._finish(
                session,
                result.get("returncode"),
                ExecutionState.CANCELLED if cancelled else state_from_exit_code(result.get("returncode")),
                None,
            )
        except Exception as exc:
            with session._lock:
                cancelled = session.status == ExecutionState.CANCELLED.value
            self._finish(
                session,
                None,
                ExecutionState.CANCELLED if cancelled else ExecutionState.UNKNOWN,
                None if cancelled else str(exc),
            )

    def _append_output(self, session: ProcessSession, text: str) -> None:
        events: list[dict[str, Any]] = []
        with session._lock:
            session._output += text
            combined = session._watch_buffer + text
            lines = combined.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                session._watch_buffer = lines.pop()
            else:
                session._watch_buffer = ""
            for line in lines:
                events.extend(self._watch_events(session, line.rstrip("\r\n")))
        for event in events:
            self.completion_queue.put(event)

    @staticmethod
    def _watch_events(session: ProcessSession, line: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "watch_match",
                "session_id": session.id,
                "command": session.command,
                "pattern": pattern,
                "output": line,
                "suppressed": 0,
            }
            for pattern in session.watch_patterns
            if pattern in line
        ]

    @staticmethod
    def _completion_event(session: ProcessSession) -> dict[str, Any]:
        return {
            "type": "completion",
            "session_id": session.id,
            "command": session.command,
            "exit_code": session.exit_code,
            "state": session.status,
            "output": session._output,
            "output_truncated": session.output_truncated,
        }

    def _finish(
        self,
        session: ProcessSession,
        exit_code: int | None,
        state: ExecutionState,
        error: str | None,
    ) -> None:
        events: list[dict[str, Any]] = []
        with session._lock:
            if session._done.is_set():
                return
            if session._pending_utf8:
                self._append_output(
                    session,
                    session._pending_utf8.decode("utf-8", errors="replace"),
                )
                session._pending_utf8 = b""
            if session._watch_buffer:
                events = self._watch_events(session, session._watch_buffer)
                session._watch_buffer = ""
            session.exit_code = exit_code
            session.status = state.value
            session.error = error or session.error
            session.finished_at = utc_now()
            session._done.set()
            if session.notify_on_complete:
                events.append(self._completion_event(session))
        self._persist(session)
        for event in events:
            self.completion_queue.put(event)

    def list_sessions(self, task_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        if task_id is not None:
            sessions = [session for session in sessions if session.task_id == task_id]
        results = []
        for session in sessions:
            result = session.snapshot()
            result.pop("output")
            results.append(result)
        return results

    def has_active_processes(self, task_id: str) -> bool:
        with self._lock:
            sessions = list(self._sessions.values())
        return any(
            session.task_id == task_id
            and session.status not in _PROVEN_TERMINAL_STATUSES
            for session in sessions
        )

    def poll(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        self._refresh_proven_terminal_state(session)
        if (
            session.status == ExecutionState.UNKNOWN.value
            and session.marker_path.exists()
        ):
            self._finish_from_marker(session)
        self._observe_spool(session)
        result = self._snapshot_incremental(session)
        if result["status"] in _PROVEN_TERMINAL_STATUSES:
            self.mark_completion_consumed(session_id)
        return result

    def wait(self, session_id: str, timeout: float | None = None) -> dict[str, Any]:
        session = self._require(session_id)
        self._refresh_proven_terminal_state(session)
        if (
            session.status == ExecutionState.UNKNOWN.value
            and session.marker_path.exists()
        ):
            self._finish_from_marker(session)
        finished = session._done.wait(timeout)
        self._observe_spool(session)
        result = self._snapshot_incremental(session)
        result["timed_out"] = not finished
        if finished:
            self.mark_completion_consumed(session_id)
        return result

    def write(self, session_id: str, data: str) -> dict[str, Any]:
        session = self._require(session_id)
        if session.kind != "local":
            raise ValueError("Remote background sessions do not support stdin")
        process = session._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise ValueError("Process stdin is not available")
        process.stdin.write(data.encode("utf-8"))
        process.stdin.flush()
        return self._snapshot_incremental(session)

    def close(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        if session.kind != "local":
            raise ValueError("Remote background sessions do not support stdin")
        process = session._process
        if process is None or process.stdin is None or process.stdin.closed:
            raise ValueError("Process stdin is not available")
        process.stdin.close()
        return self._snapshot_incremental(session)

    def kill(self, session_id: str) -> dict[str, Any]:
        session = self._require(session_id)
        self._kill_session(session)
        session._done.wait(2)
        self.mark_completion_consumed(session_id)
        return self._snapshot_incremental(session)

    def _kill_session(self, session: ProcessSession) -> bool:
        with session._lock:
            if session.status != "running":
                return False
            session.status = ExecutionState.CANCELLED.value
            process = session._process
        self._persist(session)
        if session.kind == "remote":
            try:
                from .terminal_tool import cleanup_vm
                cleanup_vm(session.task_id)
            finally:
                self._finish(session, None, ExecutionState.CANCELLED, None)
            return True
        if process is not None:
            try:
                if _IS_WINDOWS:
                    result = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    if result.returncode != 0 and process.poll() is None:
                        process.kill()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (OSError, subprocess.SubprocessError):
                try:
                    process.kill()
                except OSError:
                    pass
        elif session.pid is not None and self._identity_matches(session):
            try:
                recovered = psutil.Process(session.pid)
                children = recovered.children(recursive=True)
                for child in children:
                    child.terminate()
                recovered.terminate()
                _, alive = psutil.wait_procs([*children, recovered], timeout=1)
                for item in alive:
                    item.kill()
            except (psutil.Error, OSError):
                pass
            self._finish(session, None, ExecutionState.CANCELLED, None)
        return True

    def kill_all(self, task_id: str | None = None) -> int:
        with self._lock:
            sessions = list(self._sessions.values())
        targets = [
            session
            for session in sessions
            if session.status == "running"
            and (task_id is None or session.task_id == task_id)
        ]
        for session in targets:
            self._kill_session(session)
        return len(targets)

    def is_completion_consumed(self, session_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT notification_consumed FROM process_sessions "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return bool(row and row["notification_consumed"])

    def mark_completion_consumed(self, session_id: str) -> None:
        session = self._require(session_id)
        with session._lock:
            session.notification_consumed = True
        with self._connect() as connection:
            connection.execute(
                "UPDATE process_sessions SET notification_consumed = 1 "
                "WHERE session_id = ?",
                (session_id,),
            )

    def cleanup(self) -> int:
        now = utc_now()
        with self._lock:
            terminal = sorted(
                (
                    item
                    for item in self._sessions.values()
                    if item.status in _CLEANABLE_STATUSES
                ),
                key=lambda item: item.finished_at or item.started_at,
            )
            total_bytes = sum(
                path.stat().st_size
                for path in self.spool_dir.glob("*.log")
                if path.is_file()
            )
            delete: list[ProcessSession] = []
            retained_count = len(self._sessions)
            for session in terminal:
                expired = bool(
                    session.finished_at and now - session.finished_at > self.retention
                )
                oversized = total_bytes > self.max_total_spool_bytes
                over_count = retained_count > self.max_retained_sessions
                if not (expired or oversized or over_count):
                    continue
                try:
                    size = session.spool_path.stat().st_size
                except FileNotFoundError:
                    size = 0
                total_bytes -= size
                retained_count -= 1
                delete.append(session)
            for session in delete:
                self._sessions.pop(session.id, None)
        if not delete:
            return 0
        with self._connect() as connection:
            connection.executemany(
                "DELETE FROM process_sessions WHERE session_id = ?",
                [(session.id,) for session in delete],
            )
        for session in delete:
            for path in (session.spool_path, session.marker_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        return len(delete)

    def _require(self, session_id: str) -> ProcessSession:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Unknown process session: {session_id}")
        return session


process_registry = ProcessRegistry()


PROCESS_SCHEMA = {
    "description": "Inspect and control background terminal sessions.",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "poll", "wait", "write", "close", "kill"],
                "description": "Operation to perform.",
            },
            "session_id": {"type": "string", "description": "Background session ID."},
            "data": {"type": "string", "description": "Text to write to process stdin."},
            "timeout": {
                "type": "number",
                "minimum": 0,
                "maximum": 3600,
                "description": "Maximum seconds for wait; omitted means wait until completion.",
            },
        },
        "required": ["action"],
    },
}


def process_tool(args: dict | None = None, **_: Any) -> str:
    args = args or {}
    action = args.get("action")
    try:
        if action == "list":
            result: dict[str, Any] = {"sessions": process_registry.list_sessions()}
        else:
            session_id = args.get("session_id")
            if not session_id:
                raise ValueError(f"session_id is required for action {action!r}")
            if action == "poll":
                result = process_registry.poll(session_id)
            elif action == "wait":
                result = process_registry.wait(session_id, args.get("timeout"))
            elif action == "write":
                data = args.get("data")
                if not isinstance(data, str):
                    raise ValueError("data must be a string")
                result = process_registry.write(session_id, data)
            elif action == "close":
                result = process_registry.close(session_id)
            elif action == "kill":
                result = process_registry.kill(session_id)
            else:
                raise ValueError(f"Unknown process action: {action!r}")
        return json.dumps({"success": True, **result}, ensure_ascii=False)
    except (KeyError, ValueError, OSError) as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


from ...extensions.tools.registry import registry

registry.register(
    name="process",
    toolset="terminal",
    schema=PROCESS_SCHEMA,
    handler=process_tool,
    max_result_size_chars=100_000,
    effect="non_idempotent_write",
)
