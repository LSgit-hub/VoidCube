import pytest

from systems.supervisor.autonomous_chain_recovery_service import (
    AutonomousChainRecoveryService,
)
from systems.supervisor.autonomous_chain_store import AutonomousChainStore


class _Activity:
    def __init__(self) -> None:
        self.calls = []

    async def touch(self, kind, *, metadata):
        self.calls.append((kind, metadata))


@pytest.mark.asyncio
async def test_recovery_service_owns_mem_projection_recovery(tmp_path) -> None:
    store = AutonomousChainStore(tmp_path / "tasks.json")
    activity = _Activity()
    events = []

    service = AutonomousChainRecoveryService(
        store=store,
        load_governance_events=lambda: events,
        governance_repository_path=lambda: tmp_path / "mem_governance.jsonl",
        touch_activity=activity.touch,
    )

    result = await service.recover_from_mem({"replace": True})

    assert result["existing_task_count"] == 0
    assert result["added_task_count"] == 0
    assert result["mem_governance_path"].endswith("mem_governance.jsonl")
    assert activity.calls == []
