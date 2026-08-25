import pytest

from voidcube.systems.supervisor.autonomous_chain_store import (
    AutonomousChainTask,
    AutonomousChainTaskDecision,
)
from voidcube.systems.supervisor.autonomous_task_memory_promotion_service import (
    AutonomousTaskMemoryPromotionService,
)


class _TaskState:
    def __init__(self) -> None:
        self.metadata_updates = []

    def update_metadata(self, task_id, *, metadata):
        self.metadata_updates.append((task_id, dict(metadata)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_topics"),
    [
        ("self_learning", ["self_learning"]),
        ("endogenous_drive", ["self_learning", "endogenous_drive"]),
    ],
)
async def test_completed_unverified_learning_records_final_response_without_promotion(
    source,
    expected_topics,
):
    captured = []

    class _MemoryClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def request_json(self, method, path, payload=None, **kwargs):
            captured.append((self.kwargs, method, path, payload, kwargs))
            return {"memory": {"memory_id": "learning-memory-1"}}

    def memory_client_factory(**kwargs):
        return _MemoryClient(**kwargs)

    state = _TaskState()
    service = AutonomousTaskMemoryPromotionService(
        task_state=state,
        memory_client_factory=memory_client_factory,
    )
    task = AutonomousChainTask(
        title="Research result",
        summary="Fallback summary",
        source=source,
        task_family="self_learning",
        status="completed",
        decision_history=[
            AutonomousChainTaskDecision(
                status="completed",
                task_family="self_learning",
                context={
                    "employee_final_response": "Primary-source conclusion"
                },
            )
        ],
    )

    result = await service.propose(task)

    assert result == {
        "status": "recorded_only",
        "source_memory_id": "learning-memory-1",
    }
    assert len(captured) == 1
    assert captured[0][0] == {
        "memory_actor": "stellar_auto",
        "memory_domain": "evolution",
        "owner_id": "local-user",
        "workspace_id": "VoidCube",
        "timeout_seconds": 8,
    }
    assert captured[0][1:3] == ("POST", "/remember")
    assert captured[0][3]["summary"] == "Primary-source conclusion"
    assert captured[0][3]["topics"] == expected_topics
    assert captured[0][4]["idempotency_key"].startswith("auto-memory:")
    assert state.metadata_updates[0][1]["memory_promotion_candidate_status"] == "recorded_only"
