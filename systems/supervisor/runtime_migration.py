from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


class SupervisorRuntimeMigrationConflict(RuntimeError):
    """Raised when both canonical and legacy Supervisor roots exist."""


@dataclass(frozen=True, slots=True)
class SupervisorRuntimeMigrationResult:
    status: str
    source: Path
    target: Path
    files_verified: int = 0


def migrate_supervisor_runtime(
    *,
    source: str | Path,
    target: str | Path,
) -> SupervisorRuntimeMigrationResult:
    """Move a legacy Supervisor runtime tree after content verification."""
    source_path = Path(source)
    target_path = Path(target)
    if source_path.resolve() == target_path.resolve():
        return SupervisorRuntimeMigrationResult(
            status="already_canonical",
            source=source_path,
            target=target_path,
        )
    if target_path.exists():
        if source_path.exists():
            raise SupervisorRuntimeMigrationConflict(
                "Both canonical and legacy Supervisor runtime roots exist: "
                f"{target_path} and {source_path}. Refusing to choose or merge."
            )
        return SupervisorRuntimeMigrationResult(
            status="target_exists",
            source=source_path,
            target=target_path,
        )
    if not source_path.exists():
        return SupervisorRuntimeMigrationResult(
            status="source_missing",
            source=source_path,
            target=target_path,
        )
    if not source_path.is_dir():
        raise RuntimeError(
            f"Legacy Supervisor runtime root is not a directory: {source_path}"
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(
        f".{target_path.name}.migrating-{uuid.uuid4().hex}"
    )
    published = False
    try:
        shutil.copytree(source_path, temporary)
        source_snapshot = _snapshot_files(source_path)
        target_snapshot = _snapshot_files(temporary)
        if source_snapshot != target_snapshot:
            raise RuntimeError(
                "Copied Supervisor runtime failed file-set or checksum verification"
            )
        _validate_structured_files(temporary)
        os.replace(temporary, target_path)
        published = True
        shutil.rmtree(source_path)
    except Exception:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
        raise

    return SupervisorRuntimeMigrationResult(
        status="migrated",
        source=source_path,
        target=target_path,
        files_verified=len(target_snapshot),
    )


def _snapshot_files(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(
                f"Supervisor runtime migration does not accept symlinks: {path}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        snapshot[relative] = digest.hexdigest()
    return snapshot


def _validate_structured_files(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif suffix == ".jsonl":
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            f"Invalid JSONL in {path} at line {line_number}: {exc}"
                        ) from exc
