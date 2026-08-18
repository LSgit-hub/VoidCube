"""Content-addressed persistence for evolution authoring results."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from VoidCube_app.infrastructure.persistence.file_store import atomic_json_write, interprocess_file_lock
from systems.evolution_authoring.models import EvolutionAuthoringResult


INDEX_SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"^evolution-authoring-result-[0-9a-f]{64}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvolutionAuthoringRepositoryError(RuntimeError):
    """Base error for persisted authoring results."""


class EvolutionAuthoringRecordCorrupted(EvolutionAuthoringRepositoryError):
    pass


class EvolutionAuthoringImmutableConflict(EvolutionAuthoringRepositoryError):
    pass


class EvolutionAuthoringRepository(Protocol):
    def put(self, result: EvolutionAuthoringResult) -> EvolutionAuthoringResult: ...

    def get(self, result_id: str) -> EvolutionAuthoringResult | None: ...

    def list_ids(self) -> tuple[str, ...]: ...


class JsonEvolutionAuthoringRepository:
    """Persist immutable authoring results below ``authoring/results``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.results_root = self.root / "results"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / ".repository.lock"

    def put(self, result: EvolutionAuthoringResult) -> EvolutionAuthoringResult:
        validated = EvolutionAuthoringResult.model_validate(
            result.model_dump(mode="json")
        )
        path = self._record_path(validated.authoring_result_id)
        with interprocess_file_lock(self.lock_path):
            if path.exists():
                existing = self._read_record(path)
                if existing != validated:
                    raise EvolutionAuthoringImmutableConflict(
                        f"authoring result {validated.authoring_result_id} already has different content"
                    )
            else:
                atomic_json_write(
                    path,
                    validated.model_dump(mode="json"),
                    sort_keys=True,
                )
            self._record_in_index(validated)
        return validated

    def get(self, result_id: str) -> EvolutionAuthoringResult | None:
        path = self._record_path(result_id)
        return self._read_record(path) if path.exists() else None

    def list_ids(self) -> tuple[str, ...]:
        index = self._read_index()
        return tuple(entry["authoring_result_id"] for entry in index["records"])

    def _record_path(self, result_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(str(result_id or "")):
            raise ValueError("invalid evolution authoring result id")
        return self.results_root / f"{result_id}.json"

    def _read_record(self, path: Path) -> EvolutionAuthoringResult:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return EvolutionAuthoringResult.model_validate(payload)
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValidationError,
        ) as exc:
            raise EvolutionAuthoringRecordCorrupted(
                f"invalid evolution authoring result: {path}"
            ) from exc

    def _record_in_index(self, result: EvolutionAuthoringResult) -> None:
        index = self._read_index()
        records = {entry["authoring_result_id"]: entry for entry in index["records"]}
        records[result.authoring_result_id] = {
            "authoring_result_id": result.authoring_result_id,
            "content_hash": result.content_hash,
            "finished_at": result.model_dump(mode="json")["finished_at"],
            "status": result.status,
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
                result_id = str(entry.get("authoring_result_id") or "")
                content_hash = str(entry.get("content_hash") or "")
                self._record_path(result_id)
                if not _HASH_PATTERN.fullmatch(content_hash) or not result_id.endswith(
                    content_hash
                ):
                    raise ValueError("index content_hash does not match result id")
                normalized.append(
                    {
                        "authoring_result_id": result_id,
                        "content_hash": content_hash,
                        "finished_at": str(entry.get("finished_at") or ""),
                        "status": str(entry.get("status") or ""),
                    }
                )
            return {
                "schema_version": INDEX_SCHEMA_VERSION,
                "records": sorted(
                    normalized, key=lambda item: item["authoring_result_id"]
                ),
            }
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise EvolutionAuthoringRecordCorrupted(
                f"invalid evolution authoring index: {self.index_path}"
            ) from exc


__all__ = [
    "EvolutionAuthoringImmutableConflict",
    "EvolutionAuthoringRecordCorrupted",
    "EvolutionAuthoringRepository",
    "EvolutionAuthoringRepositoryError",
    "JsonEvolutionAuthoringRepository",
]
