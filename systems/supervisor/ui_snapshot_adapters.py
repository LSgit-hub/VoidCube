"""Timeout, cache, and normalization adapters for Supervisor UI snapshots."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Tuple


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUIObservationSnapshotContext:
    """Callbacks needed to load and cache the runtime observation snapshot."""

    load_runtime_observation_input: Callable[[], Awaitable[Any]]
    default_snapshot: Callable[[], JsonDict]
    get_cached_snapshot: Callable[[], JsonDict]
    set_cached_snapshot: Callable[[JsonDict], None]


@dataclass(frozen=True, slots=True)
class SupervisorUIMemorySnapshotContext:
    """Callbacks needed to load and cache the memory status snapshot."""

    fetch_tier1_stats: Callable[[], Awaitable[JsonDict]]
    get_cached_snapshot: Callable[[], JsonDict]
    set_cached_snapshot: Callable[[JsonDict], None]


async def load_observation_input_snapshot(
    *,
    context: SupervisorUIObservationSnapshotContext,
    timeout_seconds: float = 0.8,
) -> Tuple[JsonDict, bool]:
    default_snapshot = context.default_snapshot()
    try:
        payload = await asyncio.wait_for(
            context.load_runtime_observation_input(),
            timeout=max(float(timeout_seconds), 0.05),
        )
    except Exception:
        cached = dict(context.get_cached_snapshot() or {})
        if cached:
            cached["snapshot_source"] = "cached"
            return cached, False
        return default_snapshot, False

    normalized = dict(payload.get("observation_input") or {})
    if not normalized:
        normalized = dict(default_snapshot)
        normalized["snapshot_source"] = "default"
    normalized["activity"] = dict(normalized.get("activity") or {})
    normalized["user_chain_signal"] = dict(normalized.get("user_chain_signal") or {})
    if not normalized["user_chain_signal"]:
        normalized["user_chain_signal"] = dict(default_snapshot["user_chain_signal"])
    normalized["user_chain_signal"]["scope"] = str(
        normalized["user_chain_signal"].get("scope") or "soft_signal_only"
    ).strip() or "soft_signal_only"
    context.set_cached_snapshot(dict(normalized))
    return normalized, True


async def load_memory_stats(
    *,
    context: SupervisorUIMemorySnapshotContext,
    timeout_seconds: float = 0.8,
) -> JsonDict:
    try:
        stats = await asyncio.wait_for(
            context.fetch_tier1_stats(),
            timeout=max(float(timeout_seconds), 0.05),
        )
    except Exception:
        cached = dict(context.get_cached_snapshot() or {})
        if cached:
            cached["snapshot_source"] = "cached"
            return cached
        return {
            "memory_unavailable": True,
            "memory_unavailable_reason": "ui_snapshot_unavailable",
            "memory_active": False,
            "snapshot_source": "default",
        }

    normalized = dict(stats or {})
    normalized["snapshot_source"] = "live"
    context.set_cached_snapshot(dict(normalized))
    return normalized
