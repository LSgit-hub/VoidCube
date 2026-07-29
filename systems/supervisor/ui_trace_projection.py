"""Pure timeline, trace, and chain-segment projections for the Supervisor UI."""

from __future__ import annotations

from typing import Any


def project_chain_section_events(
    *,
    key: str,
    items: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    limit: int = 4,
) -> list[dict[str, Any]]:
    task_ids = {
        str(item.get("task_id") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("task_id") or "").strip()
    }
    trace_ids = {
        str(item.get("trace_id") or "").strip()
        for item in items
        if isinstance(item, dict) and str(item.get("trace_id") or "").strip()
    }

    def matches(event: dict[str, Any]) -> bool:
        event_task_id = str(event.get("task_id") or "").strip()
        event_trace_id = str(event.get("trace_id") or "").strip()
        if event_task_id and event_task_id in task_ids:
            return True
        if event_trace_id and event_trace_id in trace_ids:
            return True
        event_type = str(event.get("event_type") or "").strip().lower()
        summary = str(event.get("summary") or "").strip().lower()
        if key == "api_b_candidates":
            return (
                "endogenous_drive" in event_type
                or "candidate" in summary
                or "候选" in summary
            )
        if key == "mem_recent":
            return "writeback" in event_type or "写回" in summary
        return False

    matched: list[dict[str, Any]] = []
    for event in timeline:
        if not isinstance(event, dict) or not matches(event):
            continue
        matched.append(
            {
                "recorded_at": event.get("recorded_at"),
                "source": str(event.get("source") or "").strip(),
                "source_label": str(event.get("source_label") or "").strip(),
                "event_type": str(event.get("event_type") or "").strip(),
                "event_label": str(event.get("event_label") or "").strip(),
                "summary": str(event.get("summary") or "").strip()[:160],
                "task_id": str(event.get("task_id") or "").strip(),
                "trace_id": str(event.get("trace_id") or "").strip(),
            }
        )
        if len(matched) >= max(int(limit), 1):
            break
    return matched


def project_chain_section_traces(
    *,
    items: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    titles_by_trace: dict[str, list[str]] = {}
    task_ids_by_trace: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        trace_id = str(item.get("trace_id") or "").strip()
        if not trace_id:
            continue
        title = str(item.get("title") or "").strip()
        task_id = str(item.get("task_id") or "").strip()
        if title:
            titles = titles_by_trace.setdefault(trace_id, [])
            if title not in titles:
                titles.append(title)
        if task_id:
            task_ids = task_ids_by_trace.setdefault(trace_id, [])
            if task_id not in task_ids:
                task_ids.append(task_id)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in recent_events:
        trace_id = str((event or {}).get("trace_id") or "").strip()
        if trace_id:
            grouped.setdefault(trace_id, []).append(dict(event))

    traces: list[dict[str, Any]] = []
    for trace_id, events in grouped.items():
        if not events:
            continue
        first = dict(events[0])
        sources: list[str] = []
        source_labels: list[str] = []
        task_ids = list(task_ids_by_trace.get(trace_id) or [])
        for event in events:
            source = str(event.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)
            source_label = str(event.get("source_label") or "").strip()
            if source_label and source_label not in source_labels:
                source_labels.append(source_label)
            event_task_id = str(event.get("task_id") or "").strip()
            if event_task_id and event_task_id not in task_ids:
                task_ids.append(event_task_id)
        traces.append(
            {
                "trace_id": trace_id,
                "event_count": len(events),
                "last_seen_at": first.get("recorded_at"),
                "last_event_type": str(first.get("event_type") or "").strip(),
                "last_event_label": str(first.get("event_label") or "").strip(),
                "latest_summary": str(first.get("summary") or "").strip()[:160],
                "sources": sources,
                "source_labels": source_labels,
                "task_ids": task_ids,
                "task_titles": list(titles_by_trace.get(trace_id) or []),
            }
        )
    traces.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    return traces[: max(int(limit), 1)]


def project_chain_segment_focus_item(
    *,
    items: list[dict[str, Any]],
    activity_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        dict(item)
        for item in [*activity_items, *items]
        if isinstance(item, dict)
    ]
    if not candidates:
        return None

    preferred_statuses = (
        "active", "running", "awaiting_user_consent", "ready", "approved",
        "candidate", "retry", "planned", "awaiting_review", "deferred",
        "paused", "completed", "failed",
    )
    for expected in preferred_statuses:
        for item in candidates:
            if str(item.get("status") or "").strip().lower() == expected:
                return dict(item)
    return dict(candidates[0])


def project_chain_segment_status(
    *,
    items: list[dict[str, Any]],
    activity_items: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
) -> tuple[str, str]:
    normalized_statuses = {
        str(item.get("status") or "").strip().lower()
        for item in [*activity_items, *items]
        if isinstance(item, dict)
    }
    if normalized_statuses.intersection({"active", "running"}):
        return "active", "当前有流动"
    if items or recent_events or normalized_statuses.intersection(
        {
            "ready", "approved", "awaiting_user_consent", "candidate", "retry",
            "planned", "awaiting_review", "deferred", "paused", "completed", "failed",
        }
    ):
        return "ready", "已有观测"
    return "idle", "暂无信号"


def project_chain_segment_activity(
    *,
    chain_segments: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    activity_items_by_key: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for group in chain_segments:
        section = dict(group or {})
        items = [dict(item) for item in list(section.get("items") or []) if isinstance(item, dict)]
        activity_items = [
            dict(item)
            for item in list((activity_items_by_key or {}).get(str(section.get("key") or "").strip()) or [])
            if isinstance(item, dict)
        ]
        combined_items = [*items, *activity_items]
        recent_events = project_chain_section_events(
            key=str(section.get("key") or "").strip(),
            items=combined_items,
            timeline=timeline,
        )
        recent_traces = project_chain_section_traces(
            items=combined_items,
            recent_events=recent_events,
        )
        focus_item = project_chain_segment_focus_item(
            items=items,
            activity_items=activity_items,
        )
        segment_status, segment_status_label = project_chain_segment_status(
            items=items,
            activity_items=activity_items,
            recent_events=recent_events,
        )
        section["items"] = items
        section["recent_events"] = recent_events
        section["event_count"] = len(recent_events)
        section["recent_event_count"] = len(recent_events)
        section["latest_trace_id"] = next(
            (str(event.get("trace_id") or "").strip() for event in recent_events if str(event.get("trace_id") or "").strip()),
            "",
        )
        section["recent_traces"] = recent_traces
        section["trace_count"] = len(recent_traces)
        section["payload_count"] = len(items)
        section["segment_status"] = segment_status
        section["segment_status_label"] = segment_status_label
        section["focus_item"] = dict(focus_item) if isinstance(focus_item, dict) else None
        section["latest_item"] = dict(items[0]) if items else section["focus_item"]
        item_label = str(section.get("item_label") or "").strip() or "链路项"
        event_label = str(section.get("event_label") or "").strip() or "动作"
        trace_label = str(section.get("trace_label") or "").strip() or "回合"
        footer_label = str(section.get("footer_label") or "").strip() or "查看最近状态"
        latest_summary = (
            str(recent_events[0].get("summary") or "").strip()
            if recent_events else str((focus_item or {}).get("summary") or "").strip()
        )
        section["latest_summary"] = str(
            latest_summary or section.get("summary") or section.get("empty_text") or ""
        ).strip()[:160]
        trace_ids = [str(trace.get("trace_id") or "").strip() for trace in recent_traces if str(trace.get("trace_id") or "").strip()]
        section["drawer_summary"] = " · ".join(
            part for part in (
                str(section.get("source_label") or "").strip(),
                str(section.get("stage_label") or "").strip(),
                str(section.get("summary") or section.get("empty_text") or "").strip(),
            ) if part
        )[:220]
        counts_summary = f"当前可见{item_label} {len(items)} · 最近{event_label} {len(recent_events)}"
        if trace_ids:
            counts_summary += f" · 回合 {' / '.join(trace_ids[:3])}"
        section["drawer_counts_summary"] = counts_summary[:220]
        section["drawer_empty_items_text"] = f"当前这一段没有可见{item_label}，但仍可能有最近{event_label}。"[:160]
        section["drawer_recent_events_label"] = f"最近{event_label}"
        section["drawer_recent_traces_label"] = f"最近{trace_label}"
        section["footer_text"] = (
            f"{item_label} {len(items)} · {event_label} {len(recent_events)} · {trace_label} {len(recent_traces)}"
            if items or recent_events or recent_traces else footer_label
        )[:180]
        section["projection_scope"] = "chain_segment_projection"
        enriched.append(section)
    return enriched


def project_trace_detail(
    *,
    trace_id: str,
    summary: dict[str, Any],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    def event_projection(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "recorded_at": event.get("recorded_at"),
            "source": str(event.get("source") or "").strip(),
            "source_label": str(event.get("source_label") or "").strip(),
            "event_type": str(event.get("event_type") or "").strip(),
            "event_label": str(event.get("event_label") or "").strip(),
            "summary": str(event.get("summary") or "").strip()[:160],
            "task_id": str(event.get("task_id") or "").strip(),
            "decision_id": str(event.get("decision_id") or "").strip(),
        }

    return {
        "trace_id": trace_id,
        "found": bool(summary.get("record_count")),
        "record_count": int(summary.get("record_count") or 0),
        "first_seen_at": summary.get("first_seen_at"),
        "last_seen_at": summary.get("last_seen_at"),
        "source_counts": dict(summary.get("sources") or {}),
        "source_labels": list(summary.get("source_labels") or []),
        "task_ids": list(summary.get("task_ids") or []),
        "decision_ids": list(summary.get("decision_ids") or []),
        "task_families": list(summary.get("task_families") or []),
        "governance_labels": list(summary.get("governance_labels") or []),
        "execution_kinds": list(summary.get("execution_kinds") or []),
        "execution_labels": list(summary.get("execution_labels") or []),
        "timeline_preview": [event_projection(event) for event in reversed(timeline[-6:])],
        "timeline_events": [event_projection(event) for event in reversed(timeline[-20:])],
    }


def recent_observation_trace_ids(observation: dict[str, Any]) -> list[str]:
    chain = dict(observation.get("chain") or {})
    trace_ids: list[str] = []
    for section in list(chain.get("segments") or []):
        if not isinstance(section, dict):
            continue
        for trace in list(section.get("recent_traces") or []):
            trace_id = str((trace or {}).get("trace_id") or "").strip()
            if trace_id and trace_id not in trace_ids:
                trace_ids.append(trace_id)
    return trace_ids


def attach_observation_trace_details(
    observation: dict[str, Any],
    *,
    details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    projected = dict(observation)
    chain = dict(projected.get("chain") or {})
    enriched_segments: list[dict[str, Any]] = []
    for raw_section in list(chain.get("segments") or []):
        if not isinstance(raw_section, dict):
            continue
        section = dict(raw_section)
        traces: list[dict[str, Any]] = []
        for trace in list(section.get("recent_traces") or []):
            if not isinstance(trace, dict):
                continue
            trace_payload = dict(trace)
            trace_id = str(trace_payload.get("trace_id") or "").strip()
            if trace_id:
                trace_payload["detail"] = dict(details.get(trace_id) or {})
            traces.append(trace_payload)
        section["recent_traces"] = traces
        latest_trace_id = str(section.get("latest_trace_id") or "").strip()
        if latest_trace_id and latest_trace_id in details:
            section["latest_trace_detail"] = dict(details[latest_trace_id])
        enriched_segments.append(section)
    chain["segments"] = enriched_segments
    projected["chain"] = chain
    return projected
