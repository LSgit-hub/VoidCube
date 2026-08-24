from datetime import datetime, timedelta, timezone

from voidcube.systems.supervisor.endogenous_candidate_eligibility import (
    GOVERNANCE_HYGIENE_STABLE_KEY,
    MEMORY_MAINTENANCE_STABLE_KEY,
    has_recent_static_governance_completion,
    resolve_candidate_stream_eligibility,
)


def _task(candidate_kind: str, *, status: str = "planned") -> dict:
    return {
        "status": status,
        "metadata": {"candidate_kind": candidate_kind},
    }


def _eligibility(**overrides):
    values = {
        "api_b_judgement_tasks": [],
        "existing_keys": [],
        "memory_planning_eligible": True,
        "self_learning_planning_eligible": True,
        "autonomous_improvement_planning_eligible": True,
        "truthfulness_signal_present": True,
        "shell_slot_id": "slot-B",
        "shell_worktree": "body/slot-B",
        "has_learning_history": False,
        "governance_signal_present": True,
        "body_projection_available": True,
        "body_growth_blocked": False,
        "body_growth_quota": 1,
    }
    values.update(overrides)
    return resolve_candidate_stream_eligibility(**values)


def test_candidate_stream_projection_resolves_all_static_gates():
    eligibility = _eligibility()

    assert eligibility.memory_maintenance is True
    assert eligibility.truthfulness_review is True
    assert eligibility.shell_baseline_learning is True
    assert eligibility.exploratory_learning is True
    assert eligibility.governance_hygiene_review is True
    assert eligibility.body_improvement is True


def test_candidate_stream_projection_applies_active_existing_and_quota_gates():
    eligibility = _eligibility(
        api_b_judgement_tasks=[
            _task("memory_maintenance"),
            _task("exploratory_learning"),
            _task("body_improvement"),
        ],
        existing_keys={
            "truthfulness:review_correction_signals",
            "creativity:self_learning:shell_baseline:slot-B",
        },
        body_growth_quota=0,
    )

    assert eligibility.active_candidate_kinds == frozenset(
        {"memory_maintenance", "exploratory_learning", "body_improvement"}
    )
    assert eligibility.memory_maintenance is False
    assert eligibility.truthfulness_review is False
    assert eligibility.shell_baseline_learning is False
    assert eligibility.exploratory_learning is False
    assert eligibility.body_improvement is False


def test_candidate_stream_projection_requires_signal_and_body_readiness():
    eligibility = _eligibility(
        governance_signal_present=False,
        body_projection_available=False,
        body_growth_blocked=True,
    )

    assert eligibility.governance_hygiene_review is False
    assert eligibility.body_improvement is False


def test_static_completion_is_true_only_inside_cooldown():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    recent = {
        "status": "completed",
        "updated_at": (now - timedelta(hours=1)).isoformat(),
        "metadata": {"endogenous_drive_key": MEMORY_MAINTENANCE_STABLE_KEY},
    }
    old = {
        **recent,
        "updated_at": (now - timedelta(hours=13)).isoformat(),
    }

    assert has_recent_static_governance_completion(
        [recent], stable_key=MEMORY_MAINTENANCE_STABLE_KEY, now=now
    ) is True
    assert has_recent_static_governance_completion(
        [old], stable_key=GOVERNANCE_HYGIENE_STABLE_KEY, now=now
    ) is False
    assert has_recent_static_governance_completion(
        [old], stable_key=MEMORY_MAINTENANCE_STABLE_KEY, now=now
    ) is False


def test_memory_candidate_waits_for_active_memory_service_run():
    eligibility = _eligibility(
        api_b_judgement_tasks=[],
        memory_maintenance_status={
            "status": "running",
            "maintenance_due": False,
        },
    )

    assert eligibility.memory_maintenance is False


def test_memory_candidate_follows_memory_service_due_state_over_static_cooldown():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    recent = {
        "status": "completed",
        "updated_at": (now - timedelta(hours=1)).isoformat(),
        "metadata": {"endogenous_drive_key": MEMORY_MAINTENANCE_STABLE_KEY},
    }

    due = _eligibility(
        api_b_judgement_tasks=[recent],
        memory_maintenance_status={
            "status": "completed",
            "maintenance_due": True,
        },
        now=now,
    )
    not_due = _eligibility(
        api_b_judgement_tasks=[recent],
        memory_maintenance_status={
            "status": "completed",
            "maintenance_due": False,
        },
        now=now,
    )

    assert due.memory_maintenance is True
    assert not_due.memory_maintenance is False


def test_failed_memory_service_run_can_be_retried():
    eligibility = _eligibility(
        api_b_judgement_tasks=[
            {
                "status": "completed",
                "metadata": {
                    "endogenous_drive_key": MEMORY_MAINTENANCE_STABLE_KEY,
                },
            }
        ],
        memory_maintenance_status={"status": "failed"},
    )

    assert eligibility.memory_maintenance is True
