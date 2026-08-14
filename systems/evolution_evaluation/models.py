"""Immutable contracts for reproducible evolution experiments."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = 1
SCORING_POLICY_SCHEMA_VERSION = 2
EXPERIMENT_RESULT_SCHEMA_VERSION = 2
EXECUTION_ENVIRONMENT_IDENTITY_SCHEMA_VERSION = 1
SUBJECT_CHECKOUT_EVIDENCE_SCHEMA_VERSION = 1
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GIT_COMMIT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_IMAGE_DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class RuntimeToolIdentity(_FrozenModel):
    scope: Literal["host", "execution"]
    name: Literal["git", "python", "pytest", "node", "npm"]
    available: bool
    executable: str = ""
    version: str = ""

    @model_validator(mode="after")
    def _validate_availability(self) -> Self:
        if self.available and (not self.executable or not self.version):
            raise ValueError("available runtime tools require executable and version")
        if not self.available and (self.executable or self.version):
            raise ValueError("unavailable runtime tools cannot declare executable or version")
        return self


class WorkspacePathMapping(_FrozenModel):
    host_path: str = Field(min_length=1)
    execution_path: str = Field(min_length=1)


class _ExecutionEnvironmentManifestContent(_FrozenModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    backend: str = Field(min_length=1)
    validation_scope: Literal["host", "container", "remote"]
    host_os: str = Field(min_length=1)
    execution_os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    host_workspace_path: str = Field(min_length=1)
    execution_workspace_path: str = Field(min_length=1)
    path_mappings: tuple[WorkspacePathMapping, ...] = Field(min_length=1)
    tools: tuple[RuntimeToolIdentity, ...] = Field(min_length=1)
    repository_head: str = Field(pattern=_GIT_COMMIT_PATTERN)
    dependency_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    validated_platforms: tuple[str, ...] = Field(min_length=1)
    image_reference: str | None = None
    image_digest: str | None = Field(default=None, pattern=_IMAGE_DIGEST_PATTERN)

    @model_validator(mode="after")
    def _validate_environment(self) -> Self:
        tool_keys = ((item.scope, item.name) for item in self.tools)
        _require_unique("runtime tool", (f"{scope}:{name}" for scope, name in tool_keys))
        _require_unique("validated platform", self.validated_platforms)
        execution_platform = _platform_key(self.execution_os)
        if self.validated_platforms != (execution_platform,):
            raise ValueError(
                "validated_platforms must match the execution operating system"
            )
        if not any(
            item.host_path == self.host_workspace_path
            and item.execution_path == self.execution_workspace_path
            for item in self.path_mappings
        ):
            raise ValueError("workspace paths require an explicit host-to-execution mapping")
        if self.image_digest and not self.image_reference:
            raise ValueError("image digest requires an image reference")
        return self


class ExecutionEnvironmentManifest(_ExecutionEnvironmentManifestContent):
    execution_environment_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_ExecutionEnvironmentManifestContent,
            id_field="execution_environment_id",
            id_prefix="execution-environment-",
            values=values,
        )

    def content_payload(self) -> dict[str, object]:
        return _content_payload(
            self,
            _ExecutionEnvironmentManifestContent,
            "execution_environment_id",
        )

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address_with_legacy_optional_fields(
            self,
            "execution_environment_id",
            "execution-environment-",
            ("image_reference", "image_digest"),
        )
        return self

    def identity(self) -> ExecutionEnvironmentIdentity:
        """Return the environment identity without the subject checkout HEAD."""
        return ExecutionEnvironmentIdentity.from_manifest(self)


class _ExecutionEnvironmentIdentityContent(_FrozenModel):
    schema_version: Literal[1] = EXECUTION_ENVIRONMENT_IDENTITY_SCHEMA_VERSION
    backend: str = Field(min_length=1)
    validation_scope: Literal["host", "container", "remote"]
    host_os: str = Field(min_length=1)
    execution_os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    host_workspace_path: str = Field(min_length=1)
    execution_workspace_path: str = Field(min_length=1)
    path_mappings: tuple[WorkspacePathMapping, ...] = Field(min_length=1)
    tools: tuple[RuntimeToolIdentity, ...] = Field(min_length=1)
    dependency_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    validated_platforms: tuple[str, ...] = Field(min_length=1)
    image_reference: str | None = None
    image_digest: str | None = Field(default=None, pattern=_IMAGE_DIGEST_PATTERN)

    @model_validator(mode="after")
    def _validate_identity(self) -> Self:
        tool_keys = ((item.scope, item.name) for item in self.tools)
        _require_unique("runtime tool", (f"{scope}:{name}" for scope, name in tool_keys))
        _require_unique("validated platform", self.validated_platforms)
        execution_platform = _platform_key(self.execution_os)
        if self.validated_platforms != (execution_platform,):
            raise ValueError(
                "validated_platforms must match the execution operating system"
            )
        if not any(
            item.host_path == self.host_workspace_path
            and item.execution_path == self.execution_workspace_path
            for item in self.path_mappings
        ):
            raise ValueError("workspace paths require an explicit host-to-execution mapping")
        if self.image_digest and not self.image_reference:
            raise ValueError("image digest requires an image reference")
        return self


class ExecutionEnvironmentIdentity(_ExecutionEnvironmentIdentityContent):
    execution_environment_identity_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_ExecutionEnvironmentIdentityContent,
            id_field="execution_environment_identity_id",
            id_prefix="execution-environment-identity-",
            values=values,
        )

    @classmethod
    def from_manifest(cls, manifest: ExecutionEnvironmentManifest) -> Self:
        payload = manifest.model_dump(mode="python")
        payload.pop("repository_head", None)
        payload.pop("execution_environment_id", None)
        payload.pop("content_hash", None)
        return cls.create(**payload)

    def content_payload(self) -> dict[str, object]:
        return _content_payload(
            self,
            _ExecutionEnvironmentIdentityContent,
            "execution_environment_identity_id",
        )

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address_with_legacy_optional_fields(
            self,
            "execution_environment_identity_id",
            "execution-environment-identity-",
            ("image_reference", "image_digest"),
        )
        return self


class _SubjectCheckoutEvidenceContent(_FrozenModel):
    schema_version: Literal[1] = SUBJECT_CHECKOUT_EVIDENCE_SCHEMA_VERSION
    subject: Literal["baseline", "candidate"]
    commit: str = Field(pattern=_GIT_COMMIT_PATTERN)
    worktree_path: str = Field(min_length=1)
    execution_environment_identity_id: str = Field(
        pattern=r"^execution-environment-identity-[0-9a-f]{64}$"
    )
    checked_out_at: datetime

    @field_validator("checked_out_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        return _aware_datetime("checked_out_at", value)


class SubjectCheckoutEvidence(_SubjectCheckoutEvidenceContent):
    subject_checkout_evidence_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_SubjectCheckoutEvidenceContent,
            id_field="subject_checkout_evidence_id",
            id_prefix="subject-checkout-",
            values=values,
        )

    def content_payload(self) -> dict[str, object]:
        return _content_payload(
            self,
            _SubjectCheckoutEvidenceContent,
            "subject_checkout_evidence_id",
        )

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address(self, "subject_checkout_evidence_id", "subject-checkout-")
        return self


class BenchmarkCase(_FrozenModel):
    case_id: str = Field(min_length=1)
    runner: str = Field(min_length=1)
    input_ref: str = Field(min_length=1)
    expected_ref: str | None = None
    tags: tuple[str, ...] = ()


class _BenchmarkPackContent(_FrozenModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    name: str = Field(min_length=1)
    pack_version: str = Field(min_length=1)
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        return _aware_datetime("created_at", value)

    @model_validator(mode="after")
    def _validate_cases(self) -> Self:
        _require_unique("case_id", (item.case_id for item in self.cases))
        return self


class BenchmarkPack(_BenchmarkPackContent):
    benchmark_pack_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_BenchmarkPackContent,
            id_field="benchmark_pack_id",
            id_prefix="benchmark-pack-",
            values=values,
        )

    def content_payload(self) -> dict[str, object]:
        return _content_payload(self, _BenchmarkPackContent, "benchmark_pack_id")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address(self, "benchmark_pack_id", "benchmark-pack-")
        return self


class ScoringDimension(_FrozenModel):
    name: str = Field(min_length=1)
    weight: float = Field(gt=0.0, le=1.0)


class _ScoringPolicyContent(_FrozenModel):
    schema_version: Literal[2] = SCORING_POLICY_SCHEMA_VERSION
    policy_version: str = Field(min_length=1)
    dimensions: tuple[ScoringDimension, ...] = Field(min_length=1)
    required_hard_gates: tuple[str, ...] = Field(min_length=1)
    required_validation_platforms: tuple[str, ...] = Field(min_length=1)
    promote_threshold: float = Field(ge=0.0, le=1.0)
    observe_threshold: float = Field(ge=0.0, le=1.0)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        return _aware_datetime("created_at", value)

    @model_validator(mode="after")
    def _validate_policy(self) -> Self:
        _require_unique("dimension", (item.name for item in self.dimensions))
        _require_unique("hard gate", self.required_hard_gates)
        _require_unique("validation platform", self.required_validation_platforms)
        if any(
            platform_name != _platform_key(platform_name)
            for platform_name in self.required_validation_platforms
        ):
            raise ValueError("required validation platforms must use canonical names")
        if abs(sum(item.weight for item in self.dimensions) - 1.0) > 1e-9:
            raise ValueError("scoring dimension weights must sum to 1.0")
        if self.observe_threshold > self.promote_threshold:
            raise ValueError("observe_threshold cannot exceed promote_threshold")
        return self


class ScoringPolicy(_ScoringPolicyContent):
    scoring_policy_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_ScoringPolicyContent,
            id_field="scoring_policy_id",
            id_prefix="scoring-policy-",
            values=values,
        )

    def content_payload(self) -> dict[str, object]:
        return _content_payload(self, _ScoringPolicyContent, "scoring_policy_id")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address(self, "scoring_policy_id", "scoring-policy-")
        return self


class MetricTarget(_FrozenModel):
    metric: str = Field(min_length=1)
    objective: str = Field(pattern=r"^(increase|decrease|maintain)$")
    target_value: float | None = None


class AllowedRegression(_FrozenModel):
    metric: str = Field(min_length=1)
    maximum_delta: float = Field(ge=0.0)


class _ExperimentSpecContent(_FrozenModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    baseline_snapshot_id: str = Field(pattern=r"^self-cognition-[0-9a-f]{64}$")
    candidate_commit: str = Field(min_length=1)
    candidate_snapshot_id: str = Field(pattern=r"^self-cognition-[0-9a-f]{64}$")
    hypothesis: str = Field(min_length=1)
    knowledge_ids: tuple[str, ...] = ()
    target_metrics: tuple[MetricTarget, ...] = Field(min_length=1)
    allowed_regressions: tuple[AllowedRegression, ...] = ()
    benchmark_pack_id: str = Field(pattern=r"^benchmark-pack-[0-9a-f]{64}$")
    scoring_policy_id: str = Field(pattern=r"^scoring-policy-[0-9a-f]{64}$")
    execution_environment_identity_id: str | None = Field(
        default=None,
        pattern=r"^execution-environment-identity-[0-9a-f]{64}$",
    )
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        return _aware_datetime("created_at", value)

    @model_validator(mode="after")
    def _validate_spec(self) -> Self:
        _require_unique("knowledge_id", self.knowledge_ids)
        if any(
            re.fullmatch(r"knowledge-[0-9a-f]{64}", item) is None
            for item in self.knowledge_ids
        ):
            raise ValueError("knowledge_ids must be content-addressed knowledge IDs")
        _require_unique("target metric", (item.metric for item in self.target_metrics))
        _require_unique(
            "allowed regression metric",
            (item.metric for item in self.allowed_regressions),
        )
        return self


class ExperimentSpec(_ExperimentSpecContent):
    experiment_spec_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_ExperimentSpecContent,
            id_field="experiment_spec_id",
            id_prefix="experiment-spec-",
            values=values,
        )

    def content_payload(self) -> dict[str, object]:
        return _content_payload(self, _ExperimentSpecContent, "experiment_spec_id")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address(self, "experiment_spec_id", "experiment-spec-")
        return self


class MetricValue(_FrozenModel):
    metric: str = Field(min_length=1)
    value: float
    unit: str = Field(min_length=1)


class MetricDelta(_FrozenModel):
    metric: str = Field(min_length=1)
    delta: float


class Regression(_FrozenModel):
    metric: str = Field(min_length=1)
    observed_delta: float
    allowed_delta: float = Field(ge=0.0)


class HardGateResult(_FrozenModel):
    gate: str = Field(min_length=1)
    passed: bool
    evidence_refs: tuple[str, ...] = ()


class _ExperimentResultContent(_FrozenModel):
    schema_version: Literal[2] = EXPERIMENT_RESULT_SCHEMA_VERSION
    experiment_spec_id: str = Field(pattern=r"^experiment-spec-[0-9a-f]{64}$")
    baseline_metrics: tuple[MetricValue, ...] = Field(min_length=1)
    candidate_metrics: tuple[MetricValue, ...] = Field(min_length=1)
    metric_deltas: tuple[MetricDelta, ...] = Field(min_length=1)
    regressions: tuple[Regression, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)
    hard_gate_results: tuple[HardGateResult, ...] = Field(min_length=1)
    execution_environment: ExecutionEnvironmentManifest
    verdict: str = Field(pattern=r"^(promote|observe|reject)$")
    completed_at: datetime
    execution_environment_identity: ExecutionEnvironmentIdentity | None = None
    subject_checkouts: tuple[SubjectCheckoutEvidence, ...] = ()

    @field_validator("completed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        return _aware_datetime("completed_at", value)

    @model_validator(mode="after")
    def _validate_result(self) -> Self:
        baseline_names = [item.metric for item in self.baseline_metrics]
        candidate_names = [item.metric for item in self.candidate_metrics]
        delta_names = [item.metric for item in self.metric_deltas]
        _require_unique("baseline metric", baseline_names)
        _require_unique("candidate metric", candidate_names)
        _require_unique("metric delta", delta_names)
        _require_unique("hard gate", (item.gate for item in self.hard_gate_results))
        if set(baseline_names) != set(candidate_names) or set(baseline_names) != set(delta_names):
            raise ValueError("baseline, candidate, and delta metrics must match")
        baseline = {item.metric: item for item in self.baseline_metrics}
        candidate = {item.metric: item for item in self.candidate_metrics}
        deltas = {item.metric: item.delta for item in self.metric_deltas}
        for metric in baseline_names:
            if baseline[metric].unit != candidate[metric].unit:
                raise ValueError("baseline and candidate metric units must match")
            expected_delta = candidate[metric].value - baseline[metric].value
            if abs(deltas[metric] - expected_delta) > 1e-9:
                raise ValueError("metric delta does not match candidate minus baseline")
        if self.verdict == "promote" and not all(
            item.passed for item in self.hard_gate_results
        ):
            raise ValueError("promote verdict requires every hard gate to pass")
        if self.execution_environment_identity is None and self.subject_checkouts:
            raise ValueError(
                "subject checkout evidence requires an execution environment identity"
            )
        if self.execution_environment_identity is not None:
            if (
                self.execution_environment.identity().execution_environment_identity_id
                != self.execution_environment_identity.execution_environment_identity_id
            ):
                raise ValueError(
                    "execution environment identity does not match the manifest"
                )
            _require_unique("subject checkout", (item.subject for item in self.subject_checkouts))
        return self


class ExperimentResult(_ExperimentResultContent):
    experiment_result_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def create(cls, **values: object) -> Self:
        return _create_content_addressed(
            cls=cls,
            content_cls=_ExperimentResultContent,
            id_field="experiment_result_id",
            id_prefix="experiment-result-",
            values=values,
        )

    def content_payload(self) -> dict[str, object]:
        return _content_payload(self, _ExperimentResultContent, "experiment_result_id")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        _validate_address(self, "experiment_result_id", "experiment-result-")
        return self


def _create_content_addressed(
    *,
    cls: type[BaseModel],
    content_cls: type[BaseModel],
    id_field: str,
    id_prefix: str,
    values: dict[str, object],
) -> object:
    content = content_cls.model_validate(values)
    payload = content.model_dump(mode="json")
    content_hash = _content_hash(payload)
    return cls.model_validate(
        {
            **payload,
            id_field: f"{id_prefix}{content_hash}",
            "content_hash": content_hash,
        }
    )


def _content_payload(
    model: BaseModel,
    content_cls: type[BaseModel],
    id_field: str,
) -> dict[str, object]:
    return content_cls.model_validate(
        model.model_dump(exclude={id_field, "content_hash"})
    ).model_dump(mode="json")


def _validate_address(model: BaseModel, id_field: str, id_prefix: str) -> None:
    payload = model.content_payload()  # type: ignore[attr-defined]
    expected_hash = _content_hash(payload)
    if model.content_hash != expected_hash:  # type: ignore[attr-defined]
        raise ValueError("content_hash does not match record content")
    if getattr(model, id_field) != f"{id_prefix}{expected_hash}":
        raise ValueError(f"{id_field} does not match content_hash")


def _validate_address_with_legacy_optional_fields(
    model: BaseModel,
    id_field: str,
    id_prefix: str,
    optional_fields: tuple[str, ...],
) -> None:
    try:
        _validate_address(model, id_field, id_prefix)
        return
    except ValueError:
        if any(getattr(model, field) is not None for field in optional_fields):
            raise

    payload = model.content_payload()  # type: ignore[attr-defined]
    for field in optional_fields:
        payload.pop(field, None)
    expected_hash = _content_hash(payload)
    if model.content_hash != expected_hash:  # type: ignore[attr-defined]
        raise ValueError("content_hash does not match record content")
    if getattr(model, id_field) != f"{id_prefix}{expected_hash}":
        raise ValueError(f"{id_field} does not match content_hash")


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _aware_datetime(label: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value


def _require_unique(label: str, values: object) -> None:
    items = [str(item) for item in values]
    if len(items) != len(set(items)):
        raise ValueError(f"{label} values must be unique")


def _platform_key(value: str) -> str:
    normalized = str(value or "unknown").strip().lower()
    if normalized.startswith("win"):
        return "windows"
    if normalized.startswith("linux"):
        return "linux"
    if normalized.startswith(("darwin", "macos", "mac ")):
        return "macos"
    return normalized.split(maxsplit=1)[0] or "unknown"
