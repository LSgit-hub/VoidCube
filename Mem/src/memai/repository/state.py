from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import json
from pathlib import Path
from typing import Any, Sequence

from ..arc_binder import ArcDecision
from ..diffing import MemoryDiffEngine, MemoryDiffReport
from ..extraction import normalize_profile_memories
from ..pipeline import ChroniclePipeline, PipelineResult
from ..schema import (
    Arc,
    ArcState,
    CertaintyState,
    Epoch,
    Event,
    EventKind,
    ImpactScope,
    MainOrSide,
    MemoryKind,
    ProfileMemory,
    Scene,
    Status,
    TimePrecision,
    TranscriptTurn,
    parse_datetime,
)


@dataclass(slots=True)
class MemoryState:
    version: int
    result: PipelineResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "result": self.result.to_dict(),
        }


@dataclass(slots=True)
class MemoryStateUpdate:
    state: MemoryState
    diff: MemoryDiffReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "diff": self.diff.to_dict(),
        }


class MemoryStateRepository:
    def __init__(
        self,
        pipeline: ChroniclePipeline | None = None,
        *,
        incremental_lookback_days: int = 14,
    ) -> None:
        self.pipeline = pipeline or ChroniclePipeline()
        self.incremental_lookback_days = incremental_lookback_days
        self.diff_engine = MemoryDiffEngine()

    def initialize_from_transcript(
        self, state_path: str | Path, transcript_path: str | Path
    ) -> MemoryState:
        result = self.pipeline.ingest_json_file(transcript_path)
        state = MemoryState(version=1, result=result)
        self.save(state_path, state)
        return state

    def update_from_transcript(
        self, state_path: str | Path, transcript_path: str | Path
    ) -> MemoryState:
        return self.update_with_report(state_path, transcript_path).state

    def update_with_report(
        self, state_path: str | Path, transcript_path: str | Path
    ) -> MemoryStateUpdate:
        existing = self.load(state_path)
        payload = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
        raw_turns = (
            payload["turns"]
            if isinstance(payload, dict) and "turns" in payload
            else payload
        )
        incoming_turns = [self._turn_from_dict(item) for item in raw_turns]
        known_ids = {turn.turn_id for turn in existing.result.turns}
        new_turns = [turn for turn in incoming_turns if turn.turn_id not in known_ids]
        if not new_turns:
            empty_diff = self.diff_engine.compare(existing.result, existing.result)
            return MemoryStateUpdate(state=existing, diff=empty_diff)

        merged_turns = sorted(
            [*existing.result.turns, *new_turns], key=lambda item: item.timestamp
        )
        result = self._incremental_update(existing.result, merged_turns, new_turns)
        state = MemoryState(version=existing.version + 1, result=result)
        self.save(state_path, state)
        diff = self.diff_engine.compare(existing.result, result)
        return MemoryStateUpdate(state=state, diff=diff)

    def _incremental_update(
        self,
        existing: PipelineResult,
        merged_turns: Sequence[TranscriptTurn],
        new_turns: Sequence[TranscriptTurn],
    ) -> PipelineResult:
        earliest_new_time = min(turn.timestamp for turn in new_turns)
        latest_new_time = max(turn.timestamp for turn in new_turns)
        context = timedelta(days=self.incremental_lookback_days)
        affected_start = earliest_new_time - context
        affected_end = latest_new_time + context

        directly_affected_event_ids = {
            event.id
            for event in existing.events
            if self._overlaps(
                event.timespan_start, event.timespan_end, affected_start, affected_end
            )
        }
        affected_scene_ids = {
            scene.id
            for scene in existing.scenes
            if self._overlaps(
                scene.timespan_start, scene.timespan_end, affected_start, affected_end
            )
            or any(
                child_id in directly_affected_event_ids for child_id in scene.child_ids
            )
        }
        affected_arc_ids = {
            arc.id
            for arc in existing.arcs
            if self._overlaps(
                arc.timespan_start, arc.timespan_end, affected_start, affected_end
            )
            or any(child_id in affected_scene_ids for child_id in arc.child_ids)
        }
        affected_scene_ids.update(
            scene_id
            for arc in existing.arcs
            if arc.id in affected_arc_ids
            for scene_id in arc.child_ids
        )
        affected_epoch_ids = {
            epoch.id
            for epoch in existing.epochs
            if self._overlaps(
                epoch.timespan_start, epoch.timespan_end, affected_start, affected_end
            )
            or any(child_id in affected_arc_ids for child_id in epoch.child_ids)
        }
        affected_event_ids = {
            event.id
            for event in existing.events
            if event.id in directly_affected_event_ids
        }
        affected_event_ids.update(
            event_id
            for scene in existing.scenes
            if scene.id in affected_scene_ids
            for event_id in scene.child_ids
        )

        affected_turn_ids = {turn.turn_id for turn in new_turns}
        affected_turn_ids.update(
            source_turn
            for event in existing.events
            if event.id in affected_event_ids
            for source_turn in event.source_turns
        )

        rebuild_turns = [
            turn
            for turn in merged_turns
            if turn.turn_id in affected_turn_ids
            or affected_start <= turn.timestamp <= affected_end
        ]
        rebuilt = self.pipeline.ingest(rebuild_turns)

        retained_events = [
            event for event in existing.events if event.id not in affected_event_ids
        ]
        retained_scenes = [
            scene for scene in existing.scenes if scene.id not in affected_scene_ids
        ]
        retained_arcs = [arc for arc in existing.arcs if arc.id not in affected_arc_ids]
        retained_epochs = [
            epoch for epoch in existing.epochs if epoch.id not in affected_epoch_ids
        ]
        retained_arc_ids = {arc.id for arc in retained_arcs}
        retained_decisions = [
            decision
            for decision in existing.arc_decisions
            if decision.arc_id in retained_arc_ids
        ]
        merged_profile_memories: dict[tuple[str, str, str, str], ProfileMemory] = {}
        for item in [*existing.profile_memories, *rebuilt.profile_memories]:
            key = (
                item.memory_kind.value,
                item.subject.lower(),
                item.predicate.lower(),
                item.value.lower(),
            )
            merged_profile_memories[key] = item

        return PipelineResult(
            turns=list(merged_turns),
            events=sorted(
                [*retained_events, *rebuilt.events],
                key=lambda item: item.timespan_start,
            ),
            scenes=sorted(
                [*retained_scenes, *rebuilt.scenes],
                key=lambda item: item.timespan_start,
            ),
            arcs=sorted(
                [*retained_arcs, *rebuilt.arcs], key=lambda item: item.timespan_start
            ),
            epochs=sorted(
                [*retained_epochs, *rebuilt.epochs],
                key=lambda item: item.timespan_start,
            ),
            profile_memories=sorted(
                normalize_profile_memories(list(merged_profile_memories.values())),
                key=lambda item: item.valid_from,
            ),
            arc_decisions=[*retained_decisions, *rebuilt.arc_decisions],
            maintenance_engine=self.pipeline.maintenance_engine,
        )

    def _overlaps(self, left_start, left_end, right_start, right_end) -> bool:
        return left_start <= right_end and right_start <= left_end

    def save(self, state_path: str | Path, state: MemoryState) -> None:
        target = Path(state_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load(self, state_path: str | Path) -> MemoryState:
        payload = json.loads(Path(state_path).read_text(encoding="utf-8"))
        return MemoryState(
            version=payload["version"], result=self._result_from_dict(payload["result"])
        )

    def _result_from_dict(self, payload: dict[str, Any]) -> PipelineResult:
        return PipelineResult(
            turns=[self._turn_from_dict(item) for item in payload.get("turns", [])],
            events=[self._event_from_dict(item) for item in payload.get("events", [])],
            scenes=[self._scene_from_dict(item) for item in payload.get("scenes", [])],
            arcs=[self._arc_from_dict(item) for item in payload.get("arcs", [])],
            epochs=[self._epoch_from_dict(item) for item in payload.get("epochs", [])],
            profile_memories=[
                self._profile_memory_from_dict(item)
                for item in payload.get("profile_memories", [])
            ],
            arc_decisions=[
                self._arc_decision_from_dict(item)
                for item in payload.get("arc_decisions", [])
            ],
            maintenance_engine=self.pipeline.maintenance_engine,
        )

    def _common_kwargs(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": payload["id"],
            "type": payload["type"],
            "title": payload["title"],
            "summary": payload["summary"],
            "timespan_start": parse_datetime(payload["timespan_start"]),
            "timespan_end": parse_datetime(payload["timespan_end"]),
            "time_precision": TimePrecision(payload["time_precision"]),
            "importance": payload["importance"],
            "confidence": payload["confidence"],
            "status": Status(payload["status"]),
            "main_or_side": MainOrSide(payload["main_or_side"]),
            "topics": list(payload.get("topics", [])),
            "entities": list(payload.get("entities", [])),
            "evidence_refs": list(payload.get("evidence_refs", [])),
            "parent_ids": list(payload.get("parent_ids", [])),
            "child_ids": list(payload.get("child_ids", [])),
            "supersedes": list(payload.get("supersedes", [])),
            "compression_level": payload.get("compression_level", 0),
            "created_at": parse_datetime(payload["created_at"]),
            "updated_at": parse_datetime(payload["updated_at"]),
            "last_reviewed_at": parse_datetime(payload["last_reviewed_at"]),
        }

    def _turn_from_dict(self, payload: dict[str, Any]) -> TranscriptTurn:
        return TranscriptTurn(
            turn_id=payload["turn_id"],
            speaker=payload["speaker"],
            text=payload["text"],
            timestamp=parse_datetime(payload["timestamp"]),
        )

    def _event_from_dict(self, payload: dict[str, Any]) -> Event:
        return Event(
            **self._common_kwargs(payload),
            event_kind=EventKind(payload["event_kind"]),
            novelty=payload.get("novelty", 0.5),
            impact_scope=ImpactScope(
                payload.get("impact_scope", ImpactScope.LOCAL.value)
            ),
            source_turns=list(payload.get("source_turns", [])),
        )

    def _scene_from_dict(self, payload: dict[str, Any]) -> Scene:
        return Scene(
            **self._common_kwargs(payload),
            scene_goal=payload.get("scene_goal", ""),
            key_events=list(payload.get("key_events", [])),
            local_turning_points=list(payload.get("local_turning_points", [])),
            open_questions=list(payload.get("open_questions", [])),
        )

    def _arc_from_dict(self, payload: dict[str, Any]) -> Arc:
        return Arc(
            **self._common_kwargs(payload),
            arc_goal=payload.get("arc_goal", ""),
            arc_state=ArcState(payload.get("arc_state", ArcState.EMERGING.value)),
            drivers=list(payload.get("drivers", [])),
            obstacles=list(payload.get("obstacles", [])),
            milestones=list(payload.get("milestones", [])),
            turning_points=list(payload.get("turning_points", [])),
        )

    def _epoch_from_dict(self, payload: dict[str, Any]) -> Epoch:
        return Epoch(
            **self._common_kwargs(payload),
            epoch_theme=payload.get("epoch_theme", ""),
            major_arcs=list(payload.get("major_arcs", [])),
            chapter_shift=payload.get("chapter_shift", ""),
            long_term_effects=list(payload.get("long_term_effects", [])),
        )

    def _arc_decision_from_dict(self, payload: dict[str, Any]) -> ArcDecision:
        return ArcDecision(
            arc_id=payload["arc_id"],
            classification_score=payload["classification_score"],
            classification_reason=list(payload.get("classification_reason", [])),
        )

    def _profile_memory_from_dict(self, payload: dict[str, Any]) -> ProfileMemory:
        valid_to = payload.get("valid_to")
        return ProfileMemory(
            id=payload["id"],
            type=payload["type"],
            memory_kind=MemoryKind(payload["memory_kind"]),
            subject=payload["subject"],
            predicate=payload["predicate"],
            value=payload["value"],
            summary=payload["summary"],
            confidence=payload["confidence"],
            certainty_state=CertaintyState(payload["certainty_state"]),
            status=Status(payload.get("status", Status.ACTIVE.value)),
            valid_from=parse_datetime(payload["valid_from"]),
            valid_to=parse_datetime(valid_to) if valid_to else None,
            evidence_refs=list(payload.get("evidence_refs", [])),
            source_turns=list(payload.get("source_turns", [])),
            parent_timeline_refs=list(payload.get("parent_timeline_refs", [])),
            supersedes=list(payload.get("supersedes", [])),
            conflict_refs=list(payload.get("conflict_refs", [])),
            created_at=parse_datetime(payload["created_at"]),
            updated_at=parse_datetime(payload["updated_at"]),
            last_reviewed_at=parse_datetime(payload["last_reviewed_at"]),
        )
