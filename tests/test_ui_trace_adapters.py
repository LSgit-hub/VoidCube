import pytest

from voidcube.systems.supervisor.ui_trace_adapters import (
    SupervisorUITraceContext,
    attach_recent_trace_details_to_observation,
    collect_ui_trace_records,
    load_recent_trace_details,
    recent_local_supervisor_observation_timeline,
)


def _context():
    records = {
        "task": [{"trace_id": "trace-1", "source": "task"}],
        "activity": [{"trace_id": "trace-1", "source": "activity"}],
        "history": [{"trace_id": "trace-1", "source": "history"}],
    }

    def build_timeline(items):
        return list(items)

    return SupervisorUITraceContext(
        collect_trace_records_from_tasks=lambda **kwargs: records["task"],
        collect_trace_records_from_supervisor_activity=lambda **kwargs: records[
            "activity"
        ],
        collect_trace_records_from_governor_history=lambda **kwargs: records[
            "history"
        ],
        build_trace_timeline=build_timeline,
        summarize_single_trace=lambda trace_id, items: {
            "trace_id": trace_id,
            "record_count": len(items),
        },
    )


def test_trace_owner_collects_and_bounds_recent_timeline():
    context = _context()

    records = collect_ui_trace_records(context=context, trace_id="trace-1", limit=0)
    timeline = recent_local_supervisor_observation_timeline(context=context, limit=2)

    assert len(records) == 3
    assert len(timeline) == 2
    assert timeline[0]["source"] == "history"


@pytest.mark.asyncio
async def test_trace_owner_projects_detail_and_attaches_observation_refs():
    context = _context()

    details = await load_recent_trace_details(
        context=context,
        trace_ids=["", "trace-1", "trace-1", "trace-2"],
        limit=1,
    )
    observation = await attach_recent_trace_details_to_observation(
        context=context,
        observation={
            "chain": {
                "segments": [
                    {
                        "latest_trace_id": "trace-1",
                        "recent_traces": [{"trace_id": "trace-1"}],
                    }
                ]
            }
        },
    )

    assert list(details) == ["trace-1"]
    assert details["trace-1"]["trace_id"] == "trace-1"
    assert observation["chain"]["segments"][0]["latest_trace_detail"][
        "record_count"
    ] == 3


@pytest.mark.asyncio
async def test_trace_owner_batches_recent_detail_collection_once():
    calls = {"task": 0, "activity": 0, "history": 0}
    records = {
        "task": [
            {"trace_id": "trace-1", "source": "task"},
            {"trace_id": "trace-2", "source": "task"},
        ],
        "activity": [
            {"trace_id": "trace-1", "source": "activity"},
        ],
        "history": [
            {"trace_id": "trace-2", "source": "history"},
        ],
    }

    def build_timeline(items):
        return list(items)

    def collect(name):
        def _collect(**kwargs):
            calls[name] += 1
            return records[name]

        return _collect

    context = SupervisorUITraceContext(
        collect_trace_records_from_tasks=collect("task"),
        collect_trace_records_from_supervisor_activity=collect("activity"),
        collect_trace_records_from_governor_history=collect("history"),
        build_trace_timeline=build_timeline,
        summarize_single_trace=lambda trace_id, items: {
            "trace_id": trace_id,
            "record_count": len(items),
        },
    )

    details = await load_recent_trace_details(
        context=context,
        trace_ids=["trace-1", "trace-2"],
        limit=2,
    )

    assert calls == {"task": 1, "activity": 1, "history": 1}
    assert details["trace-1"]["record_count"] == 2
    assert details["trace-2"]["record_count"] == 2
