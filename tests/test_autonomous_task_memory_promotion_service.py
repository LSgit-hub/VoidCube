from types import SimpleNamespace

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


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_topics"),
    [
        ("self_learning", ["self_learning"]),
        ("endogenous_drive", ["self_learning", "endogenous_drive"]),
    ],
)
async def test_completed_unverified_learning_records_final_response_without_promotion(
    monkeypatch,
    source,
    expected_topics,
):
    captured = []

    class _Session:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, json, headers):
            captured.append((url, json, headers))
            return _Response(200, {"memory": {"memory_id": "learning-memory-1"}})

    monkeypatch.setitem(
        __import__("sys").modules,
        "aiohttp",
        SimpleNamespace(ClientTimeout=lambda **kwargs: kwargs, ClientSession=_Session),
    )

    state = _TaskState()
    service = AutonomousTaskMemoryPromotionService(
        task_state=state,
        gateway_address="http://gateway",
        gateway_memory_headers=lambda *, memory_actor: {
            "X-VoidCube-Memory-Actor": memory_actor
        },
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
    assert captured[0][0] == "http://gateway/api/mem/remember"
    assert captured[0][1]["summary"] == "Primary-source conclusion"
    assert captured[0][1]["topics"] == expected_topics
    assert state.metadata_updates[0][1]["memory_promotion_candidate_status"] == "recorded_only"
