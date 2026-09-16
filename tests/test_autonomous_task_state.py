from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainStore
from voidcube.systems.supervisor.autonomous_task_state import AutonomousTaskStateService


class _GovernanceRepository:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


def test_state_service_owns_mutations_and_governance_events(tmp_path) -> None:
    repository = _GovernanceRepository()
    observed = []
    store = AutonomousChainStore(tmp_path / "tasks.json")
    service = AutonomousTaskStateService(
        store=store,
        governance_repository=repository,
        on_status_change=lambda task, event_type: observed.append(
            (task.task_id, event_type, task.status)
        ),
    )

    task = service.create_task(
        title="state owner",
        metadata={
            "cycle_id": "cycle-1",
            "attempt": 2,
            "evidence_refs": ["probe-1"],
            "memory_write_status": "queued",
            "execution_outcome_status": "succeeded",
        },
    )
    assert task.metadata["cycle_id"] == "cycle-1"
    assert task.metadata["attempt"] == 2
    service.update_priority(
        task.task_id,
        priority="high",
        actor="supervisor",
        reason="priority evidence",
    )
    service.update_metadata(task.task_id, metadata={"source": "test"})
    service.update_status(
        task.task_id,
        status="approved",
        actor="supervisor",
        reason="ready",
        event_type="review",
    )

    assert [event.event_type.value for event in repository.events] == [
        "autonomous_task_transition",
        "autonomous_task_transition",
        "autonomous_task_transition",
        "autonomous_task_transition",
    ]
    assert observed == [(task.task_id, "review", "approved")]
    assert store.get_task(task.task_id).metadata["source"] == "test"
    evidence = repository.events[-1].execution_result["transition_evidence"]
    assert evidence["task_id"] == task.task_id
    assert evidence["to_status"] == "approved"
    assert evidence["cycle_id"] == "cycle-1"
    assert evidence["attempt"] == 2
    assert evidence["evidence_refs"] == ["probe-1"]
    assert evidence["memory_write_status"] == "queued"
    assert evidence["execution_outcome_status"] == "succeeded"

    service.clear_tasks([store.get_task(task.task_id)])

    assert repository.events[-1].event_type.value == "autonomous_task_clear"
    assert store.list_tasks() == []


def test_store_finalize_execution_rejects_failed_completed_outcome(tmp_path) -> None:
    store = AutonomousChainStore(tmp_path / "tasks.json")
    task = store.create_task(title="terminal guard")
    store.update_status(task.task_id, status="approved", reason="ready")
    running = store.claim_execution(
        task.task_id, owner_session_id="worker-1", lease_seconds=30,
    )
    assert running.metadata["attempt"] == running.execution_lease.generation
    assert running.metadata["lease_id"] == running.execution_lease.attempt_id
    assert running.metadata["cycle_id"]

    import pytest

    with pytest.raises(ValueError, match="successful execution"):
        store.finalize_execution(
            task.task_id,
            generation=running.execution_lease.generation,
            attempt_id=running.execution_lease.attempt_id,
            status="completed",
            actor="worker-1",
            reason="invalid completion",
            context={"execution_outcome_status": "failed"},
        )
