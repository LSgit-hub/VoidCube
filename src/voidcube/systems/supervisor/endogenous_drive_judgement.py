"""Drive-judgement metadata projections for candidate evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .endogenous_drive_models import (
    DriveAdaptivePolicy,
    DriveDeliberationReport,
    DriveIntent,
    DrivePerceptionSnapshot,
    DriveReflection,
    DriveWorldModel,
)
from .endogenous_needs import DriveNeed


def build_intent_metadata(
    *,
    intent: DriveIntent,
    needs: List[DriveNeed],
    perception: DrivePerceptionSnapshot,
    world_model: DriveWorldModel,
    reflection: DriveReflection,
    adaptive_policy: DriveAdaptivePolicy,
) -> Dict[str, Any]:
    report = DriveDeliberationReport(
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=adaptive_policy,
        needs=needs,
        intents=[intent],
    ).to_dict()
    intent_dict = report["intents"][0] if report["intents"] else {}
    linked_needs = [
        need
        for need in report["needs"]
        if need["need_type"] in set(intent.source_needs)
    ]
    return {
        "perception": report["perception"],
        "world_model": report["world_model"],
        "reflection": report["reflection"],
        "adaptive_policy": report["adaptive_policy"],
        "intent": intent_dict,
        "intents": [intent_dict] if intent_dict else [],
        "needs": linked_needs,
    }


def build_drive_judgement_metadata(
    *,
    intent: Optional[DriveIntent],
    candidate_kind: str,
    all_intents: List[DriveIntent],
    needs: List[DriveNeed],
    perception: DrivePerceptionSnapshot,
    world_model: DriveWorldModel,
    reflection: DriveReflection,
    adaptive_policy: DriveAdaptivePolicy,
) -> Dict[str, Any]:
    if intent is not None:
        return build_intent_metadata(
            intent=intent,
            needs=needs,
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
        )

    matching_intents = [
        item
        for item in all_intents
        if str(item.candidate_kind or "").strip() == candidate_kind
    ]
    selected_intents = matching_intents or list(all_intents[:3])
    report = DriveDeliberationReport(
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=adaptive_policy,
        needs=needs,
        intents=selected_intents,
    ).to_dict()
    source_need_types = {
        need_type
        for intent_row in report["intents"]
        for need_type in list(intent_row.get("source_needs") or [])
        if str(need_type).strip()
    }
    linked_needs = [
        need
        for need in report["needs"]
        if not source_need_types or need["need_type"] in source_need_types
    ][:4]
    return {
        "perception": report["perception"],
        "world_model": report["world_model"],
        "reflection": report["reflection"],
        "adaptive_policy": report["adaptive_policy"],
        "intent": report["intents"][0] if report["intents"] else {},
        "intents": list(report["intents"]),
        "needs": linked_needs,
    }
