"""Runtime orchestration for one autonomous-chain review cycle."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional

from .autonomous_chain_store import (
    AutonomousChainTask,
    StaleExecutionLeaseError,
)
from .autonomous_task_review import is_agent_pull_task
from .autonomous_task_state import AutonomousTaskStateService
from .task_profile_policy import TaskProfilePolicy


logger = logging.getLogger("supervisor")

ListExecutionLaneTasks = Callable[[Optional[str]], Iterable[AutonomousChainTask]]
ReviewTasks = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]
ConsumeEvents = Callable[[], Dict[str, Any]]
FetchCliSession = Callable[[str], Awaitable[Dict[str, Any]]]
HandoffExecution = Callable[[AutonomousChainTask], Awaitable[Optional[Dict[str, Any]]]]
Now = Callable[[], datetime]


class AutonomousTaskReviewCycleService:
    """Coordinate recovery, review, and execution handoff without Supervisor state."""

    _STALE_RUNNING_SECONDS = 1800

    def __init__(
        self,
        *,
        task_profile_policy: TaskProfilePolicy,
        task_state: AutonomousTaskStateService,
        list_execution_lane_tasks: ListExecutionLaneTasks,
        get_task: Callable[[str], Optional[AutonomousChainTask]],
        fetch_cli_session: FetchCliSession,
        review_tasks: ReviewTasks,
        consume_governance_events: ConsumeEvents,
        consume_alignment_events: ConsumeEvents,
        consume_truthfulness_alerts: ConsumeEvents,
        handoff_execution: HandoffExecution,
        handoff_limit: Callable[[], Any],
        now: Optional[Now] = None,
    ) -> None:
        self._task_profile_policy = task_profile_policy
        self._task_state = task_state
        self._list_execution_lane_tasks = list_execution_lane_tasks
        self._get_task = get_task
        self._fetch_cli_session = fetch_cli_session
        self._review_tasks = review_tasks
        self._consume_governance_events = consume_governance_events
        self._consume_alignment_events = consume_alignment_events
        self._consume_truthfulness_alerts = consume_truthfulness_alerts
        self._handoff_execution = handoff_execution
        self._handoff_limit = handoff_limit
        self._now = now or (lambda: datetime.now(timezone.utc))
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
        recovered_orphaned = await self.recover_orphaned_agent_pull_tasks()
        governance_consumption = self._consume_governance_events()
        alignment_consumption = self._consume_alignment_events()
        truthfulness_consumption = self._consume_truthfulness_alerts()
        stale_running = self._fail_stale_running_tasks()
        if stale_running:
            logger.warning("Auto-failed %d stale running tasks", stale_running)

        review_result = await self._review_tasks(request)
        handed_off, handoff_budget_exhausted = await self._handoff_reviewed_tasks(
            review_result
        )

        return {
            "reviewed": review_result.get("count", 0),
            "handed_off": handed_off,
            "recovered_orphaned": recovered_orphaned,
            "stale_running": stale_running,
            "governance_consumption": governance_consumption,
            "alignment_consumption": alignment_consumption,
            "truthfulness_consumption": truthfulness_consumption,
            "handoff_limit": self._normalized_handoff_limit(),
            "handoff_budget_exhausted": handoff_budget_exhausted,
        }

    async def recover_orphaned_agent_pull_tasks(self) -> int:
        recovered = 0
        for task in self._list_execution_lane_tasks("running"):
            if not is_agent_pull_task(
                task,
                task_profile_policy=self._task_profile_policy,
            ):
                continue

            metadata = dict(task.metadata or {})
            lease = task.execution_lease
            owner_session_id = str(lease.owner_session_id or "").strip()
            execution_source = str(metadata.get("execution_source") or "").strip().lower()
            if execution_source and execution_source != "cli_agent_pull":
                continue
            if not owner_session_id:
                logger.warning(
                    "跳过运行中 agent-pull 链路项 %s 的孤儿恢复：owner_session_id 缺失，当前无法确认归属。",
                    task.task_id,
                )
                self._task_state.update_metadata(
                    task.task_id,
                    metadata={
                        "owner_session_missing_seen_at": self._now().isoformat(),
                    },
                )
                continue

            expires_at = lease.expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            lease_expired = expires_at is None or expires_at <= self._now()
            owner_session: Dict[str, Any] = {}
            if not lease_expired:
                try:
                    owner_session = await self._fetch_cli_session(owner_session_id)
                except Exception as exc:
                    logger.warning(
                        "跳过链路项 %s 的孤儿恢复：无法从网关确认 owner CLI 会话 %s（%s）；当前保守地不做恢复。",
                        task.task_id,
                        owner_session_id,
                        exc,
                    )
                    continue

            owner_missing = bool(owner_session.get("missing"))
            owner_stale = bool(owner_session.get("is_stale")) or str(
                owner_session.get("lease_status") or ""
            ).strip().lower() == "stale"
            owner_stale = owner_stale or lease_expired
            if not owner_missing and not owner_stale:
                continue

            try:
                self._task_state.begin_reconcile(
                    task.task_id,
                    expected_generation=lease.generation,
                    expected_attempt_id=str(lease.attempt_id or ""),
                    reason=(
                        "Agent-pull owner is missing, stale, or its execution lease expired; "
                        "the outcome must be reconciled before this task can be claimed again."
                    ),
                    context={
                        "recovered": True,
                        "previous_owner_session_id": owner_session_id,
                        "owner_session_missing": owner_missing,
                        "owner_session_stale": owner_stale,
                        "execution_lease_expired": lease_expired,
                        "owner_lease_status": owner_session.get("lease_status"),
                        "active_cli_session_id": owner_session.get("active_cli_session_id"),
                    },
                )
            except StaleExecutionLeaseError:
                continue
            self._task_state.update_metadata(
                task.task_id,
                metadata={
                    "reconcile_started_at": self._now().isoformat(),
                },
            )
            recovered += 1
        return recovered

    def _fail_stale_running_tasks(self) -> int:
        stale_running = 0
        now = self._now()
        for task in self._list_execution_lane_tasks("running"):
            started = task.execution_lease.heartbeat_at or dict(task.metadata or {}).get("executed_at") or dict(
                task.metadata or {}
            ).get("execution_started_at")
            if not started:
                continue
            try:
                started_at = datetime.fromisoformat(str(started))
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                if (now - started_at).total_seconds() > self._STALE_RUNNING_SECONDS:
                    self._task_state.expire_execution(
                        task.task_id,
                        expected_generation=task.execution_lease.generation,
                        expected_attempt_id=str(task.execution_lease.attempt_id or ""),
                        expected_heartbeat_at=task.execution_lease.heartbeat_at,
                        reason="timeout: stuck >30min",
                    )
                    stale_running += 1
            except (StaleExecutionLeaseError, ValueError, TypeError):
                continue
        return stale_running

    async def _handoff_reviewed_tasks(
        self,
        review_result: Dict[str, Any],
    ) -> tuple[list[Dict[str, Any]], int]:
        handed_off: list[Dict[str, Any]] = []
        handoff_considered_ids: set[str] = set()
        budget_exhausted = 0

        for task_payload in review_result.get("tasks", []):
            if task_payload.get("status") != "approved":
                continue
            task = self._get_task(str(task_payload.get("task_id") or ""))
            if task is None:
                continue
            handoff_considered_ids.add(task.task_id)
            if not self._is_supervisor_handoff_task(task) or task.execution_request is None:
                continue
            if not self._handoff_budget_available(handed_off):
                budget_exhausted += 1
                continue
            result = await self._handoff_execution(task)
            if result is not None:
                handed_off.append(
                    {"task_id": task.task_id, "status": result.get("status")}
                )

        handed_off_ids = {item["task_id"] for item in handed_off}
        for task in self._list_execution_lane_tasks("approved"):
            if task.task_id in handed_off_ids or task.task_id in handoff_considered_ids:
                continue
            if not self._is_supervisor_handoff_task(task) or task.execution_request is None:
                continue
            if not self._handoff_budget_available(handed_off):
                budget_exhausted += 1
                continue
            result = await self._handoff_execution(task)
            if result is not None:
                handed_off.append(
                    {"task_id": task.task_id, "status": result.get("status")}
                )

        return handed_off, budget_exhausted

    def _is_supervisor_handoff_task(self, task: AutonomousChainTask) -> bool:
        return not (
            self._task_profile_policy.governance_type(task) == "self_learning"
            or self._task_profile_policy.execution_kind(task) == "body_improvement"
        )

    def _normalized_handoff_limit(self) -> int:
        return int(self._handoff_limit() or 0)

    def _handoff_budget_available(self, handed_off: list[Dict[str, Any]]) -> bool:
        limit = self._normalized_handoff_limit()
        return limit <= 0 or len(handed_off) < limit

    @staticmethod
    def _skipped_result() -> Dict[str, Any]:
        return {
            "reviewed": 0,
            "handed_off": [],
            "recovered_orphaned": 0,
            "stale_running": 0,
            "governance_consumption": {"count": 0, "consumed": []},
            "alignment_consumption": {"count": 0, "consumed": []},
            "truthfulness_consumption": {"count": 0, "consumed": []},
            "skipped": "cycle_already_running",
        }


__all__ = ["AutonomousTaskReviewCycleService"]
