"""Progress calculations for goal graphs."""

from __future__ import annotations

from typing import Any, Iterable


def weighted_children_progress(
    children: Iterable[tuple[dict[str, Any], dict[str, Any]]],
) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for child, edge in children:
        if child.get("status") == "cancelled" or not int(edge.get("required", 1)):
            continue
        weight = max(0.0, float(edge.get("progress_weight", 1)))
        numerator += float(child.get("progress", 0)) * weight
        denominator += weight
    return None if denominator == 0 else max(0.0, min(1.0, numerator / denominator))


def evidence_progress(criteria: Any) -> float:
    if not isinstance(criteria, list) or not criteria:
        return 0.0
    met = sum(1 for item in criteria if isinstance(item, dict) and item.get("met"))
    return met / len(criteria)
