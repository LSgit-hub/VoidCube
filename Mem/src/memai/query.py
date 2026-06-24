from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from .schema import (
    Arc,
    CertaintyState,
    Epoch,
    Event,
    MemoryKind,
    ProfileMemory,
    Scene,
    Status,
    UTC,
)


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


class MemoryQueryEngine:
    def __init__(
        self,
        *,
        events: Sequence[Event],
        scenes: Sequence[Scene],
        arcs: Sequence[Arc],
        epochs: Sequence[Epoch] | None = None,
        profile_memories: Sequence[ProfileMemory] | None = None,
        tier1_db_path: str | None = None,
    ) -> None:
        self.tier1_db_path = tier1_db_path
        self.all_events = sorted(events, key=lambda item: item.timespan_start)
        self.all_scenes = sorted(scenes, key=lambda item: item.timespan_start)
        self.all_arcs = sorted(arcs, key=lambda item: item.timespan_start)
        self.all_epochs = sorted(
            list(epochs or []), key=lambda item: item.timespan_start
        )
        self.all_profile_memories = sorted(
            list(profile_memories or []), key=lambda item: item.valid_from
        )
        self.events = [
            item for item in self.all_events if item.status != Status.SUPERSEDED
        ]
        self.scenes = [
            item for item in self.all_scenes if item.status != Status.SUPERSEDED
        ]
        self.arcs = [item for item in self.all_arcs if item.status != Status.SUPERSEDED]
        self.epochs = [
            item for item in self.all_epochs if item.status != Status.SUPERSEDED
        ]
        self.profile_memories = [
            item
            for item in self.all_profile_memories
            if item.status != Status.SUPERSEDED
        ]
        self._index = {
            item.id: item
            for item in [
                *self.all_events,
                *self.all_scenes,
                *self.all_arcs,
                *self.all_epochs,
                *self.all_profile_memories,
            ]
        }

    def profile_lookup(
        self,
        *,
        subject: str | None = None,
        memory_kind: MemoryKind | None = None,
        certainty_states: Sequence[CertaintyState] | None = None,
        include_superseded: bool = False,
        max_results: int = 10,
    ) -> dict[str, Any]:
        pool = (
            self.all_profile_memories if include_superseded else self.profile_memories
        )
        if memory_kind is not None:
            pool = [item for item in pool if item.memory_kind == memory_kind]
        if certainty_states is not None:
            allowed = set(certainty_states)
            pool = [item for item in pool if item.certainty_state in allowed]
        if subject is not None:
            pool = [
                item
                for item in pool
                if self._matches_term(subject, [item.subject, item.summary, item.value])
            ]
        ranked = sorted(
            pool,
            key=lambda item: (
                self._certainty_rank(item.certainty_state),
                item.confidence,
                item.updated_at,
            ),
            reverse=True,
        )[: max(1, max_results)]
        return {
            "result_type": "profile_lookup",
            "items": [item.to_dict() for item in ranked],
            "confidence": clamp(sum(item.confidence for item in ranked) / len(ranked))
            if ranked
            else 0.4,
            "uncertainty": None
            if ranked
            else "No stable profile memory matched the request.",
        }

    def point_query(
        self,
        when: datetime,
        *,
        include_evidence: bool = True,
        include_superseded: bool = False,
        detail_level: str = "standard",
        max_results: int = 10,
        statuses: Sequence[Status] | None = None,
    ) -> dict[str, Any]:
        target = when.astimezone(UTC)
        events_pool = self._select_units(
            self.all_events,
            self.events,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        scenes_pool = self._select_units(
            self.all_scenes,
            self.scenes,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        arcs_pool = self._select_units(
            self.all_arcs,
            self.arcs,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        events = [
            event
            for event in events_pool
            if event.timespan_start <= target <= event.timespan_end
        ]
        events = sorted(
            events,
            key=lambda item: self._point_rank(item, target),
            reverse=True,
        )
        scenes = [
            scene
            for scene in scenes_pool
            if scene.timespan_start <= target <= scene.timespan_end
        ]
        scenes = sorted(
            scenes,
            key=lambda item: self._point_rank(item, target),
            reverse=True,
        )
        related_arcs = [
            arc for arc in arcs_pool if arc.timespan_start <= target <= arc.timespan_end
        ]
        related_arcs = sorted(
            related_arcs,
            key=lambda item: self._point_rank(item, target),
            reverse=True,
        )
        event_limit = self._limit(max_results, detail_level, "event")
        evidence = (
            [event.id for event in events[:event_limit]] if include_evidence else []
        )
        stable_context = self._related_stable_context(
            unit_refs=[
                *[item.id for item in events[:event_limit]],
                *[item.id for item in scenes[:1]],
                *[item.id for item in related_arcs[:1]],
            ],
            topics=[
                topic
                for item in [*events[:event_limit], *scenes[:1], *related_arcs[:1]]
                for topic in item.topics
            ],
            entities=[
                entity
                for item in [*events[:event_limit], *scenes[:1], *related_arcs[:1]]
                for entity in item.entities
            ],
            include_superseded=include_superseded,
        )
        return {
            "result_type": "point_summary",
            "detail_level": detail_level,
            "events": [event.summary for event in events[:event_limit]],
            "local_scene": scenes[0].summary if scenes else None,
            "related_arc": related_arcs[0].summary if related_arcs else None,
            "stable_context": stable_context,
            "evidence_refs": evidence,
            "confidence": clamp(sum(event.confidence for event in events) / len(events))
            if events
            else 0.4,
            "uncertainty": None
            if (events or scenes or related_arcs)
            else "No memory units directly cover that time point.",
        }

    def range_query(
        self,
        start: datetime,
        end: datetime,
        *,
        topic: str | None = None,
        entity: str | None = None,
        include_evidence: bool = True,
        include_superseded: bool = False,
        detail_level: str = "standard",
        max_results: int = 10,
        statuses: Sequence[Status] | None = None,
    ) -> dict[str, Any]:
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        arcs_pool = self._select_units(
            self.all_arcs,
            self.arcs,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        scenes_pool = self._select_units(
            self.all_scenes,
            self.scenes,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        arcs = [
            arc
            for arc in arcs_pool
            if self._overlaps(arc.timespan_start, arc.timespan_end, start_utc, end_utc)
        ]
        scenes = [
            scene
            for scene in scenes_pool
            if self._overlaps(
                scene.timespan_start, scene.timespan_end, start_utc, end_utc
            )
        ]

        arcs = [
            arc
            for arc in arcs
            if self._matches_filters(arc, topic=topic, entity=entity)
        ]
        scenes = [
            scene
            for scene in scenes
            if self._matches_filters(scene, topic=topic, entity=entity)
        ]
        arcs = sorted(
            arcs,
            key=lambda item: self._range_rank(item, start_utc, end_utc, topic, entity),
            reverse=True,
        )
        scenes = sorted(
            scenes,
            key=lambda item: self._range_rank(item, start_utc, end_utc, topic, entity),
            reverse=True,
        )
        scene_limit = self._limit(max_results, detail_level, "scene")
        arc_limit = self._limit(max_results, detail_level, "arc")
        observed = [scene.summary for scene in scenes[:scene_limit]]
        main_arcs = [
            arc.summary for arc in arcs[:arc_limit] if arc.main_or_side.value == "main"
        ]
        side_arcs = [
            arc.summary for arc in arcs[:arc_limit] if arc.main_or_side.value != "main"
        ]
        evidence_refs = []
        if include_evidence:
            evidence_refs = [
                *[scene.id for scene in scenes[:scene_limit]],
                *[arc.id for arc in arcs[:arc_limit]],
            ]
        stable_context = self._related_stable_context(
            topic=topic,
            entity=entity,
            unit_refs=evidence_refs,
            topics=[
                topic_name
                for item in [*scenes[:scene_limit], *arcs[:arc_limit]]
                for topic_name in item.topics
            ],
            entities=[
                entity_name
                for item in [*scenes[:scene_limit], *arcs[:arc_limit]]
                for entity_name in item.entities
            ],
            include_superseded=include_superseded,
        )

        return {
            "result_type": "range_summary",
            "detail_level": detail_level,
            "observed": observed,
            "main_arcs": main_arcs,
            "side_arcs": side_arcs,
            "stable_context": stable_context,
            "turning_points": [
                scene.id for scene in scenes[:scene_limit] if scene.local_turning_points
            ],
            "open_questions": [
                question
                for scene in scenes[:scene_limit]
                for question in scene.open_questions
            ][: self._limit(max_results, detail_level, "question")],
            "evidence_refs": evidence_refs,
            "confidence": clamp(
                (sum(scene.confidence for scene in scenes) / len(scenes))
                if scenes
                else 0.4
            ),
            "uncertainty": None
            if (scenes or arcs)
            else "No structured memory matched the requested range.",
        }

    def theme_evolution(
        self,
        theme: str,
        *,
        include_evidence: bool = True,
        include_superseded: bool = False,
        detail_level: str = "standard",
        max_results: int = 10,
        statuses: Sequence[Status] | None = None,
    ) -> dict[str, Any]:
        scenes_pool = self._select_units(
            self.all_scenes,
            self.scenes,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        arcs_pool = self._select_units(
            self.all_arcs,
            self.arcs,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        scenes = [
            scene for scene in scenes_pool if self._matches_filters(scene, topic=theme)
        ]
        arcs = [arc for arc in arcs_pool if self._matches_filters(arc, topic=theme)]
        scenes = sorted(
            scenes,
            key=lambda item: (
                self._semantic_score(item, topic=theme, entity=None),
                item.timespan_start,
            ),
        )
        arcs = sorted(
            arcs,
            key=lambda item: (
                self._semantic_score(item, topic=theme, entity=None),
                item.timespan_end,
            ),
        )
        timeline_limit = self._limit(max_results, detail_level, "timeline")
        evidence_refs = []
        if include_evidence:
            evidence_refs = [
                *[arc.id for arc in arcs[:timeline_limit]],
                *[scene.id for scene in scenes[:timeline_limit]],
            ]
        stable_context = self._related_stable_context(
            topic=theme,
            unit_refs=evidence_refs,
            topics=[theme],
            entities=[
                entity_name
                for item in [*scenes[:timeline_limit], *arcs[:timeline_limit]]
                for entity_name in item.entities
            ],
            include_superseded=include_superseded,
        )
        return {
            "result_type": "theme_evolution",
            "theme": theme,
            "detail_level": detail_level,
            "timeline": [
                {
                    "time": scene.timespan_start.date().isoformat(),
                    "shift": scene.summary,
                }
                for scene in scenes[:timeline_limit]
            ],
            "active_state": arcs[-1].arc_state.value if arcs else "unknown",
            "major_turning_points": [
                scene.id
                for scene in scenes[:timeline_limit]
                if scene.local_turning_points
            ],
            "evidence_refs": evidence_refs,
            "stable_context": stable_context,
            "confidence": clamp(sum(arc.confidence for arc in arcs) / len(arcs))
            if arcs
            else 0.4,
            "uncertainty": None
            if (scenes or arcs)
            else "Theme lacks longitudinal evidence in the current memory view.",
        }

    def active_arcs(
        self,
        statuses: Sequence[Status] | None = None,
        *,
        include_superseded: bool = False,
        max_results: int = 10,
    ) -> dict[str, Any]:
        allowed = set(statuses or [Status.ACTIVE, Status.DORMANT])
        arcs_pool = self.all_arcs if include_superseded else self.arcs
        ranked = sorted(
            [arc for arc in arcs_pool if arc.status in allowed],
            key=lambda item: (item.importance, item.confidence, item.timespan_end),
            reverse=True,
        )
        return {
            "result_type": "active_arcs",
            "arcs": [arc.to_dict() for arc in ranked[: max(1, max_results)]],
        }

    def chapter_summary(
        self,
        start: datetime,
        end: datetime,
        *,
        include_evidence: bool = True,
        include_superseded: bool = False,
        detail_level: str = "standard",
        max_results: int = 10,
        statuses: Sequence[Status] | None = None,
    ) -> dict[str, Any]:
        start_utc = start.astimezone(UTC)
        end_utc = end.astimezone(UTC)
        epochs_pool = self._select_units(
            self.all_epochs,
            self.epochs,
            include_superseded=include_superseded,
            statuses=statuses,
        )
        epochs = [
            epoch
            for epoch in epochs_pool
            if self._overlaps(
                epoch.timespan_start, epoch.timespan_end, start_utc, end_utc
            )
        ]
        epochs = sorted(
            epochs,
            key=lambda item: self._range_rank(item, start_utc, end_utc, None, None),
            reverse=True,
        )
        epoch_limit = self._limit(max_results, detail_level, "epoch")
        evidence_refs = (
            [epoch.id for epoch in epochs[:epoch_limit]] if include_evidence else []
        )
        stable_context = self._related_stable_context(
            unit_refs=evidence_refs,
            topics=[
                epoch.epoch_theme for epoch in epochs[:epoch_limit] if epoch.epoch_theme
            ],
            entities=[
                entity_name
                for epoch in epochs[:epoch_limit]
                for entity_name in epoch.entities
            ],
            include_superseded=include_superseded,
        )
        return {
            "result_type": "chapter_summary",
            "detail_level": detail_level,
            "epochs": [epoch.summary for epoch in epochs[:epoch_limit]],
            "themes": [epoch.epoch_theme for epoch in epochs[:epoch_limit]],
            "chapter_shifts": [epoch.chapter_shift for epoch in epochs[:epoch_limit]],
            "evidence_refs": evidence_refs,
            "stable_context": stable_context,
            "confidence": clamp(sum(epoch.confidence for epoch in epochs) / len(epochs))
            if epochs
            else 0.4,
            "uncertainty": None
            if epochs
            else "No epoch-level history overlaps the requested range.",
        }

    def evidence_trace(
        self,
        target_id: str,
        *,
        include_superseded: bool = True,
        resolve_turns: bool = False,
    ) -> dict[str, Any]:
        target = self._index[target_id]
        if target.status == Status.SUPERSEDED and not include_superseded:
            raise KeyError(
                f"Target id {target_id} is superseded; set include_superseded to inspect historical versions."
            )
        chain: list[str] = [target.id]
        queue = list(target.child_ids)
        while queue:
            current_id = queue.pop(0)
            chain.append(current_id)
            current = self._index.get(current_id)
            if current is not None:
                queue.extend(current.child_ids)
        chain.extend(ref for ref in target.evidence_refs if ref not in chain)

        result: dict[str, Any] = {
            "result_type": "evidence_trace",
            "target_id": target.id,
            "summary": target.summary,
            "support_chain": chain,
        }

        # Resolve turn IDs to original text from Tier 1 SQLite store
        if resolve_turns and self.tier1_db_path:
            turn_texts = self._resolve_turn_texts(chain)
            if turn_texts:
                result["turn_texts"] = turn_texts

        return result

    def _resolve_turn_texts(self, refs: list[str]) -> dict[str, dict[str, Any]]:
        """Look up turn texts from Tier 1 SQLite (turns + turns_archive tables)."""
        import sqlite3
        try:
            conn = sqlite3.connect(self.tier1_db_path)
        except Exception:
            return {}
        turn_texts: dict[str, dict[str, Any]] = {}
        for ref in refs:
            row = conn.execute(
                "SELECT turn_id, speaker, text, timestamp FROM turns WHERE turn_id = ?",
                (ref,),
            ).fetchone()
            if row:
                turn_texts[ref] = {
                    "speaker": row[1],
                    "text": row[2],
                    "timestamp": row[3],
                    "source": "tier1_active",
                }
                continue
            arch_row = conn.execute(
                "SELECT turn_id, speaker, text_summary, original_text, timestamp "
                "FROM turns_archive WHERE turn_id = ?",
                (ref,),
            ).fetchone()
            if arch_row:
                turn_texts[ref] = {
                    "speaker": arch_row[1],
                    "text": arch_row[3] or arch_row[2],
                    "timestamp": arch_row[4],
                    "source": "tier1_archive",
                }
        conn.close()
        return turn_texts

    def _select_units(
        self,
        all_units: Sequence[Any],
        active_units: Sequence[Any],
        *,
        include_superseded: bool,
        statuses: Sequence[Status] | None,
    ) -> list[Any]:
        units = list(all_units if include_superseded else active_units)
        if statuses is None:
            return units
        allowed = set(statuses)
        return [item for item in units if item.status in allowed]

    def _matches_filters(
        self,
        item: Event | Scene | Arc | Epoch,
        *,
        topic: str | None = None,
        entity: str | None = None,
    ) -> bool:
        topic_match = (
            True
            if topic is None
            else self._matches_term(topic, [*item.topics, item.title, item.summary])
        )
        entity_match = (
            True
            if entity is None
            else self._matches_term(entity, [*item.entities, item.title, item.summary])
        )
        return topic_match and entity_match

    def _matches_term(self, query: str, values: Sequence[str]) -> bool:
        normalized_query = self._normalize_term(query)
        if not normalized_query:
            return False
        query_tokens = set(normalized_query.split())
        for value in values:
            normalized_value = self._normalize_term(value)
            if not normalized_value:
                continue
            if (
                normalized_query == normalized_value
                or normalized_query in normalized_value
            ):
                return True
            if query_tokens and query_tokens <= set(normalized_value.split()):
                return True
        return False

    def _normalize_term(self, value: str) -> str:
        return " ".join(value.lower().replace("-", " ").replace("_", " ").split())

    def _related_stable_context(
        self,
        *,
        topic: str | None = None,
        entity: str | None = None,
        unit_refs: Sequence[str] | None = None,
        topics: Sequence[str] | None = None,
        entities: Sequence[str] | None = None,
        include_superseded: bool,
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        pool = (
            self.all_profile_memories if include_superseded else self.profile_memories
        )
        normalized_topics = [item for item in (topics or []) if item]
        normalized_entities = [item for item in (entities or []) if item]
        refs = set(unit_refs or [])
        ranked: list[tuple[tuple[int, float, Any], ProfileMemory]] = []
        for item in pool:
            match_score = 0
            if topic and self._matches_term(
                topic, [item.summary, item.value, item.subject, item.predicate]
            ):
                match_score += 3
            if entity and self._matches_term(
                entity, [item.summary, item.value, item.subject, item.predicate]
            ):
                match_score += 3
            if any(
                self._matches_term(
                    candidate, [item.summary, item.value, item.subject, item.predicate]
                )
                for candidate in normalized_topics
            ):
                match_score += 2
            if any(
                self._matches_term(
                    candidate, [item.summary, item.value, item.subject, item.predicate]
                )
                for candidate in normalized_entities
            ):
                match_score += 2
            if refs and refs.intersection(item.parent_timeline_refs):
                match_score += 4
            if match_score <= 0:
                continue
            ranked.append(
                (
                    (
                        match_score,
                        self._certainty_rank(item.certainty_state),
                        item.confidence,
                        item.updated_at,
                    ),
                    item,
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item.to_dict() for _, item in ranked[: max(1, max_results)]]

    def _certainty_rank(self, state: CertaintyState) -> int:
        return {
            CertaintyState.CONFIRMED: 5,
            CertaintyState.OBSERVED: 4,
            CertaintyState.INFERRED: 3,
            CertaintyState.PENDING_VERIFICATION: 2,
            CertaintyState.DISPUTED: 1,
        }[state]

    def _point_rank(self, item: Event | Scene | Arc | Epoch, target: datetime) -> float:
        span_seconds = max(
            (item.timespan_end - item.timespan_start).total_seconds(),
            1.0,
        )
        if item.timespan_start <= target <= item.timespan_end:
            temporal_fit = 1.0
        else:
            distance_seconds = min(
                abs((target - item.timespan_start).total_seconds()),
                abs((target - item.timespan_end).total_seconds()),
            )
            temporal_fit = clamp(1.0 - (distance_seconds / max(span_seconds * 4, 1.0)))
        return clamp(
            0.55 * temporal_fit + 0.30 * item.importance + 0.15 * item.confidence
        )

    def _range_rank(
        self,
        item: Event | Scene | Arc | Epoch,
        start: datetime,
        end: datetime,
        topic: str | None,
        entity: str | None,
    ) -> float:
        overlap_start = max(item.timespan_start, start)
        overlap_end = min(item.timespan_end, end)
        overlap_seconds = max((overlap_end - overlap_start).total_seconds(), 0.0)
        query_seconds = max((end - start).total_seconds(), 1.0)
        temporal_overlap = clamp(overlap_seconds / query_seconds)
        structural_relevance = self._structural_relevance(item)
        semantic_similarity = self._semantic_score(item, topic=topic, entity=entity)
        recency = clamp(
            1.0
            - (
                max((end - item.timespan_end).total_seconds(), 0.0)
                / max(query_seconds * 2, 1.0)
            )
        )
        return clamp(
            0.35 * temporal_overlap
            + 0.25 * structural_relevance
            + 0.20 * item.importance
            + 0.10 * semantic_similarity
            + 0.10 * recency
        )

    def _structural_relevance(self, item: Event | Scene | Arc | Epoch) -> float:
        if isinstance(item, Arc):
            return 1.0
        if isinstance(item, Epoch):
            return 0.95
        if isinstance(item, Scene):
            return 0.8
        return 0.6

    def _semantic_score(
        self,
        item: Event | Scene | Arc | Epoch,
        *,
        topic: str | None,
        entity: str | None,
    ) -> float:
        scores: list[float] = []
        if topic is not None:
            scores.append(
                1.0
                if self._matches_term(topic, [*item.topics, item.title, item.summary])
                else 0.0
            )
        if entity is not None:
            scores.append(
                1.0
                if self._matches_term(
                    entity, [*item.entities, item.title, item.summary]
                )
                else 0.0
            )
        if not scores:
            return 0.5
        return sum(scores) / len(scores)

    def _limit(self, max_results: int, detail_level: str, result_type: str) -> int:
        base = max(1, max_results)
        if detail_level == "brief":
            if result_type in {"arc", "timeline"}:
                return min(base, 3)
            if result_type in {"scene", "event", "epoch"}:
                return min(base, 2)
            return min(base, 2)
        if detail_level == "deep":
            if result_type in {"question", "timeline"}:
                return min(base, 12)
            return min(base, 8)
        if result_type == "question":
            return min(base, 5)
        return min(base, 5)

    def _overlaps(
        self,
        left_start: datetime,
        left_end: datetime,
        right_start: datetime,
        right_end: datetime,
    ) -> bool:
        return left_start <= right_end and right_start <= left_end
