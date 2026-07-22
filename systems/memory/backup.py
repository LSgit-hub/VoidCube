from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Callable


class MemoryBackupError(RuntimeError):
    pass


class MemoryRestoreError(MemoryBackupError):
    pass


def backup_sqlite_database(source: Path, target: Path) -> None:
    source_uri = source.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True, timeout=30.0)
    try:
        target_conn = sqlite3.connect(str(target), timeout=30.0)
        try:
            source_conn.backup(target_conn)
            target_conn.commit()
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def check_sqlite_integrity(path: Path) -> str:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    return str(row[0] if row else "missing integrity result")


@dataclass(frozen=True, slots=True)
class MemoryBackup:
    backup_id: str
    path: Path
    created_at: str
    size_bytes: int
    integrity_check: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "path": str(self.path),
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "integrity_check": self.integrity_check,
        }


class MemoryBackupManager:
    """Online backup, validated restore, and explicit export for Memory SQLite."""

    _BACKUP_PREFIX = "memory-"
    _BACKUP_SUFFIX = ".db"
    _EXPORT_PREFIX = "memory-export-"
    _EXPORT_SUFFIX = ".json"
    _EXPORT_TABLES = (
        "memories",
        "sessions",
        "turns",
        "turns_archive",
        "compressed_memories",
        "compression_quality_audit",
    )

    def __init__(self, db_path: str | Path, *, retention_count: int = 5) -> None:
        if retention_count < 1:
            raise ValueError("retention_count must be at least one")
        self.db_path = Path(db_path)
        self.runtime_root = self.db_path.parent
        self.backup_root = self.runtime_root / "backups"
        self.export_root = self.runtime_root / "exports"
        self.retention_count = retention_count

    @staticmethod
    def _timestamp() -> tuple[str, str]:
        now = datetime.now(timezone.utc)
        return now.isoformat(), now.strftime("%Y%m%dT%H%M%S%fZ")

    @staticmethod
    def _integrity_check(path: Path) -> str:
        return check_sqlite_integrity(path)

    @staticmethod
    def _backup_database(source: Path, target: Path) -> None:
        backup_sqlite_database(source, target)

    @staticmethod
    def _restore_database(source: Path, target: Path) -> None:
        backup_sqlite_database(source, target)

    def create_backup(self) -> dict[str, Any]:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        created_at, stamp = self._timestamp()
        backup_id = f"{self._BACKUP_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}{self._BACKUP_SUFFIX}"
        target = self.backup_root / backup_id
        temporary = self.backup_root / f".{backup_id}.tmp"
        try:
            self._backup_database(self.db_path, temporary)
            integrity = self._integrity_check(temporary)
            if integrity.lower() != "ok":
                raise MemoryBackupError(
                    f"Memory backup failed integrity_check: {integrity}"
                )
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        removed = self._rotate_backups()
        backup = MemoryBackup(
            backup_id=backup_id,
            path=target,
            created_at=created_at,
            size_bytes=target.stat().st_size,
            integrity_check=integrity,
        )
        result = backup.to_dict()
        result["removed_backup_ids"] = removed
        return result

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_root.exists():
            return []
        backups = []
        for path in self._backup_paths():
            stat = path.stat()
            backups.append(
                {
                    "backup_id": path.name,
                    "path": str(path),
                    "created_at": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "size_bytes": stat.st_size,
                }
            )
        return backups

    def _backup_paths(self) -> list[Path]:
        return sorted(
            self.backup_root.glob(
                f"{self._BACKUP_PREFIX}*{self._BACKUP_SUFFIX}"
            ),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
            reverse=True,
        )

    def _rotate_backups(self) -> list[str]:
        removed: list[str] = []
        for path in self._backup_paths()[self.retention_count :]:
            path.unlink()
            removed.append(path.name)
        return removed

    def _resolve_backup(self, backup_id: str) -> Path:
        if not backup_id or Path(backup_id).name != backup_id:
            raise MemoryRestoreError("Invalid backup_id")
        if not (
            backup_id.startswith(self._BACKUP_PREFIX)
            and backup_id.endswith(self._BACKUP_SUFFIX)
        ):
            raise MemoryRestoreError("Invalid backup_id")
        candidate = self.backup_root / backup_id
        if not candidate.is_file():
            raise MemoryRestoreError(f"Memory backup not found: {backup_id}")
        return candidate

    def restore_backup(
        self,
        backup_id: str,
        *,
        post_restore: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        candidate = self._resolve_backup(backup_id)
        candidate_integrity = self._integrity_check(candidate)
        if candidate_integrity.lower() != "ok":
            raise MemoryRestoreError(
                f"Restore candidate failed integrity_check: {candidate_integrity}"
            )

        _, stamp = self._timestamp()
        rollback = self.backup_root / (
            f".restore-rollback-{stamp}-{uuid.uuid4().hex[:8]}.db"
        )
        try:
            self._backup_database(self.db_path, rollback)
            rollback_integrity = self._integrity_check(rollback)
            if rollback_integrity.lower() != "ok":
                raise MemoryRestoreError(
                    "Pre-restore snapshot failed integrity_check: "
                    f"{rollback_integrity}"
                )
            try:
                self._restore_database(candidate, self.db_path)
                restored_integrity = self._integrity_check(self.db_path)
                if restored_integrity.lower() != "ok":
                    raise MemoryRestoreError(
                        f"Restored database failed integrity_check: {restored_integrity}"
                    )
                if post_restore is not None:
                    post_restore()
                    restored_integrity = self._integrity_check(self.db_path)
                    if restored_integrity.lower() != "ok":
                        raise MemoryRestoreError(
                            "Post-restore database failed integrity_check: "
                            f"{restored_integrity}"
                        )
            except Exception as restore_error:
                try:
                    self._restore_database(rollback, self.db_path)
                    rollback_result = self._integrity_check(self.db_path)
                    if rollback_result.lower() != "ok":
                        raise MemoryRestoreError(
                            f"Rollback database failed integrity_check: {rollback_result}"
                        )
                except Exception as rollback_error:
                    raise MemoryRestoreError(
                        "Memory restore failed and rollback also failed: "
                        f"restore={restore_error}; rollback={rollback_error}"
                    ) from rollback_error
                raise MemoryRestoreError(
                    f"Memory restore failed; previous database restored: {restore_error}"
                ) from restore_error
        finally:
            rollback.unlink(missing_ok=True)

        return {
            "status": "restored",
            "backup_id": backup_id,
            "integrity_check": restored_integrity,
        }

    def export_json(self) -> dict[str, Any]:
        self.export_root.mkdir(parents=True, exist_ok=True)
        exported_at, stamp = self._timestamp()
        export_id = (
            f"{self._EXPORT_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}"
            f"{self._EXPORT_SUFFIX}"
        )
        target = self.export_root / export_id
        temporary = self.export_root / f".{export_id}.tmp"

        source_uri = self.db_path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(source_uri, uri=True, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN")
            tables = {
                table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
                for table in self._EXPORT_TABLES
            }
            conn.commit()
        finally:
            conn.close()

        payload = {
            "format": "voidcube.memory.export",
            "format_version": 1,
            "exported_at": exported_at,
            "tables": tables,
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            json.loads(temporary.read_text(encoding="utf-8"))
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return {
            "status": "exported",
            "export_id": export_id,
            "path": str(target),
            "exported_at": exported_at,
            "size_bytes": target.stat().st_size,
            "table_counts": {
                table: len(rows) for table, rows in tables.items()
            },
        }
