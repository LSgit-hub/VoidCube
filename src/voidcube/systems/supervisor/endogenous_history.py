"""Pure historical outcome normalization and pressure calculation."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from systems.supervisor.endogenous_drive_context import parse_timestamp


_DRAG_STATUSES = {
    "failed",
    "cancelled",
    "approved",
    "deferred",
    "paused",
    "awaiting_review",
    "awaiting_user_consent",
    "retry",
}


def summarize_historical_pressure(
    *,
    recent_historical_outcomes: List[Dict[str, object]],
    recent_self_learning_outcomes: List[Dict[str, object]],
) -> Dict[str, object]:
    scoped_outcomes = list(recent_historical_outcomes)
    scope = "global"
    if len(recent_self_learning_outcomes) >= 3:
        scope = "self_learning"
        scoped_outcomes = list(recent_self_learning_outcomes)

    completed = 0
    failed = 0
    blocked = 0
    for item in scoped_outcomes:
        status = str(item.get("status") or "").strip().lower()
        if status == "completed":
            completed += 1
        elif status in {"failed", "cancelled"}:
            failed += 1
        elif status in {
            "approved",
            "deferred",
            "paused",
            "awaiting_review",
            "awaiting_user_consent",
            "retry",
        }:
            blocked += 1
    total = completed + failed + blocked
    success_ratio = completed / total if total > 0 else 0.5
    drag_ratio = (failed + blocked) / total if total > 0 else 0.0
    has_temporal_markers = any(
        item.get("recorded_at")
        or item.get("completed_at")
        or item.get("updated_at")
        or item.get("created_at")
        for item in recent_self_learning_outcomes
    )

    def status_counts(window: List[Dict[str, object]]) -> tuple[int, int]:
        drag_count = 0
        completed_count = 0
        for item in window:
            status = str(item.get("status") or "").strip().lower()
            if status == "completed":
                completed_count += 1
            elif status in _DRAG_STATUSES:
                drag_count += 1
        return drag_count, completed_count

    relapse_drag_count = 0
    relapse_drag_ratio = 0.0
    relapse_windows = [
        (
            list(recent_self_learning_outcomes[:3]),
            list(recent_self_learning_outcomes[3:6]),
        ),
        (
            list(recent_self_learning_outcomes[-3:]),
            list(recent_self_learning_outcomes[-6:-3]),
        ),
    ]
    for relapse_window, recovery_context in relapse_windows:
        if len(relapse_window) < 3:
            continue
        drag_count, completed_count = status_counts(relapse_window)
        _, recovery_completed_count = status_counts(recovery_context)
        if (
            drag_count >= 2
            and completed_count >= 1
            and recovery_completed_count >= 1
        ):
            ratio = drag_count / len(relapse_window)
            if ratio > relapse_drag_ratio:
                relapse_drag_ratio = ratio
                relapse_drag_count = drag_count

    underdelivery_active = (
        total >= 3
        and (
            (drag_ratio >= 0.6 and success_ratio <= 0.34)
            or (
                len(recent_self_learning_outcomes) >= 5
                and relapse_drag_count >= 2
                and relapse_drag_ratio >= 0.66
            )
            or (
                not has_temporal_markers
                and len(recent_self_learning_outcomes) >= 7
                and completed >= 3
                and drag_ratio >= 0.6
            )
        )
    )
    return {
        "scope": scope,
        "scoped_outcomes": scoped_outcomes,
        "total": total,
        "success_ratio": success_ratio,
        "drag_ratio": drag_ratio,
        "has_temporal_markers": has_temporal_markers,
        "recent_relapse_drag_count": relapse_drag_count,
        "recent_relapse_drag_ratio": relapse_drag_ratio,
        "underdelivery_active": underdelivery_active,
    }


def normalize_historical_outcomes(
    outcomes: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    indexed_rows: List[tuple[int, datetime, Dict[str, object]]] = []
    fallback_rows: List[tuple[int, Dict[str, object]]] = []
    for index, item in enumerate(outcomes):
        row = dict(item)
        parsed = parse_timestamp(
            row.get("recorded_at")
            or row.get("completed_at")
            or row.get("updated_at")
            or row.get("created_at")
        )
        if parsed is None:
            fallback_rows.append((index, row))
        else:
            indexed_rows.append((index, parsed, row))
    if not indexed_rows:
        return [row for _, row in fallback_rows]
    indexed_rows.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    ordered = [row for _, _, row in indexed_rows]
    ordered.extend(row for _, row in fallback_rows)
    return ordered
