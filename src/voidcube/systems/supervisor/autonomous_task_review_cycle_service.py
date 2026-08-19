"""Runtime orchestration for one autonomous-chain review cycle."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from .autonomous_chain_store import AutonomousChainTask


logger = logging.getLogger("supervisor")

ListExecutionLaneTasks = Callable[[Optional[str]], Iterable[AutonomousChainTask]]
ReviewTasks = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
ConsumeEvents = Callable[[], Dict[str, Any]]
DispatchEmployee = Callable[[AutonomousChainTask], Dict[str, Any]]
ReconcileEmployees = Callable[[], Awaitable[list[Dict[str, Any]]]]


class AutonomousTaskReviewCycleService:
    """Coordinate API-B review, employee dispatch, and employee writeback."""

    def __init__(
        self,
        *,
        list_execution_lane_tasks: ListExecutionLaneTasks,
        get_task: Callable[[str], Optional[AutonomousChainTask]],
        review_tasks: ReviewTasks,
        consume_governance_events: ConsumeEvents,
        consume_alignment_events: ConsumeEvents,
        consume_truthfulness_alerts: ConsumeEvents,
        dispatch_employee: DispatchEmployee,
        reconcile_employees: ReconcileEmployees,
        dispatch_limit: Callable[[], Any],
    ) -> None:
        self._list_execution_lane_tasks = list_execution_lane_tasks
        self._get_task = get_task
        self._review_tasks = review_tasks
        self._consume_governance_events = consume_governance_events
        self._consume_alignment_events = consume_alignment_events
        self._consume_truthfulness_alerts = consume_truthfulness_alerts
        self._dispatch_employee = dispatch_employee
        self._reconcile_employees = reconcile_employees
        self._dispatch_limit = dispatch_limit
        self._cycle_lock = asyncio.Lock()

    async def run(self, request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._cycle_lock.locked():
            logger.info(
                "Skipping autonomous-chain review cycle because another cycle is already running."
            )
            return self._skipped_result()
        async with self._cycle_lock:
            return await self._run_locked(dict(request or {}))

    async def _run_locked(self, request: Dict[str, Any]) -> Dict[str, Any]:
        employee_updates = await self._reconcile_employees()
        governance_consumption = self._consume_governance_events()
        alignment_consumption = self._consume_alignment_events()
        truthfulness_consumption = self._consume_truthfulness_alerts()
        review_result = await self._review_tasks(request)
        dispatched, dispatch_budget_exhausted = self._dispatch_reviewed_tasks(
            review_result
        )
        return {
            "reviewed": review_result.get("count", 0),
            "dispatched": dispatched,
            "employee_updates": employee_updates,
            "governance_consumption": governance_consumption,
            "alignment_consumption": alignment_consumption,
            "truthfulness_consumption": truthfulness_consumption,
            "dispatch_limit": self._normalized_dispatch_limit(),
            "dispatch_budget_exhausted": dispatch_budget_exhausted,
        }

    def _dispatch_reviewed_tasks(
        self,
        review_result: Dict[str, Any],
    ) -> tuple[list[Dict[str, Any]], int]:
        dispatched: list[Dict[str, Any]] = []
        considered_ids: set[str] = set()
        budget_exhausted = 0
        for task_payload in review_result.get("tasks", []):
            if task_payload.get("status") != "approved":
                continue
            task = self._get_task(str(task_payload.get("task_id") or ""))
            if task is None:
                continue
            considered_ids.add(task.task_id)
            if not self._dispatch_budget_available(dispatched):
                budget_exhausted += 1
                continue
            dispatched.append(
                {"task_id": task.task_id, **self._dispatch_employee(task)}
            )

        dispatched_ids = {item["task_id"] for item in dispatched}
        for task in self._list_execution_lane_tasks("approved"):
            if task.task_id in dispatched_ids or task.task_id in considered_ids:
                continue
            if not self._dispatch_budget_available(dispatched):
                budget_exhausted += 1
                continue
            dispatched.append(
                {"task_id": task.task_id, **self._dispatch_employee(task)}
            )
        return dispatched, budget_exhausted

    def _normalized_dispatch_limit(self) -> int:
        return int(self._dispatch_limit() or 0)

    def _dispatch_budget_available(self, dispatched: list[Dict[str, Any]]) -> bool:
        limit = self._normalized_dispatch_limit()
        return limit <= 0 or len(dispatched) < limit

    @staticmethod
    def _skipped_result() -> Dict[str, Any]:
        return {
            "reviewed": 0,
            "dispatched": [],
            "employee_updates": [],
            "governance_consumption": {"count": 0, "consumed": []},
            "alignment_consumption": {"count": 0, "consumed": []},
            "truthfulness_consumption": {"count": 0, "consumed": []},
            "skipped": "cycle_already_running",
        }


__all__ = ["AutonomousTaskReviewCycleService"]
