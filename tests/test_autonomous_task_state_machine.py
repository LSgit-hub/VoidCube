import pytest

from voidcube.domain.state.autonomous_task import (
    AutonomousTaskTransition,
    validate_autonomous_task_transition,
    validate_task_outcome_bundle,
)


def test_autonomous_task_state_machine_allows_normal_execution_path():
    for current, target in (
        ("planned", "approved"),
        ("approved", "running"),
        ("running", "awaiting_review"),
    ):
        validate_autonomous_task_transition(current, target)


def test_autonomous_task_state_machine_rejects_terminal_reopen():
    with pytest.raises(ValueError, match="terminal"):
        validate_autonomous_task_transition("completed", "running")


def test_transition_carries_required_cross_system_evidence():
    transition = AutonomousTaskTransition(
        task_id="task-1",
        from_status="running",
        to_status="completed",
        cycle_id="cycle-1",
        lease_id="lease-1",
        attempt=2,
        evidence_refs=("probe-1",),
        memory_write_status="queued",
    )
    assert transition.memory_write_status == "queued"
    assert transition.evidence_refs == ("probe-1",)


def test_completed_task_rejects_failed_execution_outcome():
    with pytest.raises(ValueError, match="successful execution"):
        validate_task_outcome_bundle(
            "completed", execution_outcome_status="failed",
        )


def test_completed_task_allows_async_memory_queue():
    validate_task_outcome_bundle(
        "completed", execution_outcome_status="succeeded",
        memory_write_status="queued",
    )
