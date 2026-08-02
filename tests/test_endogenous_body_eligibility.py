from datetime import datetime, timedelta, timezone

from systems.supervisor.endogenous_body_eligibility import (
    calculate_learning_quality_score,
    has_recent_body_improvement,
    resolve_body_improvement_eligibility,
)


def test_learning_quality_score_uses_quality_and_freshness():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)

    assert calculate_learning_quality_score([], now=now) == 0.0
    assert calculate_learning_quality_score(
        [{"quality_score": 1.0, "completed_at": now.isoformat()}],
        now=now,
    ) == 100.0


def test_body_eligibility_rejects_missing_slot_or_weak_learning_evidence():
    assert resolve_body_improvement_eligibility(
        completed_learning_tasks=[],
        shell_slot_id="slot-B",
        shell_worktree="body/slot-B",
        policy={},
        api_b_judgement_tasks=[],
    )["reason"] == "learning_evidence_unavailable"
    assert resolve_body_improvement_eligibility(
        completed_learning_tasks=[{"quality_score": 0.1}],
        shell_slot_id="slot-B",
        shell_worktree="body/slot-B",
        policy={},
        api_b_judgement_tasks=[],
    )["reason"] == "learning_quality_below_threshold"


def test_body_eligibility_rejects_matching_in_flight_improvement():
    result = resolve_body_improvement_eligibility(
        completed_learning_tasks=[{"quality_score": 1.0}],
        shell_slot_id="slot-B",
        shell_worktree="body/slot-B",
        policy={},
        api_b_judgement_tasks=[
            {
                "execution_kind": "body_improvement",
                "status": "running",
                "constraints": {"target_slot_id": "slot-B"},
            }
        ],
    )

    assert result == {
        "available": False,
        "reason": "body_improvement_cooldown",
        "learning_quality_score": 80.0,
    }


def test_completed_body_improvement_reopens_after_cooldown():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    task = {
        "execution_kind": "body_improvement",
        "status": "completed",
        "updated_at": (now - timedelta(hours=13)).isoformat(),
        "constraints": {"target_slot_id": "slot-B"},
    }

    assert has_recent_body_improvement(
        [task], shell_slot_id="slot-B", cooldown_hours=12, now=now
    ) is False
    result = resolve_body_improvement_eligibility(
        completed_learning_tasks=[{"quality_score": 1.0}],
        shell_slot_id="slot-B",
        shell_worktree="body/slot-B",
        policy={"body_improvement_cooldown_hours": 12},
        api_b_judgement_tasks=[task],
        now=now,
    )
    assert result["available"] is True
