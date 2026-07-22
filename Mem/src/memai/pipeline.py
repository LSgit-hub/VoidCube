from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Sequence

from .arc_binder import ArcBinder, ArcDecision
from .compression_policy import CompressionPolicy, HeuristicCompressionPolicy
from .epoch_builder import EpochBuilder
from .extraction import (
    EventExtractor,
    ProfileMemoryExtractor,
    normalize_profile_memories,
)
from .maintenance import MaintenanceExecution, MemoryMaintenanceEngine
from .query import MemoryQueryEngine
from .query_planner import QueryPlanner
from .scene_builder import SceneBuilder
from .scholar import HeuristicScholarBackend, ScholarBackend
from .schema import Arc, Epoch, Event, ProfileMemory, Scene, TranscriptTurn, UTC
from .temporal_scoring import HeuristicTemporalScorer, TemporalScorer
from .temporal import TemporalNormalizer


@dataclass(slots=True)
class PipelineResult:
    turns: list[TranscriptTurn]
    events: list[Event]
    scenes: list[Scene]
    arcs: list[Arc]
    epochs: list[Epoch]
    profile_memories: list[ProfileMemory]
    arc_decisions: list[ArcDecision]
    maintenance_engine: MemoryMaintenanceEngine = field(repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": [turn.to_dict() for turn in self.turns],
            "events": [event.to_dict() for event in self.events],
            "scenes": [scene.to_dict() for scene in self.scenes],
            "arcs": [arc.to_dict() for arc in self.arcs],
            "epochs": [epoch.to_dict() for epoch in self.epochs],
            "profile_memories": [item.to_dict() for item in self.profile_memories],
            "arc_decisions": [decision.to_dict() for decision in self.arc_decisions],
        }

    def create_query_engine(self, tier1_db_path: str | None = None) -> MemoryQueryEngine:
        return MemoryQueryEngine(
            events=self.events,
            scenes=self.scenes,
            arcs=self.arcs,
            epochs=self.epochs,
            profile_memories=self.profile_memories,
            tier1_db_path=tier1_db_path,
        )

    def create_query_planner(self) -> QueryPlanner:
        return QueryPlanner(self.create_query_engine())

    def apply_maintenance(
        self, reference_time: datetime | None = None
    ) -> MaintenanceExecution:
        return self.maintenance_engine.apply_plan(
            self.events,
            self.scenes,
            self.arcs,
            self.epochs,
            reference_time=reference_time,
        )

    def revise_memory(
        self,
        *,
        target_id: str,
        revision_type: str,
        reason: str,
        changes: dict[str, Any],
    ) -> MaintenanceExecution:
        return self.maintenance_engine.revise_by_id(
            self.events,
            self.scenes,
            self.arcs,
            self.epochs,
            target_id=target_id,
            revision_type=revision_type,
            reason=reason,
            changes=changes,
        )


class ChroniclePipeline:
    def __init__(
        self,
        temporal_normalizer: TemporalNormalizer | None = None,
        event_extractor: EventExtractor | None = None,
        scene_builder: SceneBuilder | None = None,
        arc_binder: ArcBinder | None = None,
        epoch_builder: EpochBuilder | None = None,
        scholar_backend: ScholarBackend | None = None,
        temporal_scorer: TemporalScorer | None = None,
        compression_policy: CompressionPolicy | None = None,
        profile_memory_extractor: ProfileMemoryExtractor | None = None,
    ) -> None:
        self.scholar_backend = scholar_backend or HeuristicScholarBackend()
        self.temporal_scorer = temporal_scorer or HeuristicTemporalScorer()
        self.compression_policy = compression_policy or HeuristicCompressionPolicy()
        self.temporal_normalizer = temporal_normalizer or TemporalNormalizer()
        self.event_extractor = event_extractor or EventExtractor(
            self.temporal_normalizer
        )
        self.scene_builder = scene_builder or SceneBuilder(self.scholar_backend)
        self.arc_binder = arc_binder or ArcBinder(
            self.scholar_backend,
            self.temporal_scorer,
        )
        self.epoch_builder = epoch_builder or EpochBuilder()
        self.profile_memory_extractor = (
            profile_memory_extractor or ProfileMemoryExtractor()
        )
        self.maintenance_engine = MemoryMaintenanceEngine(
            self.scholar_backend,
            self.compression_policy,
        )

    def ingest(self, turns: Sequence[TranscriptTurn]) -> PipelineResult:
        ordered_turns = sorted(turns, key=lambda item: item.timestamp)
        events = self.event_extractor.extract(ordered_turns)
        scenes = self.scene_builder.build(events)
        arcs, arc_decisions = self.arc_binder.bind(
            scenes,
            ordered_turns[-1].timestamp if ordered_turns else None,
        )
        epochs = self.epoch_builder.build(arcs)
        profile_memories = normalize_profile_memories(
            self.profile_memory_extractor.extract(
                ordered_turns,
                events,
                scenes,
            )
        )
        return PipelineResult(
            turns=list(ordered_turns),
            events=events,
            scenes=scenes,
            arcs=arcs,
            epochs=epochs,
            profile_memories=profile_memories,
            arc_decisions=arc_decisions,
            maintenance_engine=self.maintenance_engine,
        )

    def ingest_dicts(self, raw_turns: Sequence[dict[str, Any]]) -> PipelineResult:
        turns = [self._turn_from_dict(item) for item in raw_turns]
        return self.ingest(turns)

    def ingest_json_file(self, path: str | Path) -> PipelineResult:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "turns" in payload:
            raw_turns = payload["turns"]
        else:
            raw_turns = payload
        return self.ingest_dicts(raw_turns)

    def _turn_from_dict(self, payload: dict[str, Any]) -> TranscriptTurn:
        timestamp = payload["timestamp"]
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(
            UTC
        )
        return TranscriptTurn(
            turn_id=payload["turn_id"],
            speaker=payload["speaker"],
            text=payload["text"],
            timestamp=parsed,
        )
