"""Content-addressed JSON repository for research knowledge artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ...infrastructure.persistence.file_store import atomic_json_write, interprocess_file_lock
from .models import KnowledgeArtifact


INDEX_SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"^knowledge-[0-9a-f]{64}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeRepositoryError(RuntimeError):
    """Base error for persisted knowledge artifacts."""


class KnowledgeRecordCorrupted(KnowledgeRepositoryError):
    pass


class KnowledgeImmutableConflict(KnowledgeRepositoryError):
    pass


class KnowledgeRepository(Protocol):
    def put(self, artifact: KnowledgeArtifact) -> KnowledgeArtifact: ...

    def get(self, knowledge_id: str) -> KnowledgeArtifact | None: ...

    def list_ids(self) -> tuple[str, ...]: ...


class JsonKnowledgeRepository:
    """Persist immutable artifacts below ``knowledge/artifacts``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.artifacts_root = self.root / "artifacts"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / ".repository.lock"

    def put(self, artifact: KnowledgeArtifact) -> KnowledgeArtifact:
        validated = KnowledgeArtifact.model_validate(
            artifact.model_dump(mode="json")
        )
        path = self._record_path(validated.knowledge_id)
        with interprocess_file_lock(self.lock_path):
            if path.exists():
                existing = self._read_record(path)
                if existing != validated:
                    raise KnowledgeImmutableConflict(
                        f"artifact {validated.knowledge_id} already has different content"
                    )
            else:
                atomic_json_write(
                    path,
                    validated.model_dump(mode="json"),
                    sort_keys=True,
                )
            self._record_in_index(validated)
        return validated

    def get(self, knowledge_id: str) -> KnowledgeArtifact | None:
        path = self._record_path(knowledge_id)
        return self._read_record(path) if path.exists() else None

    def list_ids(self) -> tuple[str, ...]:
        index = self._read_index()
        return tuple(entry["knowledge_id"] for entry in index["records"])

    def _record_path(self, knowledge_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(str(knowledge_id or "")):
            raise ValueError("invalid knowledge_id")
        return self.artifacts_root / f"{knowledge_id}.json"

    def _read_record(self, path: Path) -> KnowledgeArtifact:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return KnowledgeArtifact.model_validate(payload)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raise KnowledgeRecordCorrupted(f"invalid knowledge record: {path}") from exc

    def _record_in_index(self, artifact: KnowledgeArtifact) -> None:
        index = self._read_index()
        records = {entry["knowledge_id"]: entry for entry in index["records"]}
        records[artifact.knowledge_id] = {
            "knowledge_id": artifact.knowledge_id,
            "content_hash": artifact.content_hash,
            "topic": artifact.topic,
            "artifact_version": artifact.artifact_version,
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
                knowledge_id = str(entry.get("knowledge_id") or "")
                content_hash = str(entry.get("content_hash") or "")
                self._record_path(knowledge_id)
                if not _HASH_PATTERN.fullmatch(content_hash) or not knowledge_id.endswith(content_hash):
                    raise ValueError("index content_hash does not match knowledge_id")
                normalized.append(
                    {
                        "knowledge_id": knowledge_id,
                        "content_hash": content_hash,
                        "topic": str(entry.get("topic") or ""),
                        "artifact_version": str(entry.get("artifact_version") or ""),
                    }
                )
            return {
                "schema_version": INDEX_SCHEMA_VERSION,
                "records": sorted(normalized, key=lambda item: item["knowledge_id"]),
            }
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise KnowledgeRecordCorrupted(
                f"invalid knowledge index: {self.index_path}"
            ) from exc
