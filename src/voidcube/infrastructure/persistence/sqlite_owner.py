"""Runtime ownership lease for domain-owned SQLite files.

SQLite can serialize individual writes, but it cannot express which process is
the business owner of a file.  Each domain store acquires this small lease at
startup.  Re-entrant opens in one process are allowed; a live different PID is
rejected so accidental multi-process ownership fails before any schema or
business write is attempted.  A dead PID's lease is recoverable after a crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time


class SQLiteOwnerConflict(RuntimeError):
    """The SQLite file is already owned by another live process."""


class SQLiteOwnerLease:
    """Acquire a recoverable, process-scoped owner lease for one SQLite path."""

    _guard = threading.RLock()
    _leases: dict[Path, tuple[str, int]] = {}

    def __init__(self, db_path: str | Path, owner: str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.owner = str(owner).strip()
        if not self.owner:
            raise ValueError("SQLite owner name is required")
        self.lock_path = self.db_path.with_name(self.db_path.name + ".owner")
        self._acquired = False
        self.acquire()

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def acquire(self) -> None:
        with self._guard:
            existing = self._leases.get(self.db_path)
            if existing is not None:
                existing_owner, count = existing
                if existing_owner != self.owner:
                    raise SQLiteOwnerConflict(
                        f"SQLite file already owned in this process by {existing_owner}: {self.db_path}"
                    )
                self._leases[self.db_path] = (existing_owner, count + 1)
                self._acquired = True
                return

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": os.getpid(),
                "owner": self.owner,
                "db_path": str(self.db_path),
                "acquired_at": time.time(),
            }
            while True:
                try:
                    descriptor = os.open(
                        str(self.lock_path),
                        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    )
                    try:
                        os.write(
                            descriptor,
                            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        )
                    finally:
                        os.close(descriptor)
                    break
                except FileExistsError:
                    current: dict[str, object] = {}
                    try:
                        current = json.loads(self.lock_path.read_text(encoding="utf-8"))
                        pid = int(current.get("pid") or 0)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        pid = 0
                    if self._pid_alive(pid):
                        raise SQLiteOwnerConflict(
                            f"SQLite file is owned by process {pid} ({current.get('owner', 'unknown')}): {self.db_path}"
                        )
                    try:
                        self.lock_path.unlink()
                    except FileNotFoundError:
                        continue
            self._leases[self.db_path] = (self.owner, 1)
            self._acquired = True

    def close(self) -> None:
        with self._guard:
            if not self._acquired:
                return
            existing = self._leases.get(self.db_path)
            if existing is None:
                self._acquired = False
                return
            owner, count = existing
            if count > 1:
                self._leases[self.db_path] = (owner, count - 1)
            else:
                self._leases.pop(self.db_path, None)
                try:
                    current = json.loads(self.lock_path.read_text(encoding="utf-8"))
                    if int(current.get("pid") or 0) == os.getpid():
                        self.lock_path.unlink(missing_ok=True)
                except (OSError, TypeError, ValueError, json.JSONDecodeError):
                    self.lock_path.unlink(missing_ok=True)
            self._acquired = False

    def __enter__(self) -> "SQLiteOwnerLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


__all__ = ["SQLiteOwnerConflict", "SQLiteOwnerLease"]
