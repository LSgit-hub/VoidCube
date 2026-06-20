from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Sequence

from .schema import Arc, Epoch, MainOrSide, Status, TimePrecision, new_id


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


class EpochBuilder:
    def build(self, arcs: Sequence[Arc]) -> list[Epoch]:
        ordered = sorted(arcs, key=lambda item: item.timespan_start)
        if not ordered:
            return []

        clusters: list[list[Arc]] = []
        current_cluster: list[Arc] = [ordered[0]]
        for arc in ordered[1:]:
            if self._belongs_to_cluster(current_cluster[-1], arc):
                current_cluster.append(arc)
            else:
                clusters.append(current_cluster)
                current_cluster = [arc]
        clusters.append(current_cluster)

        epochs = [self._build_epoch(cluster) for cluster in clusters]
        for epoch in epochs:
            for arc in ordered:
                if arc.id in epoch.child_ids and epoch.id not in arc.parent_ids:
                    arc.parent_ids.append(epoch.id)
                    arc.touch()
        return epochs

    def _belongs_to_cluster(self, previous: Arc, current: Arc) -> bool:
        if set(previous.topics) & set(current.topics):
            return True
        return (current.timespan_start - previous.timespan_end) <= timedelta(days=90)

    def _build_epoch(self, cluster: Sequence[Arc]) -> Epoch:
        topic_counts = Counter(topic for arc in cluster for topic in arc.topics)
        dominant_topics = [topic for topic, _ in topic_counts.most_common(4)] or [
            "general"
        ]
        entity_counts = Counter(entity for arc in cluster for entity in arc.entities)
        entities = [entity for entity, _ in entity_counts.most_common(6)]
        any_main = any(arc.main_or_side == MainOrSide.MAIN for arc in cluster)
        active_count = sum(1 for arc in cluster if arc.status == Status.ACTIVE)
        dormant_count = sum(1 for arc in cluster if arc.status == Status.DORMANT)

        if active_count:
            status = Status.ACTIVE
        elif dormant_count:
            status = Status.DORMANT
        else:
            status = Status.CLOSED

        return Epoch(
            id=new_id("epoch"),
            type="epoch",
            title=f"Epoch: {' / '.join(dominant_topics[:2])}",
            summary=(
                f"This epoch groups {len(cluster)} arcs around {', '.join(dominant_topics[:3])}, "
                f"capturing the chapter-level shift from local work into longer continuity."
            ),
            timespan_start=min(arc.timespan_start for arc in cluster),
            timespan_end=max(arc.timespan_end for arc in cluster),
            time_precision=TimePrecision.MONTH,
            importance=clamp(
                sum(arc.importance for arc in cluster) / len(cluster)
                + (0.08 if any_main else 0.0)
            ),
            confidence=clamp(sum(arc.confidence for arc in cluster) / len(cluster)),
            status=status,
            main_or_side=MainOrSide.MAIN if any_main else MainOrSide.SIDE,
            topics=dominant_topics,
            entities=entities,
            evidence_refs=[arc.id for arc in cluster],
            child_ids=[arc.id for arc in cluster],
            compression_level=3,
            epoch_theme=f"Long-range development in {', '.join(dominant_topics[:2])}.",
            major_arcs=[
                arc.id for arc in cluster if arc.main_or_side == MainOrSide.MAIN
            ]
            or [cluster[0].id],
            chapter_shift=self._make_chapter_shift(cluster),
            long_term_effects=self._collect_unique(
                [arc.arc_goal for arc in cluster if arc.arc_goal]
                + [item for arc in cluster for item in arc.obstacles]
            )[:5],
        )

    def _make_chapter_shift(self, cluster: Sequence[Arc]) -> str:
        if len(cluster) == 1:
            return "The chapter remains focused on a single sustained line of work."
        return "The chapter broadens across multiple related arcs while preserving a shared historical center."

    def _collect_unique(self, values: Sequence[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            ordered.append(normalized)
            seen.add(normalized)
        return ordered
