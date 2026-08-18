"""Persistent contracts for production evolution candidate generation cycles."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from systems.evolution_boundary import (
    classify_agent_evolution_changes,
    normalize_repo_path,
)
from systems.evolution_evaluation import MetricTarget


_COMMIT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_KNOWLEDGE_ID_PATTERN = re.compile(r"^knowledge-[0-9a-f]{64}$")
_REQUEST_ID_PATTERN = r"^evolution-candidate-request-[0-9a-f]{64}$"
_STATE_ID_PATTERN = r"^evolution-candidate-state-[0-9a-f]{64}$"
_ATTEMPT_ID_PATTERN = r"^evolution-candidate-attempt-[0-9a-f]{16}-[0-9]{4}$"
_TASK_ID_PATTERN = r"^evolution-candidate-[0-9a-f]{16}-[0-9]{4}$"
_AUTHORING_RESULT_ID_PATTERN = r"^evolution-authoring-result-[0-9a-f]{64}$"
_EXPERIMENT_RESULT_ID_PATTERN = r"^experiment-result-[0-9a-f]{64}$"


def _aware_datetime(field_name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class CandidateLearningReference(_FrozenModel):
    """Learning fact that justified a candidate-generation request."""

    learning_id: str = Field(min_length=1, max_length=200)
    completed_at: datetime
    relevance: float = Field(ge=0.0, le=1.0)
    title: str = Field(min_length=1, max_length=200)
    target_paths: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("completed_at")
    @classmethod
    def _validate_completed_at(cls, value: datetime) -> datetime:
        return _aware_datetime("completed_at", value)

    @field_validator("target_paths")
    @classmethod
    def _validate_target_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_paths(values, field_name="learning target paths")


class _EvolutionCandidateGenerationRequestContent(_FrozenModel):
    schema_version: Literal[1] = 1
    mapping_key: str = Field(min_length=1, max_length=200)
    mapping_source: str = Field(min_length=1, max_length=200)
    target_body_slot_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=4000)
    improvement_hypothesis: str = Field(min_length=1, max_length=4000)
    baseline_commit: str = Field(pattern=_COMMIT_PATTERN)
    source_learning_refs: tuple[CandidateLearningReference, ...] = Field(
        min_length=1,
        max_length=5,
    )
    knowledge_ids: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=20)
    forbidden_patterns: tuple[str, ...] = ()
    max_files_changed: int = Field(default=5, gt=0, le=20)
    test_commands: tuple[str, ...] = Field(min_length=1, max_length=20)
    command_timeout_seconds: int = Field(default=300, gt=0, le=3600)
    target_metrics: tuple[MetricTarget, ...] = Field(min_length=1, max_length=20)

    @field_validator("baseline_commit")
    @classmethod
    def _normalize_commit(cls, value: str) -> str:
        return value.lower()

    @field_validator("allowed_paths")
    @classmethod
    def _validate_allowed_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_paths(values, field_name="allowed paths")

    @field_validator("forbidden_patterns", "test_commands")
    @classmethod
    def _validate_nonempty_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(item).strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("list items cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("list items must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        learning_ids = tuple(item.learning_id for item in self.source_learning_refs)
        if len(learning_ids) != len(set(learning_ids)):
            raise ValueError("source learning references must be unique")
        if len(self.knowledge_ids) != len(set(self.knowledge_ids)) or any(
            _KNOWLEDGE_ID_PATTERN.fullmatch(item) is None for item in self.knowledge_ids
        ):
            raise ValueError("knowledge_ids must be unique content-addressed IDs")
        metrics = tuple(item.metric for item in self.target_metrics)
        if len(metrics) != len(set(metrics)):
            raise ValueError("target metrics must be unique")
        allowed = set(self.allowed_paths)
        if any(
            path not in allowed
            for ref in self.source_learning_refs
            for path in ref.target_paths
        ):
            raise ValueError("learning target paths must be included in allowed_paths")
        if self.max_files_changed > len(self.allowed_paths):
            raise ValueError("max_files_changed cannot exceed the allowed path count")
        return self


class EvolutionCandidateGenerationRequest(_EvolutionCandidateGenerationRequestContent):
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, **values: object) -> Self:
        content = _EvolutionCandidateGenerationRequestContent.model_validate(values)
        payload = content.model_dump(mode="json")
        digest = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "request_id": f"evolution-candidate-request-{digest}",
                "content_hash": digest,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _EvolutionCandidateGenerationRequestContent.model_validate(
            self.model_dump(exclude={"request_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        digest = _content_hash(self.content_payload())
        if self.content_hash != digest:
            raise ValueError("content_hash does not match candidate request")
        if self.request_id != f"evolution-candidate-request-{digest}":
            raise ValueError("request_id does not match content_hash")
        return self


CandidateGenerationStatus = Literal[
    "pending",
    "authoring",
    "evaluating",
    "authorized",
    "blocked",
    "failed",
]


class _EvolutionCandidateGenerationStateContent(_FrozenModel):
    schema_version: Literal[1] = 1
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)
    previous_state_id: str | None = Field(default=None, pattern=_STATE_ID_PATTERN)
    revision: int = Field(ge=0)
    status: CandidateGenerationStatus
    attempt_number: int = Field(default=0, ge=0, le=9999)
    attempt_id: str | None = Field(default=None, pattern=_ATTEMPT_ID_PATTERN)
    authoring_task_id: str | None = Field(default=None, pattern=_TASK_ID_PATTERN)
    authoring_result_id: str | None = Field(
        default=None,
        pattern=_AUTHORING_RESULT_ID_PATTERN,
    )
    experiment_result_id: str | None = Field(
        default=None,
        pattern=_EXPERIMENT_RESULT_ID_PATTERN,
    )
    lease_owner: str | None = Field(default=None, max_length=200)
    lease_expires_at: datetime | None = None
    cooldown_until: datetime | None = None
    error_code: str | None = Field(default=None, max_length=200)
    error_reason: str | None = Field(default=None, max_length=4000)
    requested_at: datetime
    updated_at: datetime

    @field_validator(
        "lease_expires_at",
        "cooldown_until",
        "requested_at",
        "updated_at",
    )
    @classmethod
    def _validate_timestamps(cls, value: datetime | None, info) -> datetime | None:
        if value is not None:
            return _aware_datetime(info.field_name, value)
        return value

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.updated_at < self.requested_at:
            raise ValueError("updated_at cannot precede requested_at")
        if self.revision == 0 and self.previous_state_id is not None:
            raise ValueError("initial state cannot reference a previous state")
        if self.revision > 0 and self.previous_state_id is None:
            raise ValueError("non-initial state requires previous_state_id")
        active = self.status in {"authoring", "evaluating"}
        if active != bool(self.lease_owner and self.lease_expires_at):
            raise ValueError("active state requires a complete lease")
        has_attempt = self.attempt_number > 0
        if has_attempt != bool(self.attempt_id and self.authoring_task_id):
            raise ValueError("attempt identity must match attempt_number")
        if self.status == "pending" and (
            self.attempt_id
            or self.authoring_task_id
            or self.authoring_result_id
            or self.experiment_result_id
            or self.error_code
            or self.error_reason
        ):
            raise ValueError("pending state cannot carry attempt outcome data")
        if self.status == "evaluating" and not self.authoring_result_id:
            raise ValueError("evaluating state requires authoring_result_id")
        if self.status == "authorized" and (
            not self.authoring_result_id or not self.experiment_result_id
        ):
            raise ValueError(
                "authorized state requires authoring and experiment results"
            )
        failed = self.status in {"blocked", "failed"}
        if failed != bool(self.error_code and self.error_reason):
            raise ValueError("terminal failure state requires a structured error")
        if self.cooldown_until is not None and not failed:
            raise ValueError("only blocked or failed states may have a cooldown")
        return self


class EvolutionCandidateGenerationState(_EvolutionCandidateGenerationStateContent):
    state_id: str = Field(pattern=_STATE_ID_PATTERN)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, **values: object) -> Self:
        content = _EvolutionCandidateGenerationStateContent.model_validate(values)
        payload = content.model_dump(mode="json")
        digest = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "state_id": f"evolution-candidate-state-{digest}",
                "content_hash": digest,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _EvolutionCandidateGenerationStateContent.model_validate(
            self.model_dump(exclude={"state_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        digest = _content_hash(self.content_payload())
        if self.content_hash != digest:
            raise ValueError("content_hash does not match candidate state")
        if self.state_id != f"evolution-candidate-state-{digest}":
            raise ValueError("state_id does not match content_hash")
        return self


def attempt_identity(request_id: str, attempt_number: int) -> tuple[str, str]:
    match = re.fullmatch(r"evolution-candidate-request-([0-9a-f]{64})", request_id)
    if match is None or not 1 <= attempt_number <= 9999:
        raise ValueError("invalid candidate request attempt")
    prefix = match.group(1)[:16]
    suffix = f"{attempt_number:04d}"
    return (
        f"evolution-candidate-attempt-{prefix}-{suffix}",
        f"evolution-candidate-{prefix}-{suffix}",
    )


def _validate_paths(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(normalize_repo_path(item) for item in values)
    if any(
        not item or item.startswith("/") or ".." in item.split("/")
        for item in normalized
    ):
        raise ValueError(f"{field_name} must be relative repository paths")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must be unique")
    report = classify_agent_evolution_changes(normalized)
    if not report.ok:
        raise ValueError(
            f"{field_name} exceed the child-agent evolution boundary: "
            + ", ".join(report.violations)
        )
    return normalized


__all__ = [
    "CandidateGenerationStatus",
    "CandidateLearningReference",
    "EvolutionCandidateGenerationRequest",
    "EvolutionCandidateGenerationState",
    "attempt_identity",
]
