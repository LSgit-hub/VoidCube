import pytest

from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainStore
from voidcube.systems.supervisor.autonomous_task_review_service import AutonomousTaskReviewService
from voidcube.systems.supervisor.autonomous_task_state import AutonomousTaskStateService
from voidcube.systems.supervisor.schedule_allocator import ScheduleAllocator
from voidcube.systems.supervisor.task_profile_policy import TaskProfilePolicy


class _Repository:
    def __init__(self) -> None:
        self.events = []

    def append(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_review_service_runs_through_explicit_ports(tmp_path) -> None:
    store = AutonomousChainStore(tmp_path / "tasks.json")
    state = AutonomousTaskStateService(
        store=store,
        governance_repository=_Repository(),
    )
    state.create_task(title="review through ports")

    async def resolve_drive_input(request, **kwargs):
        del request, kwargs
        return {
            "task_family": "general_self_evolution",
            "decisions": {"eligible_for_execution": False},
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }

    async def adviser(tasks, *, drive_input):
        assert len(tasks) == 1
        assert drive_input["decisions"]["eligible_for_execution"] is False
        return {}

    async def promote(task):
        del task
        return None

    async def touch_activity(kind, *, metadata):
        assert kind == "autonomous_chain_plan"
        assert metadata["action"] == "review"

    service = AutonomousTaskReviewService(
        store=store,
        task_profile_policy=TaskProfilePolicy(),
        schedule_allocator=ScheduleAllocator(slot_interval_seconds=300),
        task_state=state,
        resolve_drive_input=resolve_drive_input,
        auto_decision=lambda task, drive_input: ("deferred", "busy"),
        normalize_context=lambda **kwargs: dict(kwargs),
        propose_memory_promotion=promote,
        build_response_fields=lambda **kwargs: {"drive_input": kwargs["drive_input"]},
        serialize_task=lambda task: task.model_dump(mode="json"),
        build_activity_metadata=lambda tasks, *, action, extra: {
            "action": action,
            "count": len(tasks),
            **extra,
        },
        record_activity=lambda *args, **kwargs: None,
        touch_activity=touch_activity,
        get_active_tasks=lambda: store.list_tasks(),
        get_review_statuses=lambda: ["planned"],
        review_adviser=adviser,
        planning_activity_kind_for_task=lambda task_type: "autonomous_chain_plan",
    )

    result = await service.review({})

    assert result["status"] == "reviewed"
    assert result["decision"] == "deferred"
    assert result["tasks"][0]["status"] == "deferred"
    assert store.list_tasks()[0].status == "deferred"
