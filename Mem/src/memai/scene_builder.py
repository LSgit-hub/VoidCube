from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Sequence

from .scholar import HeuristicScholarBackend, ScholarBackend
from .schema import Event, EventKind, MainOrSide, Scene, TemporalSpan, TimePrecision


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


class SceneBuilder:
    def __init__(self, scholar_backend: ScholarBackend | None = None) -> None:
        self.scholar_backend = scholar_backend or HeuristicScholarBackend()

    def build(self, events: Sequence[Event]) -> list[Scene]:
        ordered = sorted(events, key=lambda item: item.timespan_start)
        if not ordered:
            return []

        clusters: list[list[Event]] = []
        current_cluster: list[Event] = [ordered[0]]
        for event in ordered[1:]:
            if self._belongs_to_cluster(current_cluster[-1], event):
                current_cluster.append(event)
            else:
                clusters.append(current_cluster)
                current_cluster = [event]
        clusters.append(current_cluster)

        scenes = [self._build_scene(cluster) for cluster in clusters]

        for scene in scenes:
            for event in ordered:
                if event.id in scene.child_ids and scene.id not in event.parent_ids:
                    event.parent_ids.append(scene.id)
                    event.touch()

        return scenes

    def _belongs_to_cluster(self, previous: Event, current: Event) -> bool:
        previous_day = previous.timespan_start.date()
        current_day = current.timespan_start.date()
        if previous_day != current_day:
            return False

        overlap = set(previous.topics) & set(current.topics)
        if overlap:
            return True

        return (current.timespan_start - previous.timespan_end) <= timedelta(hours=6)

    def _build_scene(self, cluster: Sequence[Event]) -> Scene:
        start = min(item.timespan_start for item in cluster)
        end = max(item.timespan_end for item in cluster)
        topic_counts = Counter(topic for event in cluster for topic in event.topics)
        dominant_topics = [topic for topic, _ in topic_counts.most_common(3)] or [
            "general"
        ]
        entity_counts = Counter(
            entity for event in cluster for entity in event.entities
        )
        entities = [entity for entity, _ in entity_counts.most_common(5)]
        importance = clamp(
            sum(item.importance for item in cluster) / len(cluster)
            + min(len(cluster) * 0.03, 0.09)
        )
        confidence = clamp(sum(item.confidence for item in cluster) / len(cluster))
        main_or_side = (
            MainOrSide.MAIN
            if any(item.main_or_side == MainOrSide.MAIN for item in cluster)
            else MainOrSide.SIDE
        )
        scholar_payload = self.scholar_backend.summarize_scene(cluster)
        title = scholar_payload.get("title", self._make_title(dominant_topics, cluster))
        summary = scholar_payload.get(
            "summary", self._make_summary(dominant_topics, cluster)
        )
        key_events = scholar_payload.get(
            "key_events",
            [
                item.id
                for item in sorted(
                    cluster, key=lambda item: item.importance, reverse=True
                )[: min(3, len(cluster))]
            ],
        )
        turning_points = scholar_payload.get(
            "local_turning_points",
            [
                item.id
                for item in cluster
                if item.event_kind
                in {
                    EventKind.SHIFT,
                    EventKind.CORRECTION,
                    EventKind.CONFLICT,
                    EventKind.COMPLETION,
                }
            ],
        )
        open_questions = scholar_payload.get(
            "open_questions",
            [item.summary for item in cluster if item.event_kind == EventKind.BLOCKER][
                :3
            ],
        )
        scene_goal = scholar_payload.get(
            "scene_goal", self._infer_goal(dominant_topics, cluster)
        )

        scene = Scene.create(
            title=title,
            summary=summary,
            timespan=TemporalSpan(
                start=start,
                end=end,
                precision=self._infer_precision(start, end),
                confidence=confidence,
                source_text="scene_cluster",
            ),
            importance=importance,
            confidence=confidence,
            main_or_side=main_or_side,
            topics=dominant_topics,
            entities=entities,
            evidence_refs=[ref for event in cluster for ref in event.evidence_refs],
            child_ids=[item.id for item in cluster],
            scene_goal=scene_goal,
            key_events=key_events,
            local_turning_points=turning_points[:3],
            open_questions=open_questions,
        )
        return scene

    def _infer_precision(self, start, end) -> TimePrecision:
        span_days = max(1, (end - start).days + 1)
        if span_days >= 28:
            return TimePrecision.MONTH
        if span_days >= 7:
            return TimePrecision.WEEK
        return TimePrecision.DAY

    def _make_title(self, dominant_topics: list[str], cluster: Sequence[Event]) -> str:
        if dominant_topics and dominant_topics[0] != "general":
            return f"Scene: {' / '.join(dominant_topics[:2])}"
        dominant_kind = Counter(item.event_kind.value for item in cluster).most_common(
            1
        )[0][0]
        return f"Scene: {dominant_kind} activity"

    def _make_summary(
        self, dominant_topics: list[str], cluster: Sequence[Event]
    ) -> str:
        kinds = Counter(item.event_kind.value for item in cluster)
        kind_text = ", ".join(kind for kind, _ in kinds.most_common(3))
        topic_text = ", ".join(dominant_topics[:3])
        return f"This scene captures {len(cluster)} memory-worthy events around {topic_text}, with dominant activity in {kind_text}."

    def _infer_goal(self, dominant_topics: list[str], cluster: Sequence[Event]) -> str:
        if any(item.event_kind == EventKind.DECISION for item in cluster):
            return f"Advance or define work around {', '.join(dominant_topics[:2])}."
        if any(item.event_kind == EventKind.BLOCKER for item in cluster):
            return f"Resolve blockers affecting {', '.join(dominant_topics[:2])}."
        return f"Track local progress in {', '.join(dominant_topics[:2])}."
