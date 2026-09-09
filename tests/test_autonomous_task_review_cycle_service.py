from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from voidcube.systems.supervisor.autonomous_task_review_cycle_service import (
    AutonomousTaskReviewCycleService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("review_existing", [False, True])
async def test_existing_assignment_does_not_starve_next_new_dispatch(review_existing):
    tasks = [SimpleNamespace(task_id=name) for name in ("existing", "new", "next")]
    assignments = {"existing"}

    def dispatch(task):
        if task.task_id in assignments:
            return {"status": "already_dispatched"}
        assignments.add(task.task_id)
        return {"status": "dispatched"}

    dispatch_employee = Mock(side_effect=dispatch)
    review_tasks = AsyncMock(return_value={
        "count": 1 if review_existing else 0,
        "tasks": [{"task_id": "existing", "status": "approved"}] if review_existing else [],
    })
    service = AutonomousTaskReviewCycleService(
        list_execution_lane_tasks=lambda status: tasks,
        get_task=lambda task_id: next(task for task in tasks if task.task_id == task_id),
        review_tasks=review_tasks,
        consume_governance_events=lambda: {"count": 0},
        consume_alignment_events=lambda: {"count": 0},
        consume_truthfulness_alerts=lambda: {"count": 0},
        dispatch_employee=dispatch_employee,
        reconcile_employees=AsyncMock(return_value=[]),
        dispatch_limit=lambda: 1,
    )

    first = await service.run()
    assert first["dispatched"] == [{"task_id": "new", "status": "dispatched"}]
    assert first["dispatch_budget_exhausted"] == 1
    assert [entry.args[0].task_id for entry in dispatch_employee.call_args_list] == ["existing", "new"]

    second = await service.run()
    assert second["dispatched"] == [{"task_id": "next", "status": "dispatched"}]
    assert second["dispatch_budget_exhausted"] == 0
    third = await service.run()
    assert third["dispatched"] == []
    assert third["dispatch_budget_exhausted"] == 0
