"""Content-addressed JSON repository for evolution evaluation records."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from VoidCube_core.utils import atomic_json_write, interprocess_file_lock
from systems.evolution_evaluation.models import (
    BenchmarkPack,
    ExperimentResult,
    ExperimentSpec,
    ScoringPolicy,
)


INDEX_SCHEMA_VERSION = 1
_KIND_RULES = {
    "benchmark_packs": ("benchmark-pack-", "benchmark_pack_id"),
    "scoring_policies": ("scoring-policy-", "scoring_policy_id"),
    "experiment_specs": ("experiment-spec-", "experiment_spec_id"),
    "experiment_results": ("experiment-result-", "experiment_result_id"),
}
_Record = TypeVar("_Record", bound=BaseModel)
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class EvaluationRepositoryError(RuntimeError):
    """Base error for persisted evaluation records."""


class EvaluationRecordCorrupted(EvaluationRepositoryError):
    pass


class EvaluationImmutableConflict(EvaluationRepositoryError):
    pass


class EvaluationRepository(Protocol):
    def put_benchmark_pack(self, record: BenchmarkPack) -> BenchmarkPack: ...

    def get_benchmark_pack(self, record_id: str) -> BenchmarkPack | None: ...

    def put_scoring_policy(self, record: ScoringPolicy) -> ScoringPolicy: ...

    def get_scoring_policy(self, record_id: str) -> ScoringPolicy | None: ...

    def put_experiment_spec(self, record: ExperimentSpec) -> ExperimentSpec: ...

    def get_experiment_spec(self, record_id: str) -> ExperimentSpec | None: ...

    def put_experiment_result(self, record: ExperimentResult) -> ExperimentResult: ...

    def get_experiment_result(self, record_id: str) -> ExperimentResult | None: ...

    def list_ids(self, kind: str) -> tuple[str, ...]: ...


class JsonEvaluationRepository:
    """Persist immutable benchmark, policy, specification, and result records."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.benchmark_packs_root = self.root / "benchmark-packs"
        self.scoring_policies_root = self.root / "scoring-policies"
        self.experiment_specs_root = self.root / "experiments" / "specs"
        self.experiment_results_root = self.root / "experiments" / "results"
        self.index_path = self.root / "index.json"
        self.lock_path = self.root / ".repository.lock"

    def put_benchmark_pack(self, record: BenchmarkPack) -> BenchmarkPack:
        return self._put(
            kind="benchmark_packs",
            record=record,
            model_type=BenchmarkPack,
            root=self.benchmark_packs_root,
        )

    def get_benchmark_pack(self, record_id: str) -> BenchmarkPack | None:
        return self._get(
            kind="benchmark_packs",
            record_id=record_id,
            model_type=BenchmarkPack,
            root=self.benchmark_packs_root,
        )

    def put_scoring_policy(self, record: ScoringPolicy) -> ScoringPolicy:
        return self._put(
            kind="scoring_policies",
            record=record,
            model_type=ScoringPolicy,
            root=self.scoring_policies_root,
        )

    def get_scoring_policy(self, record_id: str) -> ScoringPolicy | None:
        return self._get(
            kind="scoring_policies",
            record_id=record_id,
            model_type=ScoringPolicy,
            root=self.scoring_policies_root,
        )

    def put_experiment_spec(self, record: ExperimentSpec) -> ExperimentSpec:
        return self._put(
            kind="experiment_specs",
            record=record,
            model_type=ExperimentSpec,
            root=self.experiment_specs_root,
        )

    def get_experiment_spec(self, record_id: str) -> ExperimentSpec | None:
        return self._get(
            kind="experiment_specs",
            record_id=record_id,
            model_type=ExperimentSpec,
            root=self.experiment_specs_root,
        )

    def put_experiment_result(self, record: ExperimentResult) -> ExperimentResult:
        return self._put(
            kind="experiment_results",
            record=record,
            model_type=ExperimentResult,
            root=self.experiment_results_root,
        )

    def get_experiment_result(self, record_id: str) -> ExperimentResult | None:
        return self._get(
            kind="experiment_results",
            record_id=record_id,
            model_type=ExperimentResult,
            root=self.experiment_results_root,
        )

    def list_ids(self, kind: str) -> tuple[str, ...]:
        self._kind_rule(kind)
        index = self._read_index()
        return tuple(entry["record_id"] for entry in index[kind])

    def _put(
        self,
        *,
        kind: str,
        record: _Record,
        model_type: type[_Record],
        root: Path,
    ) -> _Record:
        validated = model_type.model_validate(record.model_dump(mode="json"))
        _prefix, id_field = self._kind_rule(kind)
        record_id = str(getattr(validated, id_field))
        path = self._record_path(kind=kind, record_id=record_id, root=root)
        with interprocess_file_lock(self.lock_path):
            if path.exists():
                existing = self._read_record(path, model_type)
                if existing != validated:
                    raise EvaluationImmutableConflict(
                        f"evaluation record {record_id} already has different content"
                    )
            else:
                atomic_json_write(
                    path,
                    validated.model_dump(mode="json"),
                    sort_keys=True,
                )
            self._record_in_index(
                kind=kind,
                record_id=record_id,
                content_hash=str(getattr(validated, "content_hash")),
            )
        return validated

    def _get(
        self,
        *,
        kind: str,
        record_id: str,
        model_type: type[_Record],
        root: Path,
    ) -> _Record | None:
        path = self._record_path(kind=kind, record_id=record_id, root=root)
        return self._read_record(path, model_type) if path.exists() else None

    def _record_path(self, *, kind: str, record_id: str, root: Path) -> Path:
        prefix, _id_field = self._kind_rule(kind)
        if not re.fullmatch(rf"{re.escape(prefix)}[0-9a-f]{{64}}", str(record_id or "")):
            raise ValueError(f"invalid {kind} record ID")
        return root / f"{record_id}.json"

    def _read_record(self, path: Path, model_type: type[_Record]) -> _Record:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return model_type.model_validate(payload)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
            raise EvaluationRecordCorrupted(
                f"invalid evaluation record: {path}"
            ) from exc

    def _record_in_index(
        self,
        *,
        kind: str,
        record_id: str,
        content_hash: str,
    ) -> None:
        index = self._read_index()
        records = {entry["record_id"]: entry for entry in index[kind]}
        records[record_id] = {
            "record_id": record_id,
            "content_hash": content_hash,
        }
        index[kind] = [records[key] for key in sorted(records)]
        atomic_json_write(self.index_path, index, sort_keys=True)

    def _read_index(self) -> dict[str, object]:
        empty = {
            "schema_version": INDEX_SCHEMA_VERSION,
            **{kind: [] for kind in _KIND_RULES},
        }
        if not self.index_path.exists():
            return empty
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("index must be an object")
            if payload.get("schema_version") != INDEX_SCHEMA_VERSION:
                raise ValueError("unsupported index schema_version")
            normalized: dict[str, object] = {
                "schema_version": INDEX_SCHEMA_VERSION,
            }
            for kind in _KIND_RULES:
                entries = payload.get(kind)
                if not isinstance(entries, list):
                    raise ValueError(f"index {kind} must be a list")
                kind_records = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError("index entry must be an object")
                    record_id = str(entry.get("record_id") or "")
                    content_hash = str(entry.get("content_hash") or "")
                    self._record_path(
                        kind=kind,
                        record_id=record_id,
                        root=self.root,
                    )
                    if not _HASH_PATTERN.fullmatch(content_hash) or not record_id.endswith(content_hash):
                        raise ValueError("index hash does not match record ID")
                    kind_records.append(
                        {"record_id": record_id, "content_hash": content_hash}
                    )
                normalized[kind] = sorted(
                    kind_records,
                    key=lambda item: item["record_id"],
                )
            return normalized
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise EvaluationRecordCorrupted(
                f"invalid evaluation index: {self.index_path}"
            ) from exc

    @staticmethod
    def _kind_rule(kind: str) -> tuple[str, str]:
        try:
            return _KIND_RULES[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported evaluation record kind: {kind}") from exc
