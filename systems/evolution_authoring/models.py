"""Immutable contracts for governed evolution candidate authoring."""

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
from systems.evolution_evaluation.models import ExecutionEnvironmentManifest


_COMMIT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_TASK_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class EvolutionAuthoringSpec(_FrozenModel):
    schema_version: Literal[1] = 1
    task_id: str = Field(pattern=_TASK_ID_PATTERN)
    objective: str = Field(min_length=1, max_length=4000)
    improvement_hypothesis: str = Field(min_length=1, max_length=4000)
    baseline_commit: str = Field(pattern=_COMMIT_PATTERN)
    allowed_paths: tuple[str, ...] = Field(min_length=1, max_length=20)
    forbidden_patterns: tuple[str, ...] = ()
    max_files_changed: int = Field(default=5, gt=0, le=20)
    test_commands: tuple[str, ...] = Field(min_length=1, max_length=20)
    command_timeout_seconds: int = Field(default=300, gt=0, le=3600)
    commit_message: str = Field(min_length=1, max_length=200)

    @field_validator("task_id")
    @classmethod
    def _validate_git_ref_task_id(cls, value: str) -> str:
        if ".." in value or value.endswith((".", ".lock")):
            raise ValueError("task id is not safe for a Git candidate ref")
        return value

    @field_validator("baseline_commit")
    @classmethod
    def _normalize_commit(cls, value: str) -> str:
        return value.lower()

    @field_validator("allowed_paths")
    @classmethod
    def _validate_allowed_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_repo_path(item) for item in values)
        if any(
            not item or item.startswith("/") or ".." in item.split("/")
            for item in normalized
        ):
            raise ValueError("allowed paths must be relative repository paths")
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed paths must be unique")
        report = classify_agent_evolution_changes(normalized)
        if not report.ok:
            raise ValueError(
                "allowed paths exceed the child-agent evolution boundary: "
                + ", ".join(report.violations)
            )
        return normalized

    @field_validator("forbidden_patterns", "test_commands")
    @classmethod
    def _normalize_nonempty_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(str(item).strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("list items cannot be empty")
        return normalized

    @field_validator("commit_message")
    @classmethod
    def _single_line_commit_message(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("commit message must be one line")
        return value


class EvolutionAuthoringContext(_FrozenModel):
    task_id: str
    objective: str
    improvement_hypothesis: str
    baseline_commit: str
    execution_workspace_path: str
    allowed_paths: tuple[str, ...]
    forbidden_patterns: tuple[str, ...]
    max_files_changed: int
    stop_conditions: tuple[str, ...]
    environment_manifest: ExecutionEnvironmentManifest


class AuthoringAgentReport(_FrozenModel):
    completed: bool
    summary: str = Field(default="", max_length=4000)


class AuthoringCommandEvidence(_FrozenModel):
    command: str
    exit_code: int
    output: str = ""
    timed_out: bool = False
    security_scanner_status: Literal[
        "available",
        "disabled",
        "unavailable",
        "timeout",
        "error",
    ] | None = None
    container_disk_quota_status: Literal[
        "enforced",
        "unsupported",
        "not_requested",
        "not_applicable",
    ] | None = None


AuthoringStatus = Literal[
    "candidate_created",
    "blocked",
    "authoring_failed",
    "no_changes",
    "policy_violation",
    "test_failed",
    "commit_failed",
]


class _EvolutionAuthoringResultContent(_FrozenModel):
    schema_version: Literal[1] = 1
    task_id: str
    status: AuthoringStatus
    baseline_commit: str = Field(pattern=_COMMIT_PATTERN)
    candidate_commit: str | None = Field(default=None, pattern=_COMMIT_PATTERN)
    candidate_ref: str | None = None
    changed_files: tuple[str, ...] = ()
    environment_manifest_id: str | None = None
    environment_identity_id: str | None = None
    environment_dependency_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    command_evidence: tuple[AuthoringCommandEvidence, ...] = ()
    agent_summary: str = ""
    error_code: str | None = None
    error_reason: str | None = None
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def _require_aware_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authoring timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _validate_outcome(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        success = self.status == "candidate_created"
        if success and (
            not self.candidate_commit
            or not self.candidate_ref
            or not self.changed_files
            or not self.environment_manifest_id
            or not self.environment_identity_id
        ):
            raise ValueError(
                "successful authoring requires candidate and environment evidence"
            )
        if success and self.candidate_ref != f"refs/voidcube/candidates/{self.task_id}":
            raise ValueError("successful authoring requires the canonical candidate ref")
        if not success and (self.candidate_commit or self.candidate_ref):
            raise ValueError("failed authoring cannot publish a candidate")
        if success and (self.error_code or self.error_reason):
            raise ValueError("successful authoring cannot carry an error")
        if not success and (not self.error_code or not self.error_reason):
            raise ValueError("failed authoring requires a structured error")
        return self


class EvolutionAuthoringResult(_EvolutionAuthoringResultContent):
    authoring_result_id: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, **values: object) -> Self:
        content = _EvolutionAuthoringResultContent.model_validate(values)
        payload = content.model_dump(mode="json")
        digest = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "authoring_result_id": f"evolution-authoring-result-{digest}",
                "content_hash": digest,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _EvolutionAuthoringResultContent.model_validate(
            self.model_dump(exclude={"authoring_result_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        payload = self.content_payload()
        digest = _content_hash(payload)
        if digest != self.content_hash:
            legacy_payload = dict(payload)
            if self.environment_dependency_fingerprint is None:
                legacy_payload.pop("environment_dependency_fingerprint", None)
            for command in legacy_payload.get("command_evidence", []):
                if not isinstance(command, dict):
                    continue
                if command.get("security_scanner_status") is None:
                    command.pop("security_scanner_status", None)
                if command.get("container_disk_quota_status") is None:
                    command.pop("container_disk_quota_status", None)
            digest = _content_hash(legacy_payload)
        if self.content_hash != digest:
            raise ValueError("content_hash does not match authoring result")
        if self.authoring_result_id != f"evolution-authoring-result-{digest}":
            raise ValueError("authoring_result_id does not match content_hash")
        return self


def candidate_ref_for_task(task_id: str) -> str:
    if (
        re.fullmatch(_TASK_ID_PATTERN, task_id) is None
        or ".." in task_id
        or task_id.endswith((".", ".lock"))
    ):
        raise ValueError("invalid authoring task id")
    return f"refs/voidcube/candidates/{task_id}"


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "AuthoringAgentReport",
    "AuthoringCommandEvidence",
    "EvolutionAuthoringContext",
    "EvolutionAuthoringResult",
    "EvolutionAuthoringSpec",
    "candidate_ref_for_task",
]
