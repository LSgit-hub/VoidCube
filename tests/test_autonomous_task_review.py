from datetime import datetime, timezone

from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainTask
from voidcube.systems.supervisor.autonomous_task_review import (
    build_autonomous_chain_auto_decision,
    calculate_learning_quality_score,
    normalize_autonomous_chain_decision,
)
from voidcube.systems.supervisor.task_profile_policy import TaskProfilePolicy


def _task(**kwargs) -> AutonomousChainTask:
    return AutonomousChainTask(title=kwargs.pop("title", "task"), **kwargs)


def _drive_input(*, eligible: bool) -> dict:
    return {
        "decisions": {"eligible_for_execution": eligible},
        "task_family_decisions": {},
        "governance_task_type_decisions": {},
    }


def _authorization() -> dict:
    return {
        "schema_version": 2,
        "authorized": True,
        "reason": "promote_result_verified",
        "experiment_result_id": "experiment-result-" + "1" * 64,
        "experiment_spec_id": "experiment-spec-" + "2" * 64,
        "authoring_result_id": "evolution-authoring-result-" + "9" * 64,
        "evaluated_baseline_commit": "b" * 40,
        "evaluated_candidate_commit": "a" * 40,
        "candidate_ref": "refs/voidcube/candidates/review-test",
        "changed_files": ["agent/demo.py"],
        "baseline_snapshot_id": "self-cognition-" + "3" * 64,
        "candidate_snapshot_id": "self-cognition-" + "4" * 64,
        "benchmark_pack_id": "benchmark-pack-" + "5" * 64,
        "scoring_policy_id": "scoring-policy-" + "6" * 64,
        "execution_environment_id": "execution-environment-" + "8" * 64,
        "execution_environment_ids": [
            "execution-environment-" + "8" * 64,
            "execution-environment-" + "9" * 64,
        ],
        "execution_environment_identity_ids": [
            "execution-environment-identity-" + "8" * 64,
        ],
        "authoring_environment_manifest_id": "execution-environment-" + "a" * 64,
        "authoring_environment_identity_id": (
            "execution-environment-identity-" + "b" * 64
        ),
        "authoring_dependency_fingerprint": "c" * 64,
        "authoring_security_scanner_statuses": ["available"],
        "authoring_container_disk_quota_statuses": ["unsupported"],
        "environment_capability_warnings": [
            "container_disk_quota_unsupported"
        ],
        "validation_security_scanner_statuses": ["available"],
        "validation_container_disk_quota_statuses": ["not_applicable"],
        "validation_environment_capability_warnings": [],
        "capability_policy_id": "environment-capability-policy-" + "e" * 64,
        "capability_policy_version": "environment-capability-policy-v1",
        "capability_policy_profile": "development",
        "environment_capability_policy_violations": [],
        "platform_selection_id": "benchmark-platform-selection-" + "d" * 64,
        "selected_validation_platforms": ["windows"],
        "platform_selection_reason_codes": ["project_default_windows"],
        "validation_scope": "host",
        "validated_platforms": ["windows"],
        "knowledge_ids": ["knowledge-" + "7" * 64],
    }


def _authorized_body_task(**kwargs) -> AutonomousChainTask:
    authorization = _authorization()
    fields = {
        key: authorization[key]
        for key in (
            "experiment_result_id",
            "experiment_spec_id",
            "authoring_result_id",
            "evaluated_baseline_commit",
            "evaluated_candidate_commit",
            "candidate_ref",
            "changed_files",
            "baseline_snapshot_id",
            "candidate_snapshot_id",
            "benchmark_pack_id",
            "scoring_policy_id",
            "execution_environment_id",
            "execution_environment_ids",
            "execution_environment_identity_ids",
            "authoring_environment_manifest_id",
            "authoring_environment_identity_id",
            "authoring_dependency_fingerprint",
            "authoring_security_scanner_statuses",
            "authoring_container_disk_quota_statuses",
            "environment_capability_warnings",
            "validation_security_scanner_statuses",
            "validation_container_disk_quota_statuses",
            "validation_environment_capability_warnings",
            "capability_policy_id",
            "capability_policy_version",
            "capability_policy_profile",
            "environment_capability_policy_violations",
            "platform_selection_id",
            "selected_validation_platforms",
            "platform_selection_reason_codes",
            "validation_scope",
            "validated_platforms",
            "knowledge_ids",
        )
    }
    evidence = {"learning_quality_score": 88.0, **fields}
    evidence.update(kwargs.pop("evidence", {}))
    constraints = {
        **fields,
        "must_match_evaluated_commit": True,
        "requires_governor_review": True,
        "requires_user_consent": True,
    }
    constraints.update(kwargs.pop("constraints", {}))
    return _task(
        task_family="body_upgrade",
        execution_kind="body_improvement",
        evidence=evidence,
        constraints=constraints,
        **kwargs,
    )


def test_decision_normalization_is_owned_by_the_pure_policy() -> None:
    assert normalize_autonomous_chain_decision("approve") == "approved"
    assert normalize_autonomous_chain_decision("complete") == "completed"
    assert normalize_autonomous_chain_decision("unknown") is None


def test_body_improvement_waits_for_learning_before_quality_gate() -> None:
    policy = TaskProfilePolicy()
    learning = _task(task_family="self_learning", status="planned")
    body = _task(
        task_family="body_upgrade",
        execution_kind="body_improvement",
        evidence={"learning_quality_score": 88.0},
    )

    status, reason = build_autonomous_chain_auto_decision(
        task=body,
        drive_input=_drive_input(eligible=True),
        autonomous_chain_gate_active=False,
        task_profile_policy=policy,
        active_tasks=[learning, body],
        learning_history=[],
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        body_improvement_min_quality=60.0,
    )

    assert status == "deferred"
    assert "self-learning tasks awaiting completion" in reason


def test_body_improvement_quality_gate_is_deterministic_without_supervisor() -> None:
    policy = TaskProfilePolicy()
    body = _task(
        task_family="body_upgrade",
        execution_kind="body_improvement",
        evidence={"learning_quality_score": 40.0},
    )

    status, reason = build_autonomous_chain_auto_decision(
        task=body,
        drive_input=_drive_input(eligible=True),
        autonomous_chain_gate_active=False,
        task_profile_policy=policy,
        active_tasks=[body],
        learning_history=[],
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        body_improvement_min_quality=60.0,
    )

    assert status == "cancelled"
    assert "below required 60.00" in reason


def test_learning_quality_score_uses_explicit_time_input() -> None:
    policy = TaskProfilePolicy()
    task = _task(
        task_family="self_learning",
        status="completed",
        metadata={
            "quality_score": 0.8,
            "completed_at": "2026-08-03T00:00:00+00:00",
        },
    )

    score = calculate_learning_quality_score(
        [task],
        task_profile_policy=policy,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    assert score == 88.0


def test_learning_quality_score_rejects_missing_quality() -> None:
    policy = TaskProfilePolicy()
    task = _task(task_family="self_learning", status="completed")

    assert calculate_learning_quality_score(
        [task],
        task_profile_policy=policy,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    ) == 0.0


def test_body_improvement_fallback_reads_completed_learning_history() -> None:
    policy = TaskProfilePolicy()
    completed_learning = _task(
        task_family="self_learning",
        status="completed",
        metadata={
            "quality_score": 0.8,
            "completed_at": "2026-08-03T00:00:00+00:00",
        },
    )
    body = _authorized_body_task()

    status, _ = build_autonomous_chain_auto_decision(
        task=body,
        drive_input=_drive_input(eligible=True),
        autonomous_chain_gate_active=False,
        task_profile_policy=policy,
        active_tasks=[body],
        learning_history=[completed_learning],
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        body_improvement_min_quality=60.0,
        evaluation_authorization=_authorization(),
    )

    assert status == "approved"


def test_body_improvement_rejects_forged_evaluation_binding() -> None:
    policy = TaskProfilePolicy()
    body = _authorized_body_task(
        evidence={"experiment_result_id": "experiment-result-" + "9" * 64}
    )

    status, reason = build_autonomous_chain_auto_decision(
        task=body,
        drive_input=_drive_input(eligible=True),
        autonomous_chain_gate_active=False,
        task_profile_policy=policy,
        active_tasks=[body],
        learning_history=[],
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        body_improvement_min_quality=60.0,
        evaluation_authorization=_authorization(),
    )

    assert status == "cancelled"
    assert "evaluation_authorization_binding_mismatch" in reason
