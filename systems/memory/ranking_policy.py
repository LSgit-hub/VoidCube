"""Shared ranking signals for durable memory records."""

from __future__ import annotations


_CONTENT_IMPORTANCE_BONUS = {
    "decision": 0.15,
    "correction": 0.12,
    "shift": 0.12,
    "completion": 0.08,
    "conflict": 0.08,
    "blocker": 0.06,
    "progress": 0.04,
}


def compute_dynamic_weight(
    base_weight: float,
    *,
    event_kind: str | None = None,
    access_count: int = 0,
    citation_count: int = 0,
    pinned: bool = False,
    hidden: bool = False,
) -> float:
    """Combine explicit content and reuse signals without retrieval feedback loops."""
    if hidden:
        return 0.0
    if pinned:
        return 1.0
    del access_count
    content_bonus = _CONTENT_IMPORTANCE_BONUS.get(str(event_kind or ""), 0.0)
    citation_bonus = min(citation_count / 5.0, 1.0) * 0.10
    return max(0.0, min(1.0, base_weight + content_bonus + citation_bonus))
