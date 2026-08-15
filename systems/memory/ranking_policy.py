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
        return (
            self.query_relevance_weight * query_relevance
            + self.proximity_weight * proximity
            + self.dynamic_weight * dynamic_weight
            + self.importance_weight * importance
            + self.recency_weight * recency
        )


GRAPH_RECALL_SCORING_POLICY = GraphRecallScoringPolicy()


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
