from __future__ import annotations

import pytest
from pydantic import ValidationError

from voidcube.systems.evolution_evaluation import (
    EnvironmentCapabilityPolicy,
    resolve_environment_capability_policy,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.parametrize("profile", ["development", "ci", "production"])
def test_capability_policy_is_content_addressed_and_immutable(profile: str):
    first = EnvironmentCapabilityPolicy.for_profile(profile)
    second = EnvironmentCapabilityPolicy.for_profile(profile)

    assert first == second
    assert first.capability_policy_id == second.capability_policy_id
    assert first.capability_policy_id.endswith(first.content_hash)
    with pytest.raises(ValidationError):
        first.profile = "development"  # type: ignore[misc]


def test_development_warns_without_blocking_non_ideal_capabilities():
    policy = EnvironmentCapabilityPolicy.for_profile("development")

    evaluation = policy.evaluate(
        phase="authoring",
        security_scanner_statuses=("unavailable",),
        container_disk_quota_statuses=("unsupported",),
    )

    assert evaluation.warnings == (
        "security_scanner_unavailable",
        "container_disk_quota_unsupported",
    )
    assert evaluation.violations == ()


@pytest.mark.parametrize("status", ["disabled", "unavailable", "timeout", "error"])
@pytest.mark.parametrize("profile", ["ci", "production"])
def test_ci_and_production_require_available_scanner(profile: str, status: str):
    policy = EnvironmentCapabilityPolicy.for_profile(profile)

    evaluation = policy.evaluate(
        phase="validation",
        security_scanner_statuses=(status,),
        container_disk_quota_statuses=("not_applicable",),
    )

    assert [item.model_dump() for item in evaluation.violations] == [
        {
            "phase": "validation",
            "capability": "security_scanner",
            "status": status,
            "reason_code": "security_scanner_status_not_allowed",
        }
    ]


@pytest.mark.parametrize("status", ["unsupported", "not_requested"])
def test_production_requires_enforced_or_not_applicable_disk_quota(status: str):
    policy = EnvironmentCapabilityPolicy.for_profile("production")

    evaluation = policy.evaluate(
        phase="authoring",
        security_scanner_statuses=("available",),
        container_disk_quota_statuses=(status,),
    )

    assert evaluation.violations[0].capability == "container_disk_quota"
    assert evaluation.violations[0].status == status


def test_ci_keeps_disk_quota_as_warning_and_windows_not_applicable_is_production_safe():
    ci = EnvironmentCapabilityPolicy.for_profile("ci").evaluate(
        phase="validation",
        security_scanner_statuses=("available",),
        container_disk_quota_statuses=("unsupported",),
    )
    production = EnvironmentCapabilityPolicy.for_profile("production").evaluate(
        phase="validation",
        security_scanner_statuses=("available",),
        container_disk_quota_statuses=("not_applicable",),
    )

    assert ci.warnings == ("container_disk_quota_unsupported",)
    assert ci.violations == ()
    assert production.warnings == ()
    assert production.violations == ()


def test_unknown_capability_policy_profile_is_rejected():
    with pytest.raises(ValueError, match="development, ci, or production"):
        EnvironmentCapabilityPolicy.for_profile("staging")


def test_policy_profile_cannot_be_rebound_to_weaker_rules():
    development = EnvironmentCapabilityPolicy.for_profile("development")
    payload = development.model_dump(mode="json")
    payload["profile"] = "production"

    with pytest.raises(ValidationError, match="do not match policy profile"):
        EnvironmentCapabilityPolicy.model_validate(payload)


def test_explicit_policy_and_profile_must_match():
    policy = EnvironmentCapabilityPolicy.for_profile("development")

    with pytest.raises(ValueError, match="policy and profile must match"):
        resolve_environment_capability_policy(
            policy=policy,
            profile="production",
        )
