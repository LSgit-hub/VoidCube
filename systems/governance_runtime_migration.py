from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import uuid

from memai.governance import GovernanceEvent


class GovernanceEventMigrationConflict(RuntimeError):
    """Raised when one event ID has conflicting normalized payloads."""


@dataclass(frozen=True, slots=True)
class GovernanceEventMigrationResult:
    status: str
    sources: tuple[Path, ...]
    target: Path
    source_events: int = 0
    target_events: int = 0
    merged_events: int = 0
    duplicates_removed: int = 0


def consolidate_governance_event_logs(
    *,
    sources: list[str | Path] | tuple[str | Path, ...],
    target: str | Path,
) -> GovernanceEventMigrationResult:
    """Merge legacy and retry logs into one canonical event repository."""
    target_path = Path(target).resolve()
    source_paths = tuple(
        path
        for item in sources
        if (path := Path(item).resolve()) != target_path
    )
    source_inputs = _existing_inputs(source_paths)
    target_retry = target_path.with_suffix(".retry.jsonl")
    target_inputs = [path for path in (target_path, target_retry) if path.exists()]

    if not source_inputs and not target_retry.exists():
        return GovernanceEventMigrationResult(
            status="target_exists" if target_path.exists() else "source_missing",
            sources=source_paths,
            target=target_path,
        )

    merged: dict[str, tuple[dict, GovernanceEvent]] = {}
    duplicates = 0
    target_event_count = 0
    source_event_count = 0
    for path in target_inputs:
        rows = _load_events(path)
        target_event_count += len(rows)
        duplicates += _merge_rows(merged, rows, path=path)
    for path in source_inputs:
        rows = _load_events(path)
        source_event_count += len(rows)
        duplicates += _merge_rows(merged, rows, path=path)

    ordered = sorted(
        merged.values(),
        key=lambda item: (item[1].created_at, item[1].id),
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(
        f".{target_path.name}.migrating-{uuid.uuid4().hex}"
    )
    published = False
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for payload, _event in ordered:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        verified = _load_events(temporary)
        if [event.id for _payload, event in verified] != [
            event.id for _payload, event in ordered
        ]:
            raise RuntimeError(
                "Canonical governance log failed post-write event verification"
            )
        os.replace(temporary, target_path)
        published = True
        _remove_merged_sources((*source_inputs, target_retry), target_path)
    except Exception:
        if not published:
            temporary.unlink(missing_ok=True)
        raise

    status = "migrated" if source_inputs else "recovered_retry"
    return GovernanceEventMigrationResult(
        status=status,
        sources=source_paths,
        target=target_path,
        source_events=source_event_count,
        target_events=target_event_count,
        merged_events=len(ordered),
        duplicates_removed=duplicates,
    )


def _existing_inputs(sources: tuple[Path, ...]) -> list[Path]:
    inputs: list[Path] = []
    for source in sources:
        retry = source.with_suffix(".retry.jsonl")
        for path in (source, retry):
            if path.exists() and path not in inputs:
                inputs.append(path)
    return inputs


def _load_events(path: Path) -> list[tuple[dict, GovernanceEvent]]:
    rows: list[tuple[dict, GovernanceEvent]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                event = GovernanceEvent.from_dict(raw)
            except Exception as exc:
                raise RuntimeError(
                    f"Invalid governance event in {path} at line {line_number}: {exc}"
                ) from exc
            if not event.id or event.type != "governance_event":
                raise RuntimeError(
                    f"Invalid governance event identity in {path} at line {line_number}"
                )
            rows.append((event.to_dict(), event))
    return rows


def _merge_rows(
    merged: dict[str, tuple[dict, GovernanceEvent]],
    rows: list[tuple[dict, GovernanceEvent]],
    *,
    path: Path,
) -> int:
    duplicates = 0
    for payload, event in rows:
        existing = merged.get(event.id)
        if existing is None:
            merged[event.id] = (payload, event)
            continue
        if existing[0] != payload:
            raise GovernanceEventMigrationConflict(
                f"Governance event {event.id} has conflicting payloads in {path}"
            )
        duplicates += 1
    return duplicates


def _remove_merged_sources(paths: tuple[Path, ...], target: Path) -> None:
    cleanup_errors: list[str] = []
    for path in dict.fromkeys(paths):
        if path == target:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_errors.append(f"{path}: {exc}")
    if cleanup_errors:
        raise RuntimeError(
            "Canonical governance log was created but source cleanup failed: "
            + "; ".join(cleanup_errors)
        )
