from datetime import datetime, timezone

from systems.supervisor.autonomous_chain_store import AutonomousChainTask
from systems.supervisor.autonomous_task_review import (
    build_autonomous_chain_auto_decision,
    calculate_learning_quality_score,
    normalize_autonomous_chain_decision,
)
from systems.supervisor.task_profile_policy import TaskProfilePolicy


def _task(**kwargs) -> AutonomousChainTask:
    return AutonomousChainTask(title=kwargs.pop("title", "task"), **kwargs)


def _drive_input(*, eligible: bool) -> dict:
    return {
        "decisions": {"eligible_for_execution": eligible},
        "task_family_decisions": {},
        "governance_task_type_decisions": {},
    }


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
    body = _task(
        task_family="body_upgrade",
        execution_kind="body_improvement",
    )

    status, _ = build_autonomous_chain_auto_decision(
        task=body,
        drive_input=_drive_input(eligible=True),
        autonomous_chain_gate_active=False,
        task_profile_policy=policy,
        active_tasks=[body],
        learning_history=[completed_learning],
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
        body_improvement_min_quality=60.0,
    )

    assert status == "approved"
