from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence

from .schema import MainOrSide, Scene, UTC


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


@dataclass(slots=True)
class TemporalScore:
    frequency: float
    duration: float
    impact: float
    goal_coherence: float
    reactivation: float
    dependency: float
    continuity_bonus: float
    tension_bonus: float
    impact_bonus: float
    total: float
    explanation: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency,
            "duration": self.duration,
            "impact": self.impact,
            "goal_coherence": self.goal_coherence,
            "reactivation": self.reactivation,
            "dependency": self.dependency,
            "continuity_bonus": self.continuity_bonus,
            "tension_bonus": self.tension_bonus,
            "impact_bonus": self.impact_bonus,
            "total": self.total,
            "explanation": list(self.explanation),
        }


@dataclass(slots=True)
class TemporalSequenceExample:
    scene_id: str
    timespan_start: datetime
    timespan_end: datetime
    topics: list[str]
    importance: float
    confidence: float
    main_or_side: str
    has_open_questions: bool
    has_turning_points: bool
    child_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "timespan_start": self.timespan_start.astimezone(UTC).isoformat(),
            "timespan_end": self.timespan_end.astimezone(UTC).isoformat(),
            "topics": list(self.topics),
            "importance": self.importance,
            "confidence": self.confidence,
            "main_or_side": self.main_or_side,
            "has_open_questions": self.has_open_questions,
            "has_turning_points": self.has_turning_points,
            "child_count": self.child_count,
        }


@dataclass(slots=True)
class TemporalSequenceRequest:
    reference_time: datetime
    scenes: list[TemporalSequenceExample]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_time": self.reference_time.astimezone(UTC).isoformat(),
            "scenes": [scene.to_dict() for scene in self.scenes],
        }


@dataclass(slots=True)
class TemporalSequencePrediction:
    frequency: float
    duration: float
    impact: float
    goal_coherence: float
    reactivation: float
    dependency: float
    continuity_bonus: float
    tension_bonus: float
    impact_bonus: float
    total: float
    explanation: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency,
            "duration": self.duration,
            "impact": self.impact,
            "goal_coherence": self.goal_coherence,
            "reactivation": self.reactivation,
            "dependency": self.dependency,
            "continuity_bonus": self.continuity_bonus,
            "tension_bonus": self.tension_bonus,
            "impact_bonus": self.impact_bonus,
            "total": self.total,
            "explanation": list(self.explanation),
        }


class TemporalScorer(Protocol):
    name: str

    def score(
        self, scenes: Sequence[Scene], reference_time: datetime
    ) -> TemporalScore: ...


class TemporalSequenceClient(Protocol):
    def score_sequence(
        self, request: TemporalSequenceRequest
    ) -> TemporalSequencePrediction | dict[str, Any]: ...


class HeuristicTemporalScorer:
    name = "heuristic"

    def score(self, scenes: Sequence[Scene], reference_time: datetime) -> TemporalScore:
        frequency = clamp(len(scenes) / 4)
        duration_days = max(
            1,
            (
                max(scene.timespan_end for scene in scenes)
                - min(scene.timespan_start for scene in scenes)
            ).days
            + 1,
        )
        duration = clamp(duration_days / 30)
        impact = clamp(sum(scene.importance for scene in scenes) / len(scenes))
        goal_coherence = self._goal_coherence(scenes)
        reactivation = clamp((len(scenes) - 1) / 3)
        dependency = clamp(sum(len(scene.child_ids) for scene in scenes) / 8)
        continuity_bonus = (
            0.15
            if any(scene.main_or_side == MainOrSide.MAIN for scene in scenes)
            else 0.0
        )
        tension_bonus = (
            0.10
            if any(
                scene.open_questions or scene.local_turning_points for scene in scenes
            )
            else 0.0
        )
        impact_bonus = 0.05 if impact >= 0.75 else 0.0
        total = clamp(
            0.25 * frequency
            + 0.20 * duration
            + 0.20 * impact
            + 0.15 * goal_coherence
            + 0.10 * reactivation
            + 0.10 * dependency
            + continuity_bonus
            + tension_bonus
            + impact_bonus
        )
        return TemporalScore(
            frequency=frequency,
            duration=duration,
            impact=impact,
            goal_coherence=goal_coherence,
            reactivation=reactivation,
            dependency=dependency,
            continuity_bonus=continuity_bonus,
            tension_bonus=tension_bonus,
            impact_bonus=impact_bonus,
            total=total,
            explanation=self._explain(scenes, total),
        )

    def _goal_coherence(self, scenes: Sequence[Scene]) -> float:
        if len(scenes) == 1:
            return 0.65
        overlaps: list[float] = []
        for left, right in zip(scenes, scenes[1:]):
            left_topics = set(left.topics)
            right_topics = set(right.topics)
            union = left_topics | right_topics
            overlaps.append(
                0.0 if not union else len(left_topics & right_topics) / len(union)
            )
        return clamp(sum(overlaps) / len(overlaps) + 0.3)

    def _explain(self, scenes: Sequence[Scene], total: float) -> list[str]:
        reasons = [f"score={total:.2f}"]
        if any(scene.main_or_side == MainOrSide.MAIN for scene in scenes):
            reasons.append("contains at least one main-priority scene")
        if len(scenes) >= 3:
            reasons.append(f"reappears across {len(scenes)} scenes")
        if any(scene.local_turning_points for scene in scenes):
            reasons.append("contains turning points")
        if any(scene.open_questions for scene in scenes):
            reasons.append("retains unresolved structural tension")
        if sum(len(scene.child_ids) for scene in scenes) >= 4:
            reasons.append("organizes multiple downstream events")
        if (
            max(scene.timespan_end for scene in scenes)
            - min(scene.timespan_start for scene in scenes)
        ).days >= 14:
            reasons.append("persists across multiple weeks")
        return reasons


class TransformerTemporalScorerAdapter:
    name = "transformer"

    def __init__(self, client: TemporalSequenceClient) -> None:
        self.client = client
        self.fallback = HeuristicTemporalScorer()

    def score(self, scenes: Sequence[Scene], reference_time: datetime) -> TemporalScore:
        request = TemporalSequenceRequest(
            reference_time=reference_time.astimezone(UTC),
            scenes=[
                TemporalSequenceExample(
                    scene_id=scene.id,
                    timespan_start=scene.timespan_start,
                    timespan_end=scene.timespan_end,
                    topics=list(scene.topics),
                    importance=scene.importance,
                    confidence=scene.confidence,
                    main_or_side=scene.main_or_side.value,
                    has_open_questions=bool(scene.open_questions),
                    has_turning_points=bool(scene.local_turning_points),
                    child_count=len(scene.child_ids),
                )
                for scene in scenes
            ],
        )
        payload = self.client.score_sequence(request)
        if isinstance(payload, TemporalSequencePrediction):
            payload_dict = payload.to_dict()
        else:
            payload_dict = payload
        heuristic = self.fallback.score(scenes, reference_time)
        return TemporalScore(
            frequency=clamp(float(payload_dict.get("frequency", heuristic.frequency))),
            duration=clamp(float(payload_dict.get("duration", heuristic.duration))),
            impact=clamp(float(payload_dict.get("impact", heuristic.impact))),
            goal_coherence=clamp(
                float(payload_dict.get("goal_coherence", heuristic.goal_coherence))
            ),
            reactivation=clamp(
                float(payload_dict.get("reactivation", heuristic.reactivation))
            ),
            dependency=clamp(
                float(payload_dict.get("dependency", heuristic.dependency))
            ),
            continuity_bonus=clamp(
                float(payload_dict.get("continuity_bonus", heuristic.continuity_bonus)),
                0.0,
                0.3,
            ),
            tension_bonus=clamp(
                float(payload_dict.get("tension_bonus", heuristic.tension_bonus)),
                0.0,
                0.2,
            ),
            impact_bonus=clamp(
                float(payload_dict.get("impact_bonus", heuristic.impact_bonus)),
                0.0,
                0.1,
            ),
            total=clamp(float(payload_dict.get("total", heuristic.total))),
            explanation=list(payload_dict.get("explanation", heuristic.explanation)),
        )
