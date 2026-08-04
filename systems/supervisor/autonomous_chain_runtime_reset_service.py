"""Administrative reset boundary for autonomous-chain runtime state."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from systems.supervisor.autonomous_chain_store import AutonomousChainTask


ListTasks = Callable[[], Iterable[AutonomousChainTask]]
ClearTasks = Callable[[Iterable[AutonomousChainTask]], None]
PersistHistory = Callable[[Dict[str, Any]], None]
ClearGatewayActivity = Callable[[], Awaitable[None]]
ResetSchedule = Callable[[], None]
ResetWatchWindow = Callable[[], None]


class AutonomousChainRuntimeResetService:
    """Clear autonomous-chain projections and their owned runtime projections."""

    def __init__(
        self,
        *,
        list_tasks: ListTasks,
        clear_tasks: ClearTasks,
        clear_ui_activity: Callable[[], None],
        clear_governor_projection: Callable[[], None],
        default_drive_history: Callable[[], Dict[str, Any]],
        persist_drive_history: PersistHistory,
        clear_gateway_activity: ClearGatewayActivity,
        reset_schedule: ResetSchedule,
        reset_watch_window: ResetWatchWindow,
    ) -> None:
        self._list_tasks = list_tasks
        self._clear_tasks = clear_tasks
        self._clear_ui_activity = clear_ui_activity
        self._clear_governor_projection = clear_governor_projection
        self._default_drive_history = default_drive_history
        self._persist_drive_history = persist_drive_history
        self._clear_gateway_activity = clear_gateway_activity
        self._reset_schedule = reset_schedule
        self._reset_watch_window = reset_watch_window

    async def clear(self, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        del request
        tasks = list(self._list_tasks())
        cleared_counts: Dict[str, int] = {}
        for task in tasks:
            status = str(task.status)
            cleared_counts[status] = cleared_counts.get(status, 0) + 1

        self._clear_tasks(tasks)
        self._clear_ui_activity()
        self._clear_governor_projection()
        try:
            self._persist_drive_history(self._default_drive_history())
        except Exception:
            pass

        try:
            await self._clear_gateway_activity()
        except Exception:
            pass

        self._reset_schedule()
        self._reset_watch_window()
        return {
            "status": "cleared",
            "cleared_task_count": len(tasks),
            "cleared_status_counts": cleared_counts,
            "tasks_remaining": 0,
        }


__all__ = ["AutonomousChainRuntimeResetService"]
