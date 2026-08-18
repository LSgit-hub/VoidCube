"""Recovery boundary for autonomous-chain task projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from systems.supervisor.autonomous_chain_store import AutonomousChainStore


LoadGovernanceEvents = Callable[[], list[Any]]
RepositoryPath = Callable[[], Path]
TouchActivity = Callable[..., Awaitable[Any]]


class AutonomousChainRecoveryService:
    """Recover task projections from the canonical Mem governance event stream."""

    def __init__(
        self,
        *,
        store: AutonomousChainStore,
        load_governance_events: LoadGovernanceEvents,
        governance_repository_path: RepositoryPath,
        touch_activity: TouchActivity,
    ) -> None:
        self._store = store
        self._load_governance_events = load_governance_events
        self._governance_repository_path = governance_repository_path
        self._touch_activity = touch_activity

    def recover(self, *, replace: bool = False) -> Dict[str, Any]:
        existing_count = len(self._store.list_tasks())
        result = self._store.recover_from_governance_events(
            self._load_governance_events(),
            replace=replace,
        )
        return {
            **result,
            "existing_task_count": existing_count,
            "mem_governance_path": str(self._governance_repository_path()),
        }

    async def recover_from_mem(
        self,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = self.recover(replace=bool(dict(request or {}).get("replace", False)))
        if result.get("added_task_count") or result.get("updated_task_count"):
            await self._touch_activity(
                "autonomous_chain_plan",
                metadata={
                    "action": "recover_from_mem_governance",
                    "added_task_count": result.get("added_task_count", 0),
                    "updated_task_count": result.get("updated_task_count", 0),
                },
            )
        return result


__all__ = ["AutonomousChainRecoveryService"]
