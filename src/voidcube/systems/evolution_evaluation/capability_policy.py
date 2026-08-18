"""Versioned deployment policy for evolution environment capabilities."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


CAPABILITY_POLICY_VERSION = "environment-capability-policy-v1"
CapabilityPolicyProfile = Literal["development", "ci", "production"]
CapabilityPhase = Literal["authoring", "validation"]
CapabilityName = Literal["security_scanner", "container_disk_quota"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SCANNER_STATUSES = ("available", "disabled", "unavailable", "timeout", "error")
_DISK_QUOTA_STATUSES = (
    "enforced",
    "unsupported",
    "not_requested",
    "not_applicable",
)
_PROFILE_RULES = {
    "development": (_SCANNER_STATUSES, _DISK_QUOTA_STATUSES),
    "ci": (("available",), _DISK_QUOTA_STATUSES),
    "production": (("available",), ("enforced", "not_applicable")),
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class CapabilityPolicyViolation(_FrozenModel):
    phase: CapabilityPhase
    capability: CapabilityName
    status: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)


class CapabilityPolicyEvaluation(_FrozenModel):
    warnings: tuple[str, ...] = ()
    violations: tuple[CapabilityPolicyViolation, ...] = ()


class _EnvironmentCapabilityPolicyContent(_FrozenModel):
    schema_version: Literal[1] = 1
    policy_version: Literal["environment-capability-policy-v1"] = (
        CAPABILITY_POLICY_VERSION
    )
    profile: CapabilityPolicyProfile
    allowed_security_scanner_statuses: tuple[str, ...] = Field(min_length=1)
    allowed_container_disk_quota_statuses: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_statuses(self) -> Self:
        _require_canonical_statuses(
            "allowed_security_scanner_statuses",
            self.allowed_security_scanner_statuses,
            _SCANNER_STATUSES,
        )
        _require_canonical_statuses(
            "allowed_container_disk_quota_statuses",
            self.allowed_container_disk_quota_statuses,
            _DISK_QUOTA_STATUSES,
        )
        expected_scanner, expected_disk_quota = _PROFILE_RULES[self.profile]
        if self.allowed_security_scanner_statuses != expected_scanner:
            raise ValueError("security scanner statuses do not match policy profile")
        if self.allowed_container_disk_quota_statuses != expected_disk_quota:
            raise ValueError("disk quota statuses do not match policy profile")
        return self


class EnvironmentCapabilityPolicy(_EnvironmentCapabilityPolicyContent):
    """Immutable policy selected once for an evolution runtime."""

    capability_policy_id: str = Field(
        pattern=r"^environment-capability-policy-[0-9a-f]{64}$"
    )
    content_hash: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def for_profile(cls, profile: CapabilityPolicyProfile | str) -> Self:
        normalized = str(profile).strip().lower()
        if normalized not in _PROFILE_RULES:
            raise ValueError(
                "capability policy profile must be development, ci, or production"
            )
        scanner_statuses, disk_quota_statuses = _PROFILE_RULES[normalized]
        content = _EnvironmentCapabilityPolicyContent.model_validate(
            {
                "profile": normalized,
                "allowed_security_scanner_statuses": scanner_statuses,
                "allowed_container_disk_quota_statuses": disk_quota_statuses,
            }
        )
        payload = content.model_dump(mode="json")
        digest = _content_hash(payload)
        return cls.model_validate(
            {
                **payload,
                "capability_policy_id": f"environment-capability-policy-{digest}",
                "content_hash": digest,
            }
        )

    def content_payload(self) -> dict[str, object]:
        return _EnvironmentCapabilityPolicyContent.model_validate(
            self.model_dump(exclude={"capability_policy_id", "content_hash"})
        ).model_dump(mode="json")

    @model_validator(mode="after")
    def _validate_content_address(self) -> Self:
        digest = _content_hash(self.content_payload())
        if self.content_hash != digest:
            raise ValueError("content_hash does not match capability policy")
        if self.capability_policy_id != f"environment-capability-policy-{digest}":
            raise ValueError("capability_policy_id does not match content_hash")
        return self

    def evaluate(
        self,
        *,
        phase: CapabilityPhase,
        security_scanner_statuses: Iterable[str],
        container_disk_quota_statuses: Iterable[str],
    ) -> CapabilityPolicyEvaluation:
        scanner_statuses = _canonical_observed_statuses(
            security_scanner_statuses,
            _SCANNER_STATUSES,
            "security scanner",
        )
        disk_quota_statuses = _canonical_observed_statuses(
            container_disk_quota_statuses,
            _DISK_QUOTA_STATUSES,
            "container disk quota",
        )
        warnings = tuple(
            [
                f"security_scanner_{status}"
                for status in scanner_statuses
                if status != "available"
            ]
            + [
                f"container_disk_quota_{status}"
                for status in disk_quota_statuses
                if status not in {"enforced", "not_applicable"}
            ]
        )
        violations = tuple(
            [
                CapabilityPolicyViolation(
                    phase=phase,
                    capability="security_scanner",
                    status=status,
                    reason_code="security_scanner_status_not_allowed",
                )
                for status in scanner_statuses
                if status not in self.allowed_security_scanner_statuses
            ]
            + [
                CapabilityPolicyViolation(
                    phase=phase,
                    capability="container_disk_quota",
                    status=status,
                    reason_code="container_disk_quota_status_not_allowed",
                )
                for status in disk_quota_statuses
                if status not in self.allowed_container_disk_quota_statuses
            ]
        )
        return CapabilityPolicyEvaluation(warnings=warnings, violations=violations)


def resolve_environment_capability_policy(
    *,
    policy: EnvironmentCapabilityPolicy | None = None,
    profile: CapabilityPolicyProfile | str | None = None,
) -> EnvironmentCapabilityPolicy:
    """Resolve one policy and reject conflicting construction inputs."""

    if policy is None:
        return EnvironmentCapabilityPolicy.for_profile(profile or "development")
    if profile is not None:
        normalized = str(profile).strip().lower()
        if normalized != policy.profile:
            raise ValueError("capability policy and profile must match")
    return policy


def _require_canonical_statuses(
    label: str,
    values: tuple[str, ...],
    canonical: tuple[str, ...],
) -> None:
    expected = tuple(item for item in canonical if item in set(values))
    if values != expected:
        raise ValueError(f"{label} must be unique, known, and canonically ordered")


def _canonical_observed_statuses(
    values: Iterable[str],
    canonical: tuple[str, ...],
    label: str,
) -> tuple[str, ...]:
    observed = {str(item).strip() for item in values}
    if not observed or "" in observed or not observed.issubset(set(canonical)):
        raise ValueError(f"{label} statuses must be non-empty and known")
    return tuple(item for item in canonical if item in observed)


def _content_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "CAPABILITY_POLICY_VERSION",
    "CapabilityPolicyEvaluation",
    "CapabilityPolicyProfile",
    "CapabilityPolicyViolation",
    "EnvironmentCapabilityPolicy",
    "resolve_environment_capability_policy",
]
