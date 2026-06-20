from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from .scholar import HeuristicScholarBackend, ScholarBackend
from .schema import (
    Arc,
    ArcState,
    MainOrSide,
    Scene,
    Status,
    TimePrecision,
    UTC,
    new_id,
    utc_now,
)
from .temporal_scoring import HeuristicTemporalScorer, TemporalScorer


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


@dataclass(slots=True)
class ArcDecision:
    arc_id: str
    classification_score: float
    classification_reason: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "arc_id": self.arc_id,
            "classification_score": self.classification_score,
            "classification_reason": list(self.classification_reason),
        }


class ArcBinder:
    def __init__(
        self,
        scholar_backend: ScholarBackend | None = None,
        temporal_scorer: TemporalScorer | None = None,
    ) -> None:
        self.scholar_backend = scholar_backend or HeuristicScholarBackend()
        self.temporal_scorer = temporal_scorer or HeuristicTemporalScorer()

    def bind(
        self, scenes: Sequence[Scene], reference_time: datetime | None = None
    ) -> tuple[list[Arc], list[ArcDecision]]:
        ordered = sorted(scenes, key=lambda item: item.timespan_start)
        if not ordered:
            return [], []

        clusters: list[list[Scene]] = []
        current_cluster: list[Scene] = [ordered[0]]
        for scene in ordered[1:]:
            if self._belongs_to_cluster(current_cluster[-1], scene):
                current_cluster.append(scene)
            else:
                clusters.append(current_cluster)
                current_cluster = [scene]
        clusters.append(current_cluster)

        arcs: list[Arc] = []
        decisions: list[ArcDecision] = []
        reference = reference_time.astimezone(UTC) if reference_time else utc_now()

        for cluster in clusters:
            arc, decision = self._build_arc(cluster, reference)
            arcs.append(arc)
            decisions.append(decision)
            for scene in cluster:
                if arc.id not in scene.parent_ids:
                    scene.parent_ids.append(arc.id)
                    scene.touch()

        return arcs, decisions

    def _belongs_to_cluster(self, previous: Scene, current: Scene) -> bool:
        if set(previous.topics) & set(current.topics):
            return True
        return (current.timespan_start - previous.timespan_end) <= timedelta(days=21)

    def _build_arc(
        self, cluster: Sequence[Scene], reference_time: datetime
    ) -> tuple[Arc, ArcDecision]:
        topic_counts = Counter(topic for scene in cluster for topic in scene.topics)
        dominant_topics = [topic for topic, _ in topic_counts.most_common(4)] or [
            "general"
        ]
        entity_counts = Counter(
            entity for scene in cluster for entity in scene.entities
        )
        entities = [entity for entity, _ in entity_counts.most_common(6)]

        score = self.temporal_scorer.score(cluster, reference_time)
        classification_score = score.total

        scholar_payload = self.scholar_backend.analyze_arc(
            cluster,
            classification_score=classification_score,
            reference_time=reference_time,
        )
        main_or_side = MainOrSide(
            scholar_payload.get("main_or_side", MainOrSide.UNDETERMINED.value)
        )
        status = Status(scholar_payload.get("status", Status.ACTIVE.value))
        arc_state = ArcState(scholar_payload.get("arc_state", ArcState.ACTIVE.value))
        reasons = scholar_payload.get(
            "classification_reason",
            list(score.explanation),
        )
        title = scholar_payload.get(
            "title", self._make_title(dominant_topics, main_or_side)
        )
        summary = scholar_payload.get(
            "summary",
            self._make_summary(
                cluster, dominant_topics, main_or_side, classification_score
            ),
        )
        arc_goal = scholar_payload.get(
            "arc_goal",
            f"Advance sustained work around {', '.join(dominant_topics[:2])}.",
        )
        drivers = scholar_payload.get(
            "drivers",
            self._collect_unique(
                [scene.scene_goal for scene in cluster if scene.scene_goal]
            )[:4],
        )
        obstacles = scholar_payload.get(
            "obstacles",
            self._collect_unique(
                [question for scene in cluster for question in scene.open_questions]
            )[:4],
        )
        milestones = [
            scene.id
            for scene in sorted(
                cluster, key=lambda item: item.importance, reverse=True
            )[: min(3, len(cluster))]
        ]
        turning_points = [scene.id for scene in cluster if scene.local_turning_points][
            :4
        ]
        confidence = clamp(sum(scene.confidence for scene in cluster) / len(cluster))
        importance = clamp(
            sum(scene.importance for scene in cluster) / len(cluster)
            + (0.1 if main_or_side == MainOrSide.MAIN else 0.0)
        )

        arc = Arc(
            id=new_id("arc"),
            type="arc",
            title=title,
            summary=summary,
            timespan_start=min(scene.timespan_start for scene in cluster),
            timespan_end=max(scene.timespan_end for scene in cluster),
            time_precision=TimePrecision.APPROX,
            importance=importance,
            confidence=confidence,
            status=status,
            main_or_side=main_or_side,
            topics=dominant_topics,
            entities=entities,
            evidence_refs=[scene.id for scene in cluster],
            child_ids=[scene.id for scene in cluster],
            compression_level=2,
            arc_goal=arc_goal,
            arc_state=arc_state,
            drivers=drivers,
            obstacles=obstacles,
            milestones=milestones,
            turning_points=turning_points,
        )
        return arc, ArcDecision(
            arc_id=arc.id,
            classification_score=classification_score,
            classification_reason=reasons,
        )

    def _make_title(self, dominant_topics: list[str], main_or_side: MainOrSide) -> str:
        prefix = "Mainline" if main_or_side == MainOrSide.MAIN else "Sideline"
        return f"{prefix}: {' / '.join(dominant_topics[:2])}"

    def _make_summary(
        self,
        cluster: Sequence[Scene],
        dominant_topics: list[str],
        main_or_side: MainOrSide,
        classification_score: float,
    ) -> str:
        role = "mainline" if main_or_side == MainOrSide.MAIN else "sideline"
        return (
            f"This {role} tracks {len(cluster)} scenes centered on {', '.join(dominant_topics[:3])}. "
            f"Its current structural score is {classification_score:.2f}, reflecting continuity, impact, and goal coherence."
        )

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
