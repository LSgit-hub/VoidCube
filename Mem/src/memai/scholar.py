from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Protocol, Sequence

from .llm_client import OpenAICompatibleLLMClient
from .schema import Arc, ArcState, BaseMemoryUnit, Event, MainOrSide, Scene, Status


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _coerce_string_list(value: Any) -> list[str] | None:
    if isinstance(value, str):
        normalized = value.strip()
        return [normalized] if normalized else None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [item for item in (_coerce_text(entry) for entry in value) if item]
        return items or None
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        return clamp(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_enum_value(enum_cls, value: Any) -> str | None:
    try:
        return enum_cls(str(value).strip().lower()).value
    except (TypeError, ValueError):
        return None


class ScholarBackend(Protocol):
    name: str

    def summarize_scene(self, events: Sequence[Event]) -> dict[str, Any]: ...

    def analyze_arc(
        self,
        scenes: Sequence[Scene],
        *,
        classification_score: float,
        reference_time: datetime,
    ) -> dict[str, Any]: ...

    def draft_revision(
        self,
        unit: BaseMemoryUnit,
        *,
        revision_type: str,
        reason: str,
        requested_changes: dict[str, Any],
    ) -> dict[str, Any]: ...


class HeuristicScholarBackend:
    name = "heuristic"

    def summarize_scene(self, events: Sequence[Event]) -> dict[str, Any]:
        topic_counts = Counter(topic for event in events for topic in event.topics)
        dominant_topics = [topic for topic, _ in topic_counts.most_common(3)] or [
            "general"
        ]
        kinds = Counter(item.event_kind.value for item in events)
        key_events = [
            item.id
            for item in sorted(events, key=lambda item: item.importance, reverse=True)[
                : min(3, len(events))
            ]
        ]
        turning_points = [
            item.id
            for item in events
            if item.event_kind.value
            in {"shift", "correction", "conflict", "completion"}
        ]
        open_questions = [
            item.summary for item in events if item.event_kind.value == "blocker"
        ][:3]

        if dominant_topics and dominant_topics[0] != "general":
            title = f"Scene: {' / '.join(dominant_topics[:2])}"
        else:
            dominant_kind = kinds.most_common(1)[0][0]
            title = f"Scene: {dominant_kind} activity"

        if any(item.event_kind.value == "decision" for item in events):
            scene_goal = (
                f"Advance or define work around {', '.join(dominant_topics[:2])}."
            )
        elif any(item.event_kind.value == "blocker" for item in events):
            scene_goal = f"Resolve blockers affecting {', '.join(dominant_topics[:2])}."
        else:
            scene_goal = f"Track local progress in {', '.join(dominant_topics[:2])}."

        return {
            "title": title,
            "summary": f"This scene captures {len(events)} memory-worthy events around {', '.join(dominant_topics[:3])}, with dominant activity in {', '.join(kind for kind, _ in kinds.most_common(3))}.",
            "scene_goal": scene_goal,
            "key_events": key_events,
            "local_turning_points": turning_points[:3],
            "open_questions": open_questions,
        }

    def analyze_arc(
        self,
        scenes: Sequence[Scene],
        *,
        classification_score: float,
        reference_time: datetime,
    ) -> dict[str, Any]:
        topic_counts = Counter(topic for scene in scenes for topic in scene.topics)
        dominant_topics = [topic for topic, _ in topic_counts.most_common(4)] or [
            "general"
        ]
        main_or_side = (
            MainOrSide.MAIN
            if classification_score >= 0.70
            else MainOrSide.SIDE
            if classification_score >= 0.40
            else MainOrSide.UNDETERMINED
        )
        latest_end = max(scene.timespan_end for scene in scenes)
        is_dormant = (reference_time - latest_end) > timedelta(days=30)
        role = "mainline" if main_or_side == MainOrSide.MAIN else "sideline"
        return {
            "main_or_side": main_or_side.value,
            "status": Status.DORMANT.value if is_dormant else Status.ACTIVE.value,
            "arc_state": "dormant" if is_dormant else "active",
            "title": f"{'Mainline' if main_or_side == MainOrSide.MAIN else 'Sideline'}: {' / '.join(dominant_topics[:2])}",
            "summary": (
                f"This {role} tracks {len(scenes)} scenes centered on {', '.join(dominant_topics[:3])}. "
                f"Its current structural score is {classification_score:.2f}, reflecting continuity, impact, and goal coherence."
            ),
            "arc_goal": f"Advance sustained work around {', '.join(dominant_topics[:2])}.",
            "drivers": self._collect_unique(
                [scene.scene_goal for scene in scenes if scene.scene_goal]
            )[:4],
            "obstacles": self._collect_unique(
                [question for scene in scenes for question in scene.open_questions]
            )[:4],
            "milestones": [
                scene.id
                for scene in sorted(
                    scenes, key=lambda item: item.importance, reverse=True
                )[: min(3, len(scenes))]
            ],
            "turning_points": [
                scene.id for scene in scenes if scene.local_turning_points
            ][:4],
        }

    def draft_revision(
        self,
        unit: BaseMemoryUnit,
        *,
        revision_type: str,
        reason: str,
        requested_changes: dict[str, Any],
    ) -> dict[str, Any]:
        title = requested_changes.get("title", unit.title)
        summary = requested_changes.get(
            "summary",
            f"{unit.summary} Revision note: {reason}",
        )
        importance = requested_changes.get("importance", unit.importance)
        confidence = requested_changes.get("confidence", unit.confidence)
        return {
            "title": title,
            "summary": summary,
            "importance": importance,
            "confidence": confidence,
        }

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


class LLMScholarBackend:
    name = "llm"

    def __init__(self, client: OpenAICompatibleLLMClient) -> None:
        self.client = client
        self.fallback = HeuristicScholarBackend()

    def summarize_scene(self, events: Sequence[Event]) -> dict[str, Any]:
        heuristic = self.fallback.summarize_scene(events)
        payload = self.client.safe_complete_json(
            task="scholar.scene",
            prompt_key="scholar.scene",
            fallback_prompt=OpenAICompatibleLLMClient.default_json_prompt(
                "summarize a memory scene from events",
                '{"title": str, "summary": str, "scene_goal": str, "open_questions": [str]}',
            ),
            user_payload={
                "events": [event.to_dict() for event in events],
                "heuristic": heuristic,
            },
        )
        merged = dict(heuristic)
        merged.update(self._sanitize_scene_payload(payload))
        return merged

    def analyze_arc(
        self,
        scenes: Sequence[Scene],
        *,
        classification_score: float,
        reference_time: datetime,
    ) -> dict[str, Any]:
        heuristic = self.fallback.analyze_arc(
            scenes,
            classification_score=classification_score,
            reference_time=reference_time,
        )
        payload = self.client.safe_complete_json(
            task="scholar.arc",
            prompt_key="scholar.arc",
            fallback_prompt=OpenAICompatibleLLMClient.default_json_prompt(
                "analyze an arc from scenes",
                '{"title": str, "summary": str, "arc_goal": str, "drivers": [str], "obstacles": [str], "classification_reason": [str]}',
            ),
            user_payload={
                "classification_score": classification_score,
                "reference_time": reference_time.isoformat(),
                "scenes": [scene.to_dict() for scene in scenes],
                "heuristic": heuristic,
            },
        )
        merged = dict(heuristic)
        merged.update(self._sanitize_arc_payload(payload))
        return merged

    def draft_revision(
        self,
        unit: BaseMemoryUnit,
        *,
        revision_type: str,
        reason: str,
        requested_changes: dict[str, Any],
    ) -> dict[str, Any]:
        heuristic = self.fallback.draft_revision(
            unit,
            revision_type=revision_type,
            reason=reason,
            requested_changes=requested_changes,
        )
        payload = self.client.safe_complete_json(
            task="scholar.revision",
            prompt_key="scholar.revision",
            fallback_prompt=OpenAICompatibleLLMClient.default_json_prompt(
                "draft a revision for an existing memory unit",
                '{"title": str, "summary": str, "importance": number, "confidence": number}',
            ),
            user_payload={
                "unit": unit.to_dict(),
                "revision_type": revision_type,
                "reason": reason,
                "requested_changes": requested_changes,
                "heuristic": heuristic,
            },
        )
        merged = dict(heuristic)
        merged.update(self._sanitize_revision_payload(payload))
        return merged

    def _sanitize_scene_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key in ("title", "summary", "scene_goal"):
            value = _coerce_text(payload.get(key))
            if value is not None:
                sanitized[key] = value
        open_questions = _coerce_string_list(payload.get("open_questions"))
        if open_questions is not None:
            sanitized["open_questions"] = open_questions
        return sanitized

    def _sanitize_arc_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key in ("title", "summary", "arc_goal"):
            value = _coerce_text(payload.get(key))
            if value is not None:
                sanitized[key] = value
        for key in ("drivers", "obstacles", "classification_reason"):
            value = _coerce_string_list(payload.get(key))
            if value is not None:
                sanitized[key] = value
        main_or_side = _coerce_enum_value(MainOrSide, payload.get("main_or_side"))
        if main_or_side is not None:
            sanitized["main_or_side"] = main_or_side
        status = _coerce_enum_value(Status, payload.get("status"))
        if status is not None:
            sanitized["status"] = status
        arc_state = _coerce_enum_value(ArcState, payload.get("arc_state"))
        if arc_state is not None:
            sanitized["arc_state"] = arc_state
        return sanitized

    def _sanitize_revision_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key in ("title", "summary"):
            value = _coerce_text(payload.get(key))
            if value is not None:
                sanitized[key] = value
        for key in ("importance", "confidence"):
            value = _coerce_float(payload.get(key))
            if value is not None:
                sanitized[key] = value
        return sanitized
