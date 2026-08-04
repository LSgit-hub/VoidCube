from __future__ import annotations

from typing import Any

import pytest

from systems.supervisor.autonomous_chain_planning_service import (
    AutonomousChainPlanningService,
)
from systems.supervisor.autonomous_chain_store import AutonomousChainTask
from systems.supervisor.schedule_allocator import ScheduleAllocator
from systems.supervisor.task_profile_policy import TaskProfilePolicy


class _TaskState:
    def __init__(self) -> None:
        self.created: list[AutonomousChainTask] = []

    def create_task(self, **kwargs: Any) -> AutonomousChainTask:
        task = AutonomousChainTask(**kwargs)
        self.created.append(task)
        return task


class _Store:
    def get_task(self, _task_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_planning_service_creates_tasks_with_normalized_metadata():
    task_state = _TaskState()
    activities: list[dict[str, Any]] = []

    async def touch_activity(*args: Any, **kwargs: Any) -> None:
        activities.append({"args": args, "kwargs": kwargs})

    service = AutonomousChainPlanningService(
        store=_Store(),
        task_state=task_state,
        task_profile_policy=TaskProfilePolicy(),
        schedule_allocator=ScheduleAllocator(),
        build_activity_metadata=lambda tasks, **kwargs: {
            "action": kwargs["action"],
            "count": len(tasks),
        },
        record_activity=lambda *args, **kwargs: None,
        record_drive_outcome=lambda *args, **kwargs: None,
        touch_activity=touch_activity,
    )

    result = await service.plan(
        {
            "title": "Review a governed task",
            "execution_kind": "body_improvement",
            "scheduled_for": "2026-08-04T12:00:00",
        }
    )

    assert result["count"] == 1
    assert task_state.created[0].metadata["task_family"] == "body_improvement"
    assert task_state.created[0].metadata["scheduled_for"] == "2026-08-04T12:00:00"
    assert result["tasks"][0]["task_identity"]["task_id"] == task_state.created[0].task_id
    assert activities[0]["args"] == ("autonomous_chain_plan",)


def test_planning_service_serializes_judgement_preview_without_supervisor():
    service = AutonomousChainPlanningService(
        store=_Store(),
        task_state=_TaskState(),
        task_profile_policy=TaskProfilePolicy(),
        schedule_allocator=ScheduleAllocator(),
        build_activity_metadata=lambda *_args, **_kwargs: {},
        record_activity=lambda *_args, **_kwargs: None,
        record_drive_outcome=lambda *_args, **_kwargs: None,
        touch_activity=lambda *_args, **_kwargs: None,
    )
    task = AutonomousChainTask(
        title="Keep the current governed task",
        decision_history=[
            {
                "status": "approved",
                "context": {
                    "supervisor_review_outcome": {
                        "action": "approve",
                        "reason": "Evidence is sufficient.",
                    }
                }
            }
        ],
    )

    payload = service.serialize_task(task)

    assert payload["judgement_preview"]["review_outcome"]["action_label"] == "转交"
    assert payload["task_identity"]["display_kind"]
