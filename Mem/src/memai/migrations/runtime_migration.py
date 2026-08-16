from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import uuid

from memai.repository.backup import backup_sqlite_database, check_sqlite_integrity


class MemoryDatabaseMigrationConflict(RuntimeError):
    """Raised when both canonical and legacy databases exist."""


@dataclass(frozen=True, slots=True)
class MemoryDatabaseMigrationResult:
    status: str
    source: Path
    target: Path
    integrity_check: str = ""


def migrate_memory_database(
    *,
    source: str | Path,
    target: str | Path,
) -> MemoryDatabaseMigrationResult:
    """Move one legacy SQLite database to canonical storage after verification."""
    source_path = Path(source)
    target_path = Path(target)
    if source_path.resolve() == target_path.resolve():
        return MemoryDatabaseMigrationResult(
            status="already_canonical",
            source=source_path,
            target=target_path,
        )
    if target_path.exists():
        if source_path.exists():
            raise MemoryDatabaseMigrationConflict(
                "Both canonical and legacy Memory databases exist: "
                f"{target_path} and {source_path}. Refusing to choose or overwrite."
            )
        return MemoryDatabaseMigrationResult(
            status="target_exists",
            source=source_path,
            target=target_path,
        )
    if not source_path.exists():
        return MemoryDatabaseMigrationResult(
            status="source_missing",
            source=source_path,
            target=target_path,
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(
        f".{target_path.name}.migrating-{uuid.uuid4().hex}"
    )
    try:
        backup_sqlite_database(source_path, temporary)
        integrity = check_sqlite_integrity(temporary)
        if integrity.lower() != "ok":
            raise RuntimeError(
                f"Migrated Memory database failed integrity_check: {integrity}"
            )
        os.replace(temporary, target_path)
        _remove_legacy_sqlite_bundle(source_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return MemoryDatabaseMigrationResult(
        status="migrated",
        source=source_path,
        target=target_path,
        integrity_check=integrity,
    )

def _remove_legacy_sqlite_bundle(source: Path) -> None:
    cleanup_errors: list[str] = []
    for path in (
        source,
        Path(f"{source}-wal"),
        Path(f"{source}-shm"),
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{path}: {exc}")
    if cleanup_errors:
        raise RuntimeError(
            "Canonical Memory database was created but legacy cleanup failed: "
            + "; ".join(cleanup_errors)
        )
