"""Immutable contracts for versioned self-cognition snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1
_ID_PREFIX = "self-cognition-"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class ModuleDependency(_FrozenModel):
    module: str = Field(min_length=1)
    dependencies: tuple[str, ...] = ()


class RuntimeCapability(_FrozenModel):
    name: str = Field(min_length=1)
    capability_type: str = Field(min_length=1)
    version: str | None = None
    available: bool = True
    evidence_refs: tuple[str, ...] = ()


class HealthMetric(_FrozenModel):
    name: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)
    status: str = Field(pattern=r"^(healthy|degraded|failed|unknown)$")
    evidence_refs: tuple[str, ...] = ()


class _SelfCognitionContent(_FrozenModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    body_id: str = Field(min_length=1)
    git_commit: str = Field(min_length=1)
    config_digest: str = Field(pattern=_SHA256_PATTERN)
    modules: tuple[ModuleDependency, ...] = ()
    capabilities: tuple[RuntimeCapability, ...] = ()
    health_metrics: tuple[HealthMetric, ...] = ()
    known_gaps: tuple[str, ...] = ()
    uncovered_areas: tuple[str, ...] = ()
    collector_version: str = Field(min_length=1)
    collected_at: datetime

    @field_validator("collected_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collected_at must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_unique_names(self) -> Self:
        _require_unique("module", (item.module for item in self.modules))
        _require_unique("capability", (item.name for item in self.capabilities))
        _require_unique("health metric", (item.name for item in self.health_metrics))
        return self


class SelfCognitionSnapshot(_SelfCognitionContent):
    """A content-addressed statement of the current system body and health."""

    snapshot_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        content = _SelfCognitionContent.model_validate(values)
        payload = content.model_dump(mode="json")
        content_hash = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "snapshot_id": f"{_ID_PREFIX}{content_hash}",
                "content_hash": content_hash,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _SelfCognitionContent.model_validate(
            self.model_dump(exclude={"snapshot_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        expected_hash = _content_hash(self.content_payload())
        if self.content_hash != expected_hash:
            raise ValueError("content_hash does not match snapshot content")
        if self.snapshot_id != f"{_ID_PREFIX}{expected_hash}":
            raise ValueError("snapshot_id does not match content_hash")
        return self


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_unique(label: str, values: object) -> None:
    items = [str(item) for item in values]
    if len(items) != len(set(items)):
        raise ValueError(f"{label} names must be unique")
