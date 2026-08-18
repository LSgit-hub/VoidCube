"""Content-addressed JSON repository for self-cognition snapshots."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from VoidCube_app.infrastructure.persistence.file_store import atomic_json_write, interprocess_file_lock
from systems.self_cognition.models import SelfCognitionSnapshot


INDEX_SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"^self-cognition-[0-9a-f]{64}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class SelfCognitionRepositoryError(RuntimeError):
    """Base error for persisted self-cognition records."""


class SelfCognitionRecordCorrupted(SelfCognitionRepositoryError):
    pass


class SelfCognitionImmutableConflict(SelfCognitionRepositoryError):
    pass


class SelfCognitionRepository(Protocol):
    def put(self, snapshot: SelfCognitionSnapshot) -> SelfCognitionSnapshot: ...

    def get(self, snapshot_id: str) -> SelfCognitionSnapshot | None: ...

    def list_ids(self) -> tuple[str, ...]: ...


class JsonSelfCognitionRepository:
    """Persist immutable snapshots below ``self-cognition/snapshots``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.snapshots_root = self.root / "snapshots"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / ".repository.lock"

    def put(self, snapshot: SelfCognitionSnapshot) -> SelfCognitionSnapshot:
        validated = SelfCognitionSnapshot.model_validate(
            snapshot.model_dump(mode="json")
        )
        path = self._record_path(validated.snapshot_id)
        with interprocess_file_lock(self.lock_path):
            if path.exists():
                existing = self._read_record(path)
                if existing != validated:
                    raise SelfCognitionImmutableConflict(
                        f"snapshot {validated.snapshot_id} already has different content"
                    )
            else:
                atomic_json_write(
                    path,
                    validated.model_dump(mode="json"),
                    sort_keys=True,
                )
            self._record_in_index(validated)
        return validated

    def get(self, snapshot_id: str) -> SelfCognitionSnapshot | None:
        path = self._record_path(snapshot_id)
        return self._read_record(path) if path.exists() else None

    def list_ids(self) -> tuple[str, ...]:
        index = self._read_index()
        return tuple(entry["snapshot_id"] for entry in index["records"])

    def _record_path(self, snapshot_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(str(snapshot_id or "")):
            raise ValueError("invalid self-cognition snapshot_id")
        return self.snapshots_root / f"{snapshot_id}.json"

    def _read_record(self, path: Path) -> SelfCognitionSnapshot:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return SelfCognitionSnapshot.model_validate(payload)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raise SelfCognitionRecordCorrupted(
                f"invalid self-cognition record: {path}"
            ) from exc

    def _record_in_index(self, snapshot: SelfCognitionSnapshot) -> None:
        index = self._read_index()
        records = {
            entry["snapshot_id"]: entry
            for entry in index["records"]
        }
        records[snapshot.snapshot_id] = {
            "snapshot_id": snapshot.snapshot_id,
            "content_hash": snapshot.content_hash,
            "collected_at": snapshot.model_dump(mode="json")["collected_at"],
        }
        atomic_json_write(
            self.index_path,
            {
                "schema_version": INDEX_SCHEMA_VERSION,
                "records": [records[key] for key in sorted(records)],
            },
            sort_keys=True,
        )

    def _read_index(self) -> dict[str, object]:
        if not self.index_path.exists():
            return {"schema_version": INDEX_SCHEMA_VERSION, "records": []}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("index must be an object")
            if payload.get("schema_version") != INDEX_SCHEMA_VERSION:
                raise ValueError("unsupported index schema_version")
            records = payload.get("records")
            if not isinstance(records, list):
                raise ValueError("index records must be a list")
            normalized = []
            for entry in records:
                if not isinstance(entry, dict):
                    raise ValueError("index entry must be an object")
                snapshot_id = str(entry.get("snapshot_id") or "")
                content_hash = str(entry.get("content_hash") or "")
                self._record_path(snapshot_id)
                if not _HASH_PATTERN.fullmatch(content_hash) or not snapshot_id.endswith(content_hash):
                    raise ValueError("index content_hash does not match snapshot_id")
                normalized.append(
                    {
                        "snapshot_id": snapshot_id,
                        "content_hash": content_hash,
                        "collected_at": str(entry.get("collected_at") or ""),
                    }
                )
            return {
                "schema_version": INDEX_SCHEMA_VERSION,
                "records": sorted(normalized, key=lambda item: item["snapshot_id"]),
            }
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise SelfCognitionRecordCorrupted(
                f"invalid self-cognition index: {self.index_path}"
            ) from exc
