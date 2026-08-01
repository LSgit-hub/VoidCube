"""Pure scheduling and conflict calculations for autonomous-chain tasks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, Optional

from systems.supervisor.autonomous_chain_store import AutonomousChainTask


SCHEDULE_KEYS = (
    "scheduled_for",
    "preset_time",
    "scheduled_at",
    "run_at",
    "execute_after",
    "time_slot",
    "window",
)
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
TASK_STATUS_ORDER = {
    "running": 0,
    "approved": 1,
    "planned": 2,
    "deferred": 3,
    "paused": 4,
    "completed": 5,
    "failed": 6,
    "cancelled": 7,
}


class ScheduleAllocator:
    """Allocate non-conflicting schedule tokens without store or clock access."""

    def __init__(self, *, slot_interval_seconds: int = 300) -> None:
        self.slot_interval_seconds = max(300, int(slot_interval_seconds or 300))

    @staticmethod
    def normalize_scheduled_for_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).isoformat()
        except ValueError:
            return text

    def normalize_metadata(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(metadata or {})
        scheduled_for = self.schedule_token_from_sources(normalized)
        if scheduled_for:
            normalized["scheduled_for"] = scheduled_for
        return normalized

    def schedule_token_from_sources(self, *sources: Any) -> Optional[str]:
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            for key in SCHEDULE_KEYS:
                scheduled_for = self.normalize_scheduled_for_value(source.get(key))
                if scheduled_for:
                    return scheduled_for
        return None

    def task_schedule_token(self, task: AutonomousChainTask) -> Optional[str]:
        execution = dict(task.metadata.get("execution_request") or {})
        evidence = dict(task.evidence or {})
        endogenous_drive = dict(evidence.get("endogenous_drive") or {})
        return self.schedule_token_from_sources(
            task.metadata,
            task.constraints,
            evidence,
            endogenous_drive,
            execution,
        )

    def align(self, when: datetime) -> datetime:
        base = when.replace(second=0, microsecond=0)
        since_midnight = base.hour * 3600 + base.minute * 60 + base.second
        remainder = since_midnight % self.slot_interval_seconds
        if remainder == 0:
            return base
        return base + timedelta(seconds=self.slot_interval_seconds - remainder)

    def occupied_tokens(self, tasks: Iterable[AutonomousChainTask]) -> set[str]:
        occupied: set[str] = set()
        for task in tasks:
            if str(task.status or "").strip().lower() in TERMINAL_TASK_STATUSES:
                continue
            token = self.task_schedule_token(task)
            if token:
                occupied.add(token)
        return occupied

    def allocate_tokens(
        self,
        *,
        count: int,
        now: datetime,
        occupied_tokens: Optional[set[str]] = None,
    ) -> list[str]:
        if count <= 0:
            return []

        occupied = set(occupied_tokens or set())
        scheduled: list[str] = []
        cursor = self.align(now)
        while len(scheduled) < count:
            cursor = self.align(cursor)
            token = cursor.isoformat()
            if token not in occupied:
                occupied.add(token)
                scheduled.append(token)
            cursor = cursor + timedelta(seconds=self.slot_interval_seconds)
        return scheduled

    def apply_to_candidates(
        self,
        candidate_items: list[dict[str, Any]],
        *,
        occupied_tokens: set[str],
        now: datetime,
    ) -> list[dict[str, Any]]:
        if not candidate_items:
            return []

        occupied = set(occupied_tokens)
        prepared: list[dict[str, Any]] = []
        missing_indexes: list[int] = []
        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row_metadata = self.normalize_metadata(dict(row.get("metadata") or {}))
            row["metadata"] = row_metadata
            existing_token = self.schedule_token_from_sources(
                row,
                row_metadata,
                row.get("constraints"),
                row.get("evidence"),
            )
            if existing_token:
                if existing_token in occupied:
                    row_metadata["requested_scheduled_for"] = existing_token
                    row_metadata["schedule_token_reallocated"] = True
                    row.pop("scheduled_for", None)
                    row_metadata.pop("scheduled_for", None)
                    missing_indexes.append(len(prepared))
                else:
                    row["scheduled_for"] = existing_token
                    row_metadata["scheduled_for"] = existing_token
                    occupied.add(existing_token)
            else:
                missing_indexes.append(len(prepared))
            prepared.append(row)

        allocated = self.allocate_tokens(
            count=len(missing_indexes),
            now=now,
            occupied_tokens=occupied,
        )
        for row_index, token in zip(missing_indexes, allocated):
            prepared[row_index]["scheduled_for"] = token
            prepared[row_index].setdefault("metadata", {})
            prepared[row_index]["metadata"]["scheduled_for"] = token
        return prepared

    @staticmethod
    def task_sort_key(task: AutonomousChainTask) -> tuple[int, str, str]:
        status = str(task.status or "").strip().lower()
        created_text = (
            task.created_at.isoformat()
            if isinstance(task.created_at, datetime)
            else str(task.created_at or "")
        )
        updated_text = (
            task.updated_at.isoformat()
            if isinstance(task.updated_at, datetime)
            else str(task.updated_at or "")
        )
        return (TASK_STATUS_ORDER.get(status, 99), created_text, updated_text)

    def conflict_index(
        self,
        tasks: Iterable[AutonomousChainTask],
        *,
        exclude_task_ids: Optional[set[str]] = None,
    ) -> dict[str, AutonomousChainTask]:
        excluded = exclude_task_ids or set()
        conflicts: dict[str, AutonomousChainTask] = {}
        for task in sorted(tasks, key=self.task_sort_key):
            if task.task_id in excluded:
                continue
            if str(task.status or "").strip().lower() in TERMINAL_TASK_STATUSES:
                continue
            schedule_token = self.task_schedule_token(task)
            if schedule_token:
                conflicts.setdefault(schedule_token, task)
        return conflicts


__all__ = ["ScheduleAllocator"]
