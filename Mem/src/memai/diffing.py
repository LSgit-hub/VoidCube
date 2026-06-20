from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .pipeline import PipelineResult
from .schema import Arc, Epoch, MainOrSide, Status


@dataclass(slots=True)
class MemoryDiffReport:
    added_event_ids: list[str]
    added_scene_ids: list[str]
    added_arc_ids: list[str]
    added_epoch_ids: list[str]
    activated_arc_ids: list[str]
    dormant_arc_ids: list[str]
    promoted_mainline_arc_ids: list[str]
    new_topics: list[str]
    chapter_shift_epoch_ids: list[str]
    line_change_records: list[dict[str, str]]
    mainline_report: dict[str, list[str] | str]
    arc_change_explanations: list[str]
    epoch_change_explanations: list[str]
    summary_lines: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "added_event_ids": list(self.added_event_ids),
            "added_scene_ids": list(self.added_scene_ids),
            "added_arc_ids": list(self.added_arc_ids),
            "added_epoch_ids": list(self.added_epoch_ids),
            "activated_arc_ids": list(self.activated_arc_ids),
            "dormant_arc_ids": list(self.dormant_arc_ids),
            "promoted_mainline_arc_ids": list(self.promoted_mainline_arc_ids),
            "new_topics": list(self.new_topics),
            "chapter_shift_epoch_ids": list(self.chapter_shift_epoch_ids),
            "line_change_records": [dict(item) for item in self.line_change_records],
            "mainline_report": dict(self.mainline_report),
            "arc_change_explanations": list(self.arc_change_explanations),
            "epoch_change_explanations": list(self.epoch_change_explanations),
            "summary_lines": list(self.summary_lines),
        }


class MemoryDiffEngine:
    def compare(
        self, previous: PipelineResult, current: PipelineResult
    ) -> MemoryDiffReport:
        prev_events = self._active_index(previous.events)
        prev_scenes = self._active_index(previous.scenes)
        prev_arcs = self._active_index(previous.arcs)
        prev_epochs = self._active_index(previous.epochs)

        curr_events = self._active_index(current.events)
        curr_scenes = self._active_index(current.scenes)
        curr_arcs = self._active_index(current.arcs)
        curr_epochs = self._active_index(current.epochs)

        event_matches = self._match_units(
            prev_events.values(),
            curr_events.values(),
            allow_signature_fallback=False,
        )
        scene_matches = self._match_units(
            prev_scenes.values(),
            curr_scenes.values(),
            allow_signature_fallback=False,
        )
        arc_matches = self._match_units(prev_arcs.values(), curr_arcs.values())
        epoch_matches = self._match_units(prev_epochs.values(), curr_epochs.values())

        added_event_ids = sorted(
            item.id
            for item in curr_events.values()
            if item.id not in event_matches
            and item.id not in prev_events
        )
        added_scene_ids = sorted(
            item.id
            for item in curr_scenes.values()
            if item.id not in scene_matches
            and item.id not in prev_scenes
        )
        added_arc_ids = sorted(
            item.id
            for item in curr_arcs.values()
            if item.id not in arc_matches
            and item.id not in prev_arcs
        )
        added_epoch_ids = sorted(
            item.id
            for item in curr_epochs.values()
            if item.id not in epoch_matches
            and item.id not in prev_epochs
        )

        activated_arc_ids = sorted(
            current_arc.id
            for current_arc in curr_arcs.values()
            if current_arc.id in arc_matches
            and arc_matches[current_arc.id].status != Status.ACTIVE
            and current_arc.status == Status.ACTIVE
        )
        dormant_arc_ids = sorted(
            current_arc.id
            for current_arc in curr_arcs.values()
            if current_arc.id in arc_matches
            and arc_matches[current_arc.id].status != Status.DORMANT
            and current_arc.status == Status.DORMANT
        )
        promoted_mainline_arc_ids = sorted(
            current_arc.id
            for current_arc in curr_arcs.values()
            if current_arc.id in arc_matches
            and arc_matches[current_arc.id].main_or_side != MainOrSide.MAIN
            and current_arc.main_or_side == MainOrSide.MAIN
        )

        prev_topics = {
            topic
            for item in [
                *prev_events.values(),
                *prev_scenes.values(),
                *prev_arcs.values(),
                *prev_epochs.values(),
            ]
            for topic in item.topics
        }
        curr_topics = {
            topic
            for item in [
                *curr_events.values(),
                *curr_scenes.values(),
                *curr_arcs.values(),
                *curr_epochs.values(),
            ]
            for topic in item.topics
        }
        new_topics = sorted(curr_topics - prev_topics)

        chapter_shift_epoch_ids = sorted(
            current_epoch.id
            for current_epoch in curr_epochs.values()
            if current_epoch.id in epoch_matches
            and epoch_matches[current_epoch.id].chapter_shift != current_epoch.chapter_shift
        )

        arc_change_explanations = self._describe_arc_changes(
            curr_arcs,
            arc_matches,
        )
        epoch_change_explanations = self._describe_epoch_changes(
            curr_epochs,
            epoch_matches,
        )
        line_change_records = self._line_change_records(
            curr_arcs,
            arc_matches,
            curr_epochs,
            epoch_matches,
        )
        summary_lines = self._summarize(
            added_event_ids=added_event_ids,
            added_scene_ids=added_scene_ids,
            added_arc_ids=added_arc_ids,
            added_epoch_ids=added_epoch_ids,
            line_change_records=line_change_records,
            arc_change_explanations=arc_change_explanations,
            epoch_change_explanations=epoch_change_explanations,
            new_topics=new_topics,
        )
        mainline_report = self._mainline_report(line_change_records)

        return MemoryDiffReport(
            added_event_ids=added_event_ids,
            added_scene_ids=added_scene_ids,
            added_arc_ids=added_arc_ids,
            added_epoch_ids=added_epoch_ids,
            activated_arc_ids=activated_arc_ids,
            dormant_arc_ids=dormant_arc_ids,
            promoted_mainline_arc_ids=promoted_mainline_arc_ids,
            new_topics=new_topics,
            chapter_shift_epoch_ids=chapter_shift_epoch_ids,
            line_change_records=line_change_records,
            mainline_report=mainline_report,
            arc_change_explanations=arc_change_explanations,
            epoch_change_explanations=epoch_change_explanations,
            summary_lines=summary_lines,
        )

    def _active_index(self, items: Sequence[Any]) -> dict[str, Any]:
        return {
            item.id: item for item in items if item.status != Status.SUPERSEDED
        }

    def _topic_signature(self, topics: Sequence[str]) -> str:
        return "|".join(sorted(topics[:4])) or "general"

    def _lineage_tokens(self, item: Any) -> set[str]:
        return {item.id, *item.supersedes}

    def _match_units(
        self,
        previous: Sequence[Any],
        current: Sequence[Any],
        *,
        allow_signature_fallback: bool = True,
    ) -> dict[str, Any]:
        prev_by_id = {item.id: item for item in previous}
        remaining_prev_ids = set(prev_by_id)
        matches: dict[str, Any] = {}

        lineage_index: dict[str, list[Any]] = {}
        for item in previous:
            for token in self._lineage_tokens(item):
                lineage_index.setdefault(token, []).append(item)

        unmatched_current: list[Any] = []
        for item in current:
            matched = self._best_lineage_match(item, lineage_index, prev_by_id, remaining_prev_ids)
            if matched is None:
                unmatched_current.append(item)
                continue
            matches[item.id] = matched
            remaining_prev_ids.discard(matched.id)

        if not allow_signature_fallback:
            return matches

        signature_buckets: dict[str, list[Any]] = {}
        for prev_id in remaining_prev_ids:
            previous_item = prev_by_id[prev_id]
            signature_buckets.setdefault(
                self._topic_signature(previous_item.topics), []
            ).append(previous_item)

        for item in unmatched_current:
            signature = self._topic_signature(item.topics)
            candidates = signature_buckets.get(signature, [])
            if not candidates:
                continue
            matched = candidates.pop(0)
            matches[item.id] = matched
            remaining_prev_ids.discard(matched.id)

        return matches

    def _best_lineage_match(
        self,
        item: Any,
        lineage_index: dict[str, list[Any]],
        previous_by_id: dict[str, Any],
        remaining_prev_ids: set[str],
    ) -> Any | None:
        scores: list[tuple[int, Any]] = []
        current_lineage = self._lineage_tokens(item)
        seen: set[str] = set()
        for token in current_lineage:
            for candidate in lineage_index.get(token, []):
                if candidate.id not in remaining_prev_ids or candidate.id in seen:
                    continue
                seen.add(candidate.id)
                candidate_lineage = self._lineage_tokens(candidate)
                overlap = len(current_lineage & candidate_lineage)
                direct = 2 if candidate.id == item.id or candidate.id in item.supersedes else 0
                reverse = 1 if item.id in candidate.supersedes else 0
                scores.append((direct + reverse + overlap, candidate))
        if not scores:
            return None
        scores.sort(key=lambda entry: (entry[0], previous_by_id[entry[1].id].timespan_end), reverse=True)
        return scores[0][1]

    def _describe_arc_changes(
        self,
        current: dict[str, Arc],
        matches: dict[str, Arc],
    ) -> list[str]:
        explanations: list[str] = []
        for arc in current.values():
            topic_label = ", ".join(arc.topics[:3]) or arc.title
            old_arc = matches.get(arc.id)
            if old_arc is None:
                role = "mainline" if arc.main_or_side == MainOrSide.MAIN else "sideline"
                explanations.append(f"A new {role} emerges around {topic_label}.")
                continue
            if (
                old_arc.main_or_side != MainOrSide.MAIN
                and arc.main_or_side == MainOrSide.MAIN
            ):
                explanations.append(
                    f"The line around {topic_label} is promoted into a mainline."
                )
            if old_arc.status != Status.DORMANT and arc.status == Status.DORMANT:
                explanations.append(
                    f"The line around {topic_label} shifts into dormancy."
                )
            if old_arc.status != Status.ACTIVE and arc.status == Status.ACTIVE:
                explanations.append(
                    f"The line around {topic_label} becomes active again."
                )
            if (
                old_arc.summary != arc.summary
                and old_arc.main_or_side == arc.main_or_side
                and old_arc.status == arc.status
            ):
                explanations.append(
                    f"The line around {topic_label} is materially updated with a revised trajectory summary."
                )
        return explanations

    def _describe_epoch_changes(
        self,
        current: dict[str, Epoch],
        matches: dict[str, Epoch],
    ) -> list[str]:
        explanations: list[str] = []
        for epoch in current.values():
            topic_label = ", ".join(epoch.topics[:3]) or epoch.title
            old_epoch = matches.get(epoch.id)
            if old_epoch is None:
                explanations.append(f"A new chapter forms around {topic_label}.")
                continue
            if old_epoch.chapter_shift != epoch.chapter_shift:
                explanations.append(
                    f"The chapter around {topic_label} changes shape: {epoch.chapter_shift}"
                )
            elif old_epoch.summary != epoch.summary:
                explanations.append(
                    f"The chapter around {topic_label} receives a revised historical summary."
                )
        return explanations

    def _summarize(
        self,
        *,
        added_event_ids: list[str],
        added_scene_ids: list[str],
        added_arc_ids: list[str],
        added_epoch_ids: list[str],
        line_change_records: list[dict[str, str]],
        arc_change_explanations: list[str],
        epoch_change_explanations: list[str],
        new_topics: list[str],
    ) -> list[str]:
        lines: list[str] = []
        lines.append(
            f"Added {len(added_event_ids)} events, {len(added_scene_ids)} scenes, {len(added_arc_ids)} arcs, and {len(added_epoch_ids)} epochs."
        )
        if new_topics:
            lines.append(f"New topics enter memory: {', '.join(new_topics[:5])}.")
        if line_change_records:
            lines.append(
                f"Tracked {len(line_change_records)} structured line-level changes."
            )
        lines.extend(arc_change_explanations[:3])
        lines.extend(epoch_change_explanations[:2])
        return lines

    def _line_change_records(
        self,
        curr_arcs: dict[str, Arc],
        arc_matches: dict[str, Arc],
        curr_epochs: dict[str, Epoch],
        epoch_matches: dict[str, Epoch],
    ) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []

        for arc in curr_arcs.values():
            topic_label = ", ".join(arc.topics[:3]) or arc.title
            old_arc = arc_matches.get(arc.id)
            if old_arc is None:
                records.append(
                    {
                        "entity_type": "arc",
                        "change_type": "new_line",
                        "label": topic_label,
                        "detail": f"{topic_label} enters memory as a new line.",
                    }
                )
                continue
            if (
                old_arc.main_or_side != MainOrSide.MAIN
                and arc.main_or_side == MainOrSide.MAIN
            ):
                records.append(
                    {
                        "entity_type": "arc",
                        "change_type": "promoted_mainline",
                        "label": topic_label,
                        "detail": f"{topic_label} is promoted into a mainline.",
                    }
                )
            if old_arc.status != Status.DORMANT and arc.status == Status.DORMANT:
                records.append(
                    {
                        "entity_type": "arc",
                        "change_type": "became_dormant",
                        "label": topic_label,
                        "detail": f"{topic_label} moves into dormancy.",
                    }
                )
            if old_arc.status != Status.ACTIVE and arc.status == Status.ACTIVE:
                records.append(
                    {
                        "entity_type": "arc",
                        "change_type": "became_active",
                        "label": topic_label,
                        "detail": f"{topic_label} becomes active again.",
                    }
                )
            if (
                old_arc.summary != arc.summary
                and old_arc.main_or_side == arc.main_or_side
                and old_arc.status == arc.status
            ):
                records.append(
                    {
                        "entity_type": "arc",
                        "change_type": "revised_line",
                        "label": topic_label,
                        "detail": f"{topic_label} receives a revised trajectory summary.",
                    }
                )

        for epoch in curr_epochs.values():
            topic_label = ", ".join(epoch.topics[:3]) or epoch.title
            old_epoch = epoch_matches.get(epoch.id)
            if old_epoch is None:
                records.append(
                    {
                        "entity_type": "epoch",
                        "change_type": "new_chapter",
                        "label": topic_label,
                        "detail": f"A new chapter forms around {topic_label}.",
                    }
                )
                continue
            if old_epoch.chapter_shift != epoch.chapter_shift:
                records.append(
                    {
                        "entity_type": "epoch",
                        "change_type": "chapter_shift",
                        "label": topic_label,
                        "detail": epoch.chapter_shift,
                    }
                )
            elif old_epoch.summary != epoch.summary:
                records.append(
                    {
                        "entity_type": "epoch",
                        "change_type": "revised_chapter",
                        "label": topic_label,
                        "detail": f"{topic_label} receives a revised historical summary.",
                    }
                )

        return records

    def _mainline_report(
        self, line_change_records: list[dict[str, str]]
    ) -> dict[str, list[str] | str]:
        report = {
            "new_lines": [
                item["label"]
                for item in line_change_records
                if item["change_type"] == "new_line"
            ],
            "promoted_mainlines": [
                item["label"]
                for item in line_change_records
                if item["change_type"] == "promoted_mainline"
            ],
            "dormant_lines": [
                item["label"]
                for item in line_change_records
                if item["change_type"] == "became_dormant"
            ],
            "reactivated_lines": [
                item["label"]
                for item in line_change_records
                if item["change_type"] == "became_active"
            ],
            "new_chapters": [
                item["label"]
                for item in line_change_records
                if item["change_type"] == "new_chapter"
            ],
        }
        report["summary"] = (
            f"new_lines={len(report['new_lines'])}, promoted_mainlines={len(report['promoted_mainlines'])}, "
            f"dormant_lines={len(report['dormant_lines'])}, reactivated_lines={len(report['reactivated_lines'])}, "
            f"new_chapters={len(report['new_chapters'])}"
        )
        return report
