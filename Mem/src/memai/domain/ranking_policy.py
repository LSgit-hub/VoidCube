"""Shared ranking signals for durable memory records."""

from __future__ import annotations

from dataclasses import dataclass


_CONTENT_IMPORTANCE_BONUS = {
    "decision": 0.15,
    "correction": 0.12,
    "shift": 0.12,
    "completion": 0.08,
    "conflict": 0.08,
    "blocker": 0.06,
    "progress": 0.04,
}


def bounded_weighted_score(*signals: tuple[float, float]) -> float:
    """Combine unit-interval signals without allowing bonus weights to saturate."""
    weighted_sum = 0.0
    total_weight = 0.0
    for value, weight in signals:
        bounded_weight = max(0.0, float(weight))
        bounded_value = max(0.0, min(1.0, float(value)))
        weighted_sum += bounded_value * bounded_weight
        total_weight += bounded_weight
    if total_weight <= 0.0:
        return 0.0
    return weighted_sum / max(1.0, total_weight)


@dataclass(frozen=True, slots=True)
class GraphRecallScoringPolicy:
    """Versioned graph ranking policy; benchmark metrics gate changes."""

    version: str = "graph-recall-v2"
    query_relevance_weight: float = 0.55
    proximity_weight: float = 0.25
    dynamic_weight: float = 0.10
    importance_weight: float = 0.05
    recency_weight: float = 0.05

    def score(
        self,
        *,
        query_relevance: float,
        proximity: float,
        dynamic_weight: float,
        importance: float,
        recency: float,
    ) -> float:
        return bounded_weighted_score(
            (query_relevance, self.query_relevance_weight),
            (proximity, self.proximity_weight),
            (dynamic_weight, self.dynamic_weight),
            (importance, self.importance_weight),
            (recency, self.recency_weight),
        )


GRAPH_RECALL_SCORING_POLICY = GraphRecallScoringPolicy()


def compute_dynamic_weight(
    base_weight: float,
    *,
    event_kind: str | None = None,
    activity_state: str | None = None,
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
    activity_penalty = 0.75 if str(activity_state or "active") == "dormant" else 1.0
    return max(
        0.0,
        min(1.0, (base_weight + content_bonus + citation_bonus) * activity_penalty),
    )
