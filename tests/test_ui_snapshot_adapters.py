import pytest

from voidcube.systems.supervisor.ui_projection import default_observation_input_snapshot
from voidcube.systems.supervisor.ui_snapshot_adapters import (
    SupervisorUIObservationSnapshotContext,
    SupervisorUIMemorySnapshotContext,
    load_memory_stats,
    load_observation_input_snapshot,
)


@pytest.mark.asyncio
async def test_observation_snapshot_owner_normalizes_live_input_and_cache_fallback():
    cached = {}
    context = SupervisorUIObservationSnapshotContext(
        load_runtime_observation_input=lambda: _observation_payload(),
        default_snapshot=default_observation_input_snapshot,
        get_cached_snapshot=lambda: cached,
        set_cached_snapshot=lambda value: cached.update(value),
    )

    live, available = await load_observation_input_snapshot(context=context)
    assert available is True
    assert live["user_chain_signal"]["scope"] == "soft_signal_only"
    assert cached["user_chain_signal"]["scope"] == "soft_signal_only"

    async def unavailable():
        raise RuntimeError("observation down")

    fallback_context = SupervisorUIObservationSnapshotContext(
        load_runtime_observation_input=unavailable,
        default_snapshot=default_observation_input_snapshot,
        get_cached_snapshot=lambda: cached,
        set_cached_snapshot=lambda value: None,
    )
    fallback, fallback_available = await load_observation_input_snapshot(
        context=fallback_context
    )
    assert fallback_available is False
    assert fallback["snapshot_source"] == "cached"


@pytest.mark.asyncio
async def test_memory_snapshot_owner_marks_live_and_default_sources():
    cached = {}
    context = SupervisorUIMemorySnapshotContext(
        fetch_tier1_stats=lambda: _memory_payload(),
        get_cached_snapshot=lambda: cached,
        set_cached_snapshot=lambda value: cached.update(value),
    )

    live = await load_memory_stats(context=context)
    assert live == {"memory_active": True, "snapshot_source": "live"}

    async def unavailable():
        raise RuntimeError("memory down")

    fallback = await load_memory_stats(
        context=SupervisorUIMemorySnapshotContext(
            fetch_tier1_stats=unavailable,
            get_cached_snapshot=lambda: {},
            set_cached_snapshot=lambda value: None,
        )
    )
    assert fallback["memory_unavailable"] is True
    assert fallback["snapshot_source"] == "default"


async def _observation_payload():
    return {
        "observation_input": {
            "activity": None,
            "user_chain_signal": {"scope": " "},
        }
    }


async def _memory_payload():
    return {"memory_active": True}
