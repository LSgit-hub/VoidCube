from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta
from typing import Any, Sequence

from .arc_binder import ArcBinder
from .compression_policy import (
    CompressionActionSpec,
    CompressionPolicy,
    HeuristicCompressionPolicy,
)
from .epoch_builder import EpochBuilder
from .scene_builder import SceneBuilder
from .scholar import HeuristicScholarBackend, ScholarBackend
from .schema import (
    Arc,
    ArcState,
    BaseMemoryUnit,
    Epoch,
    Event,
    MainOrSide,
    Scene,
    Status,
    UTC,
    new_id,
    utc_now,
)


@dataclass(slots=True)
class CompressionAction:
    action_type: str
    source_ids: list[str]
    reason: str
    target_layer: str

    def to_dict(self) -> dict[str, object]:
        return {
            "action_type": self.action_type,
            "source_ids": list(self.source_ids),
            "reason": self.reason,
            "target_layer": self.target_layer,
        }


@dataclass(slots=True)
class RevisionRecord:
    revision_type: str
    target_old_id: str
    target_new_id: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "revision_type": self.revision_type,
            "target_old_id": self.target_old_id,
            "target_new_id": self.target_new_id,
            "reason": self.reason,
        }


@dataclass(slots=True)
class MaintenancePlan:
    compression_actions: list[CompressionAction]
    dormant_arc_ids: list[str]
    policy_notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "compression_actions": [
                item.to_dict() for item in self.compression_actions
            ],
            "dormant_arc_ids": list(self.dormant_arc_ids),
            "policy_notes": list(self.policy_notes),
        }


@dataclass(slots=True)
class MaintenanceExecution:
    events: list[Event]
    scenes: list[Scene]
    arcs: list[Arc]
    epochs: list[Epoch]
    plan: MaintenancePlan
    revision_records: list[RevisionRecord]

    def to_dict(self) -> dict[str, object]:
        return {
            "events": [item.to_dict() for item in self.events],
            "scenes": [item.to_dict() for item in self.scenes],
            "arcs": [item.to_dict() for item in self.arcs],
            "epochs": [item.to_dict() for item in self.epochs],
            "plan": self.plan.to_dict(),
            "revision_records": [item.to_dict() for item in self.revision_records],
        }


class MemoryMaintenanceEngine:
    def __init__(
        self,
        scholar_backend: ScholarBackend | None = None,
        compression_policy: CompressionPolicy | None = None,
    ) -> None:
        self.scholar_backend = scholar_backend or HeuristicScholarBackend()
        self.compression_policy = compression_policy or HeuristicCompressionPolicy()
        self.scene_builder = SceneBuilder(self.scholar_backend)
        self.arc_binder = ArcBinder(self.scholar_backend)
        self.epoch_builder = EpochBuilder()

    def plan(
        self,
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch] | None = None,
        reference_time: datetime | None = None,
    ) -> MaintenancePlan:
        reference = reference_time.astimezone(UTC) if reference_time else utc_now()
        policy_decision = self.compression_policy.decide(
            scenes,
            arcs,
            list(epochs or []),
            reference,
        )
        return MaintenancePlan(
            compression_actions=[
                CompressionAction(
                    action_type=item.action_type,
                    source_ids=item.source_ids,
                    reason=item.reason,
                    target_layer=item.target_layer,
                )
                for item in policy_decision.compression_actions
            ],
            dormant_arc_ids=list(policy_decision.dormant_arc_ids),
            policy_notes=list(policy_decision.notes),
        )

    def apply_plan(
        self,
        events: Sequence[Event],
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        reference_time: datetime | None = None,
    ) -> MaintenanceExecution:
        plan = self.plan(scenes, arcs, epochs, reference_time=reference_time)
        working_events = list(events)
        working_scenes = list(scenes)
        working_arcs = list(arcs)
        working_epochs = list(epochs)
        revision_records: list[RevisionRecord] = []
        affected_arc_ids: set[str] = set()
        affected_epoch_ids: set[str] = set()

        for arc_id in plan.dormant_arc_ids:
            arc = self._find_by_id(working_arcs, arc_id)
            if arc is None:
                continue
            arc.status = Status.DORMANT
            arc.arc_state = ArcState.DORMANT
            arc.touch()

        for action in plan.compression_actions:
            if action.action_type == "compress_scene":
                scene = self._find_by_id(working_scenes, action.source_ids[0])
                if scene is None or scene.status == Status.SUPERSEDED:
                    continue
                replacement, record = self.revise_unit(
                    scene,
                    revision_type="compression_upgrade",
                    reason=action.reason,
                    summary=self._compressed_scene_summary(scene),
                    evidence_refs=scene.key_events[:2] or scene.evidence_refs[:2],
                    open_questions=scene.open_questions[:1],
                )
                self._append_replacement(working_scenes, replacement)
                self._replace_parent_refs(working_events, scene.id, replacement.id)
                affected_arc_ids.update(replacement.parent_ids)
                revision_records.append(record)

            elif action.action_type == "compress_arc":
                arc = self._find_by_id(working_arcs, action.source_ids[0])
                if arc is None or arc.status == Status.SUPERSEDED:
                    continue
                replacement, record = self.revise_unit(
                    arc,
                    revision_type="compression_upgrade",
                    reason=action.reason,
                    summary=self._compressed_arc_summary(arc),
                    evidence_refs=arc.milestones[:3] or arc.evidence_refs[:3],
                    obstacles=arc.obstacles[:2],
                )
                self._append_replacement(working_arcs, replacement)
                self._replace_parent_refs(working_scenes, arc.id, replacement.id)
                affected_epoch_ids.update(replacement.parent_ids)
                revision_records.append(record)

            elif action.action_type == "compress_epoch":
                epoch = self._find_by_id(working_epochs, action.source_ids[0])
                if epoch is None or epoch.status == Status.SUPERSEDED:
                    continue
                replacement, record = self.revise_unit(
                    epoch,
                    revision_type="compression_upgrade",
                    reason=action.reason,
                    summary=self._compressed_epoch_summary(epoch),
                    evidence_refs=epoch.major_arcs[:3] or epoch.evidence_refs[:3],
                    long_term_effects=epoch.long_term_effects[:2],
                )
                self._append_replacement(working_epochs, replacement)
                self._replace_parent_refs(working_arcs, epoch.id, replacement.id)
                revision_records.append(record)

        self._cascade_refresh(
            working_events,
            working_scenes,
            working_arcs,
            working_epochs,
            scene_ids=[],
            arc_ids=affected_arc_ids,
            epoch_ids=affected_epoch_ids,
            reason="Refresh parent summaries after maintenance updates",
            revision_records=revision_records,
        )

        return MaintenanceExecution(
            events=working_events,
            scenes=working_scenes,
            arcs=working_arcs,
            epochs=working_epochs,
            plan=plan,
            revision_records=revision_records,
        )

    def revise_by_id(
        self,
        events: Sequence[Event],
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch],
        *,
        target_id: str,
        revision_type: str,
        reason: str,
        changes: dict[str, Any],
    ) -> MaintenanceExecution:
        working_events = list(events)
        working_scenes = list(scenes)
        working_arcs = list(arcs)
        working_epochs = list(epochs)
        revision_records: list[RevisionRecord] = []
        affected_scene_ids: set[str] = set()
        affected_arc_ids: set[str] = set()
        affected_epoch_ids: set[str] = set()
        empty_plan = MaintenancePlan(
            compression_actions=[],
            dormant_arc_ids=[],
            policy_notes=[
                f"policy={self.compression_policy.name}; manual_revision=true"
            ],
        )

        target = self._find_by_id(working_events, target_id)
        collection_name = "events"
        if target is None:
            target = self._find_by_id(working_scenes, target_id)
            collection_name = "scenes"
        if target is None:
            target = self._find_by_id(working_arcs, target_id)
            collection_name = "arcs"
        if target is None:
            target = self._find_by_id(working_epochs, target_id)
            collection_name = "epochs"
        if target is None:
            raise KeyError(f"Unknown memory id: {target_id}")

        replacement, record = self.revise_unit(
            target,
            revision_type=revision_type,
            reason=reason,
            **changes,
        )
        revision_records.append(record)

        if collection_name == "events":
            self._append_replacement(working_events, replacement)
            affected_scene_ids.update(replacement.parent_ids)
        elif collection_name == "scenes":
            self._append_replacement(working_scenes, replacement)
            self._replace_parent_refs(working_events, target.id, replacement.id)
            affected_arc_ids.update(replacement.parent_ids)
        elif collection_name == "arcs":
            self._append_replacement(working_arcs, replacement)
            self._replace_parent_refs(working_scenes, target.id, replacement.id)
            affected_epoch_ids.update(replacement.parent_ids)
        else:
            self._append_replacement(working_epochs, replacement)
            self._replace_parent_refs(working_arcs, target.id, replacement.id)

        self._cascade_refresh(
            working_events,
            working_scenes,
            working_arcs,
            working_epochs,
            scene_ids=affected_scene_ids,
            arc_ids=affected_arc_ids,
            epoch_ids=affected_epoch_ids,
            reason=f"Refresh parent summaries after {revision_type}",
            revision_records=revision_records,
        )

        return MaintenanceExecution(
            events=working_events,
            scenes=working_scenes,
            arcs=working_arcs,
            epochs=working_epochs,
            plan=empty_plan,
            revision_records=revision_records,
        )

    def revise_unit(
        self, unit: BaseMemoryUnit, *, revision_type: str, reason: str, **changes
    ) -> tuple[BaseMemoryUnit, RevisionRecord]:
        now = utc_now()
        drafted_changes = self.scholar_backend.draft_revision(
            unit,
            revision_type=revision_type,
            reason=reason,
            requested_changes=changes,
        )
        resolved_changes = {**changes, **drafted_changes}
        unit.status = Status.SUPERSEDED
        unit.touch()
        replacement_kwargs: dict[str, Any] = {
            "id": new_id(unit.type),
            "supersedes": [*unit.supersedes, unit.id],
            "created_at": now,
            "updated_at": now,
            "last_reviewed_at": now,
        }
        if "status" not in resolved_changes:
            replacement_kwargs["status"] = Status.ACTIVE
        replacement = replace(
            unit,
            **replacement_kwargs,
            **resolved_changes,
        )
        for item in fields(replacement):
            value = getattr(replacement, item.name)
            if isinstance(value, list):
                setattr(replacement, item.name, list(value))
        record = RevisionRecord(
            revision_type=revision_type,
            target_old_id=unit.id,
            target_new_id=replacement.id,
            reason=reason,
        )
        return replacement, record

    def _find_by_id(
        self, collection: Sequence[BaseMemoryUnit], item_id: str
    ) -> BaseMemoryUnit | None:
        return next((item for item in collection if item.id == item_id), None)

    def _append_replacement(
        self, collection: list[BaseMemoryUnit], replacement: BaseMemoryUnit
    ) -> None:
        collection.append(replacement)

    def _cascade_refresh(
        self,
        events: list[Event],
        scenes: list[Scene],
        arcs: list[Arc],
        epochs: list[Epoch],
        *,
        scene_ids: Sequence[str],
        arc_ids: Sequence[str],
        epoch_ids: Sequence[str],
        reason: str,
        revision_records: list[RevisionRecord],
    ) -> None:
        next_arc_ids = set(arc_ids)
        for scene_id in self._dedupe(scene_ids):
            replacement = self._refresh_scene(
                events,
                scenes,
                arcs,
                scene_id=scene_id,
                reason=reason,
                revision_records=revision_records,
            )
            if replacement is not None:
                next_arc_ids.update(replacement.parent_ids)

        next_epoch_ids = set(epoch_ids)
        for arc_id in self._dedupe(next_arc_ids):
            replacement = self._refresh_arc(
                scenes,
                arcs,
                epochs,
                arc_id=arc_id,
                reason=reason,
                revision_records=revision_records,
            )
            if replacement is not None:
                next_epoch_ids.update(replacement.parent_ids)

        for epoch_id in self._dedupe(next_epoch_ids):
            self._refresh_epoch(
                arcs,
                epochs,
                epoch_id=epoch_id,
                reason=reason,
                revision_records=revision_records,
            )

    def _refresh_scene(
        self,
        events: list[Event],
        scenes: list[Scene],
        arcs: list[Arc],
        *,
        scene_id: str,
        reason: str,
        revision_records: list[RevisionRecord],
    ) -> Scene | None:
        scene = self._find_current_by_id(scenes, scene_id)
        if scene is None:
            return None
        child_events = self._active_children(events, scene.child_ids)
        if not child_events:
            return None
        template = self.scene_builder._build_scene(child_events)
        changes = self._scene_refresh_changes(scene, template)
        if not self._has_material_changes(scene, changes):
            return None
        replacement, record = self.revise_unit(
            scene,
            revision_type="propagated_refresh",
            reason=reason,
            **changes,
        )
        self._append_replacement(scenes, replacement)
        self._replace_parent_refs(events, scene.id, replacement.id)
        revision_records.append(record)
        return replacement

    def _refresh_arc(
        self,
        scenes: list[Scene],
        arcs: list[Arc],
        epochs: list[Epoch],
        *,
        arc_id: str,
        reason: str,
        revision_records: list[RevisionRecord],
    ) -> Arc | None:
        arc = self._find_current_by_id(arcs, arc_id)
        if arc is None:
            return None
        child_scenes = self._active_children(scenes, arc.child_ids)
        if not child_scenes:
            return None
        template, _ = self.arc_binder._build_arc(
            child_scenes,
            self._arc_refresh_reference_time(arc, child_scenes),
        )
        changes = self._arc_refresh_changes(arc, template)
        if not self._has_material_changes(arc, changes):
            return None
        replacement, record = self.revise_unit(
            arc,
            revision_type="propagated_refresh",
            reason=reason,
            **changes,
        )
        self._append_replacement(arcs, replacement)
        self._replace_parent_refs(scenes, arc.id, replacement.id)
        revision_records.append(record)
        return replacement

    def _refresh_epoch(
        self,
        arcs: list[Arc],
        epochs: list[Epoch],
        *,
        epoch_id: str,
        reason: str,
        revision_records: list[RevisionRecord],
    ) -> Epoch | None:
        epoch = self._find_current_by_id(epochs, epoch_id)
        if epoch is None:
            return None
        child_arcs = self._active_children(arcs, epoch.child_ids)
        if not child_arcs:
            return None
        template = self.epoch_builder._build_epoch(child_arcs)
        changes = self._epoch_refresh_changes(epoch, template)
        if not self._has_material_changes(epoch, changes):
            return None
        replacement, record = self.revise_unit(
            epoch,
            revision_type="propagated_refresh",
            reason=reason,
            **changes,
        )
        self._append_replacement(epochs, replacement)
        self._replace_parent_refs(arcs, epoch.id, replacement.id)
        revision_records.append(record)
        return replacement

    def _replace_scene_refs(
        self, arcs: Sequence[Arc], old_id: str, new_id: str
    ) -> None:
        for arc in arcs:
            if arc.status == Status.SUPERSEDED:
                continue
            self._replace_id_in_list(arc.child_ids, old_id, new_id)
            self._replace_id_in_list(arc.evidence_refs, old_id, new_id)
            self._replace_id_in_list(arc.milestones, old_id, new_id)
            self._replace_id_in_list(arc.turning_points, old_id, new_id)
            if new_id in arc.child_ids or new_id in arc.evidence_refs:
                arc.touch()

    def _replace_arc_refs(
        self, epochs: Sequence[Epoch], old_id: str, new_id: str
    ) -> None:
        for epoch in epochs:
            if epoch.status == Status.SUPERSEDED:
                continue
            self._replace_id_in_list(epoch.child_ids, old_id, new_id)
            self._replace_id_in_list(epoch.evidence_refs, old_id, new_id)
            self._replace_id_in_list(epoch.major_arcs, old_id, new_id)
            if new_id in epoch.child_ids or new_id in epoch.evidence_refs:
                epoch.touch()

    def _replace_event_refs(
        self, scenes: Sequence[Scene], old_id: str, new_id: str
    ) -> None:
        for scene in scenes:
            if scene.status == Status.SUPERSEDED:
                continue
            self._replace_id_in_list(scene.child_ids, old_id, new_id)
            self._replace_id_in_list(scene.evidence_refs, old_id, new_id)
            self._replace_id_in_list(scene.key_events, old_id, new_id)
            self._replace_id_in_list(scene.local_turning_points, old_id, new_id)
            if new_id in scene.child_ids or new_id in scene.evidence_refs:
                scene.touch()

    def _replace_parent_refs(
        self, children: Sequence[BaseMemoryUnit], old_id: str, new_id: str
    ) -> None:
        for child in children:
            if child.status == Status.SUPERSEDED:
                continue
            before = list(child.parent_ids)
            self._replace_id_in_list(child.parent_ids, old_id, new_id)
            if child.parent_ids != before:
                child.touch()

    def _replace_id_in_list(self, values: list[str], old_id: str, new_id: str) -> None:
        for index, value in enumerate(list(values)):
            if value == old_id:
                values[index] = new_id

    def _find_current_by_id(
        self, collection: Sequence[BaseMemoryUnit], item_id: str
    ) -> BaseMemoryUnit | None:
        item = self._find_by_id(collection, item_id)
        if item is None or item.status == Status.SUPERSEDED:
            return None
        return item

    def _active_children(
        self, collection: Sequence[BaseMemoryUnit], child_ids: Sequence[str]
    ) -> list[BaseMemoryUnit]:
        resolved: list[BaseMemoryUnit] = []
        for child_id in child_ids:
            replacement = self._resolve_active_unit(collection, child_id)
            if replacement is not None:
                resolved.append(replacement)
        return resolved

    def _resolve_active_unit(
        self, collection: Sequence[BaseMemoryUnit], item_id: str
    ) -> BaseMemoryUnit | None:
        direct = self._find_by_id(collection, item_id)
        if direct is not None and direct.status != Status.SUPERSEDED:
            return direct
        for item in collection:
            if item.status == Status.SUPERSEDED:
                continue
            if item_id in item.supersedes:
                return item
        return None

    def _has_material_changes(
        self, unit: BaseMemoryUnit, changes: dict[str, Any]
    ) -> bool:
        return any(getattr(unit, key) != value for key, value in changes.items())

    def _scene_refresh_changes(
        self, scene: Scene, template: Scene
    ) -> dict[str, Any]:
        return {
            "title": template.title,
            "summary": template.summary,
            "timespan_start": template.timespan_start,
            "timespan_end": template.timespan_end,
            "time_precision": template.time_precision,
            "importance": template.importance,
            "confidence": template.confidence,
            "status": scene.status,
            "main_or_side": template.main_or_side,
            "topics": list(template.topics),
            "entities": list(template.entities),
            "evidence_refs": list(template.evidence_refs),
            "child_ids": list(template.child_ids),
            "scene_goal": template.scene_goal,
            "key_events": list(template.key_events),
            "local_turning_points": list(template.local_turning_points),
            "open_questions": list(template.open_questions),
        }

    def _arc_refresh_changes(self, arc: Arc, template: Arc) -> dict[str, Any]:
        status = arc.status if arc.status != Status.ACTIVE else template.status
        arc_state = arc.arc_state if arc.status != Status.ACTIVE else template.arc_state
        return {
            "title": template.title,
            "summary": template.summary,
            "timespan_start": template.timespan_start,
            "timespan_end": template.timespan_end,
            "time_precision": template.time_precision,
            "importance": template.importance,
            "confidence": template.confidence,
            "status": status,
            "main_or_side": template.main_or_side,
            "topics": list(template.topics),
            "entities": list(template.entities),
            "evidence_refs": list(template.evidence_refs),
            "child_ids": list(template.child_ids),
            "arc_goal": template.arc_goal,
            "arc_state": arc_state,
            "drivers": list(template.drivers),
            "obstacles": list(template.obstacles),
            "milestones": list(template.milestones),
            "turning_points": list(template.turning_points),
        }

    def _epoch_refresh_changes(self, epoch: Epoch, template: Epoch) -> dict[str, Any]:
        return {
            "title": template.title,
            "summary": template.summary,
            "timespan_start": template.timespan_start,
            "timespan_end": template.timespan_end,
            "time_precision": template.time_precision,
            "importance": template.importance,
            "confidence": template.confidence,
            "status": template.status,
            "main_or_side": template.main_or_side,
            "topics": list(template.topics),
            "entities": list(template.entities),
            "evidence_refs": list(template.evidence_refs),
            "child_ids": list(template.child_ids),
            "epoch_theme": template.epoch_theme,
            "major_arcs": list(template.major_arcs),
            "chapter_shift": template.chapter_shift,
            "long_term_effects": list(template.long_term_effects),
        }

    def _arc_refresh_reference_time(
        self, arc: Arc, child_scenes: Sequence[Scene]
    ) -> datetime:
        latest_child_end = max(scene.timespan_end for scene in child_scenes)
        return max(arc.timespan_end, latest_child_end)

    def _dedupe(self, values: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered

    def _compressed_scene_summary(self, scene: Scene) -> str:
        topic_text = ", ".join(scene.topics[:3]) or "general work"
        return (
            f"Compressed historical scene covering {topic_text}. "
            f"Primary goal: {scene.scene_goal or 'preserve local continuity'}"
        )

    def _compressed_arc_summary(self, arc: Arc) -> str:
        topic_text = ", ".join(arc.topics[:3]) or "general work"
        return (
            f"Compressed historical arc for {topic_text}. "
            f"State: {arc.arc_state.value}. Goal: {arc.arc_goal or 'maintain longitudinal continuity'}"
        )

    def _compressed_epoch_summary(self, epoch: Epoch) -> str:
        topic_text = ", ".join(epoch.topics[:3]) or "general history"
        return (
            f"Compressed historical epoch centered on {topic_text}. "
            f"Chapter shift: {epoch.chapter_shift or 'preserved as high-level history'}"
        )
