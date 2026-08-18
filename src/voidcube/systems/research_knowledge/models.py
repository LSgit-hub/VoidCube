"""Immutable contracts for externally sourced research knowledge."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


SCHEMA_VERSION = 1
_ID_PREFIX = "knowledge-"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class KnowledgeClaim(_FrozenModel):
    claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    applicability_conditions: tuple[str, ...] = ()
    applicable_modules: tuple[str, ...] = ()
    target_questions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)


class KnowledgeSource(_FrozenModel):
    source_id: str = Field(min_length=1)
    url: HttpUrl
    source_type: str = Field(min_length=1)
    retrieved_at: datetime
    published_at: datetime | None = None
    source_content_hash: str = Field(pattern=_SHA256_PATTERN)
    prompt_injection_reviewed: bool = False

    @field_validator("retrieved_at", "published_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source timestamps must include a timezone")
        return value


class KnowledgeRelation(_FrozenModel):
    related_knowledge_id: str = Field(pattern=r"^knowledge-[0-9a-f]{64}$")
    relation_type: str = Field(pattern=r"^(consistent|supplements|conflicts)$")
    rationale: str = Field(min_length=1)


class _KnowledgeArtifactContent(_FrozenModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    topic: str = Field(min_length=1)
    artifact_version: str = Field(min_length=1)
    claims: tuple[KnowledgeClaim, ...] = Field(min_length=1)
    sources: tuple[KnowledgeSource, ...] = Field(min_length=1)
    relations: tuple[KnowledgeRelation, ...] = ()
    valid_until: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    raw_research_task_id: str = Field(min_length=1)
    tool_evidence_refs: tuple[str, ...] = ()
    ingested_at: datetime

    @field_validator("valid_until", "ingested_at")
    @classmethod
    def _require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("artifact timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        _require_unique("claim_id", (item.claim_id for item in self.claims))
        _require_unique("source_id", (item.source_id for item in self.sources))
        if self.valid_until is not None and self.valid_until <= self.ingested_at:
            raise ValueError("valid_until must be later than ingested_at")
        return self


class KnowledgeArtifact(_KnowledgeArtifactContent):
    """A versioned set of atomic claims and their source evidence."""

    knowledge_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        content = _KnowledgeArtifactContent.model_validate(values)
        payload = content.model_dump(mode="json")
        content_hash = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "knowledge_id": f"{_ID_PREFIX}{content_hash}",
                "content_hash": content_hash,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _KnowledgeArtifactContent.model_validate(
            self.model_dump(exclude={"knowledge_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        expected_hash = _content_hash(self.content_payload())
        if self.content_hash != expected_hash:
            raise ValueError("content_hash does not match knowledge content")
        if self.knowledge_id != f"{_ID_PREFIX}{expected_hash}":
            raise ValueError("knowledge_id does not match content_hash")
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
        raise ValueError(f"{label} values must be unique")
