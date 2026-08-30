"""Trace loading adapters used by the Supervisor UI projection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .ui_trace_projection import (
    attach_observation_trace_details,
    project_trace_detail,
    recent_observation_trace_ids,
)


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUITraceContext:
    """Trace runtime callbacks needed by the UI detail and timeline adapters."""

    collect_trace_records_from_tasks: Callable[..., List[JsonDict]]
    collect_trace_records_from_supervisor_activity: Callable[..., List[JsonDict]]
    collect_trace_records_from_governor_history: Callable[..., List[JsonDict]]
    build_trace_timeline: Callable[..., List[JsonDict]]
    summarize_single_trace: Callable[..., JsonDict]


def collect_ui_trace_records(
    *,
    context: SupervisorUITraceContext,
    trace_id: Optional[str] = None,
    limit: int = 200,
) -> List[JsonDict]:
    records: List[JsonDict] = []
    records.extend(
        context.collect_trace_records_from_tasks(trace_id=trace_id)
    )
    records.extend(
        context.collect_trace_records_from_supervisor_activity(trace_id=trace_id)
    )
    records.extend(
        context.collect_trace_records_from_governor_history(
            trace_id=trace_id,
            limit=max(int(limit), 1),
        )
    )
    return records


def recent_local_supervisor_observation_timeline(
    *,
    context: SupervisorUITraceContext,
    limit: int = 12,
) -> List[JsonDict]:
    records = collect_ui_trace_records(
        context=context,
        limit=max(int(limit) * 4, 24),
    )
    timeline = [
        dict(record)
        for record in context.build_trace_timeline(records)
        if str(record.get("trace_id") or "").strip()
    ]
    timeline.reverse()
    return timeline[: max(int(limit), 0)]


async def load_recent_trace_details(
    *,
    context: SupervisorUITraceContext,
    trace_ids: List[str],
    limit: int = 6,
) -> Dict[str, JsonDict]:
    normalized: List[str] = []
    for trace_id in trace_ids:
        candidate = str(trace_id or "").strip()
        if not candidate or candidate in normalized:
            continue
        normalized.append(candidate)
        if len(normalized) >= max(int(limit), 1):
            break

    records = collect_ui_trace_records(
        context=context,
        limit=max(int(limit), 1) * 200,
    )
    records_by_trace: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        trace_id = str(record.get("trace_id") or "").strip()
        if not trace_id:
            continue
        records_by_trace.setdefault(trace_id, []).append(record)

    async def _load(trace_id: str) -> tuple[str, JsonDict]:
        trace_records = records_by_trace.get(trace_id, [])
        summary = context.summarize_single_trace(trace_id, trace_records)
        timeline = [dict(event) for event in context.build_trace_timeline(trace_records)]
        return trace_id, project_trace_detail(
            trace_id=trace_id,
            summary=summary,
            timeline=timeline,
        )

    results = await asyncio.gather(*[_load(trace_id) for trace_id in normalized])
    return {trace_id: detail for trace_id, detail in results}


async def attach_recent_trace_details_to_observation(
    *,
    context: SupervisorUITraceContext,
    observation: JsonDict,
) -> JsonDict:
    trace_ids = recent_observation_trace_ids(observation)
    if not trace_ids:
        return observation
    details = await load_recent_trace_details(
        context=context,
        trace_ids=trace_ids,
    )
    return attach_observation_trace_details(observation, details=details)
