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

    task = service.create_task(title="state owner")
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

    service.clear_tasks([store.get_task(task.task_id)])

    assert repository.events[-1].event_type.value == "autonomous_task_clear"
    assert store.list_tasks() == []
