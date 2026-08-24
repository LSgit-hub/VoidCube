from datetime import datetime

from voidcube.systems.supervisor.autonomous_chain_store import AutonomousChainTask
from voidcube.systems.supervisor.schedule_allocator import ScheduleAllocator


def test_schedule_allocator_normalizes_metadata_and_allocates_open_slots() -> None:
    allocator = ScheduleAllocator(slot_interval_seconds=300)

    assert allocator.normalize_metadata({"preset_time": "2026-06-28 00:01:00"}) == {
        "preset_time": "2026-06-28 00:01:00",
        "scheduled_for": "2026-06-28T00:01:00",
    }
    assert allocator.allocate_tokens(
        count=2,
        now=datetime.fromisoformat("2026-06-28T00:00:00"),
        occupied_tokens={"2026-06-28T00:00:00"},
    ) == ["2026-06-28T00:05:00", "2026-06-28T00:10:00"]


def test_schedule_allocator_reallocates_conflicting_candidate_tokens() -> None:
    allocator = ScheduleAllocator()
    prepared = allocator.apply_to_candidates(
        [{"title": "candidate", "metadata": {"scheduled_for": "2026-06-28T01:00:00"}}],
        occupied_tokens={"2026-06-28T01:00:00"},
        now=datetime.fromisoformat("2026-06-28T01:00:00"),
    )

    assert prepared[0]["metadata"]["requested_scheduled_for"] == "2026-06-28T01:00:00"
    assert prepared[0]["metadata"]["schedule_token_reallocated"] is True
    assert prepared[0]["scheduled_for"] == "2026-06-28T01:05:00"


def test_schedule_allocator_ignores_terminal_tasks_in_conflicts() -> None:
    allocator = ScheduleAllocator()
    terminal = AutonomousChainTask(
        title="completed",
        status="completed",
        metadata={"scheduled_for": "2026-06-28T02:00:00"},
    )
    active = AutonomousChainTask(
        title="active",
        status="planned",
        metadata={"scheduled_for": "2026-06-28T02:00:00"},
    )

    assert allocator.occupied_tokens([terminal, active]) == {"2026-06-28T02:00:00"}
    assert allocator.conflict_index([terminal, active]) == {
        "2026-06-28T02:00:00": active
    }
