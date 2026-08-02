"""Application flow for turning one endogenous evaluation into chain tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class EndogenousDriveCycleContext:
    """Runtime services used by the endogenous planning application flow."""

    runtime_config: Any
    evaluate_drive: Callable[..., Awaitable[JsonDict]]
    drive_input_fields_from_evaluation: Callable[..., Dict[str, JsonDict]]
    load_drive_history: Callable[[], JsonDict]
    load_governance_events: Callable[[], JsonDict]
    load_cognition_state: Callable[[], JsonDict]
    persist_evaluation: Callable[..., JsonDict]
    restore_evaluation_snapshots: Callable[..., None]
    lm_generation_application_state: Callable[[], Any]
    plan_autonomous_chain_task: Callable[..., Awaitable[JsonDict]]
    record_ui_activity: Callable[..., None]
    touch_gateway_activity: Callable[..., Awaitable[None]]


def gate_endogenous_candidates_by_posture(
    *,
    candidate_items: List[JsonDict],
    drive_posture: JsonDict,
) -> tuple[List[JsonDict], List[JsonDict]]:
    """Keep stability candidates when observation posture limits backlog growth."""

    if not candidate_items:
        return [], []

    posture_payload = dict(drive_posture.get("payload") or {})
    preferred_focus = str(posture_payload.get("preferred_focus") or "").strip().lower()
    candidate_budget = int(posture_payload.get("candidate_budget") or 0)
    if preferred_focus != "observation":
        return list(candidate_items), []

    allowed_candidate_kinds = {
        "truthfulness_review",
        "governance_hygiene_review",
    }
    kept: List[JsonDict] = []
    deferred: List[JsonDict] = []
    for item in candidate_items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        metadata = dict(row.get("metadata") or {})
        score_breakdown = dict(metadata.get("score_breakdown") or {})
        candidate_kind = str(score_breakdown.get("candidate_kind") or "").strip().lower()
        if candidate_kind in allowed_candidate_kinds:
            kept.append(row)
            continue

        row_metadata = dict(metadata)
        row_metadata["deferred_by_drive_posture"] = True
        row_metadata["deferred_drive_posture_focus"] = preferred_focus
        row["metadata"] = row_metadata
        deferred.append(
            {
                "title": row.get("title"),
                "stable_key": row.get("stable_key"),
                "candidate_kind": candidate_kind,
                "reason": (
                    "Deferred before API-B judgement insertion because the endogenous drive "
                    "selected observation posture and this candidate is not a "
                    "stability-oriented governance action."
                ),
            }
        )

    if candidate_budget > 0 and len(kept) > candidate_budget:
        trimmed = kept[candidate_budget:]
        kept = kept[:candidate_budget]
        for row in trimmed:
            metadata = dict(row.get("metadata") or {})
            score_breakdown = dict(metadata.get("score_breakdown") or {})
            deferred.append(
                {
                    "title": row.get("title"),
                    "stable_key": row.get("stable_key"),
                    "candidate_kind": str(
                        score_breakdown.get("candidate_kind") or ""
                    ).strip().lower(),
                    "reason": (
                        "Deferred before API-B judgement insertion because observation posture "
                        f"limits endogenous backlog growth to budget {candidate_budget}."
                    ),
                }
            )

    return kept, deferred


async def run_endogenous_drive_cycle(
    *,
    context: EndogenousDriveCycleContext,
) -> JsonDict:
    """Evaluate endogenous drive and submit eligible candidates to the chain."""

    if not getattr(context.runtime_config, "endogenous_drive_enabled", False):
        return {"status": "disabled", "planned": 0, "tasks": []}

    evaluation = await context.evaluate_drive(
        {"record_activity": False, "persist_evaluation": False}
    )
    drive_posture = dict(evaluation.get("drive_posture") or {})
    governance_channels = dict(evaluation.get("governance_channels") or {})
    governance_event_stream = dict(evaluation.get("governance_event_stream") or {})
    raw_candidate_items = [
        candidate
        for candidate in evaluation.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    candidate_items, deferred_candidates = gate_endogenous_candidates_by_posture(
        candidate_items=raw_candidate_items,
        drive_posture=drive_posture,
    )
    if not candidate_items:
        context.record_ui_activity(
            "endogenous_drive_idle",
            scene="idle",
            summary="内生驱动本轮未形成新的 API-B 判断在途投影。",
            metadata={
                "drive_posture": drive_posture,
                "governance_channels": governance_channels,
                "governance_event_stream": governance_event_stream,
                "deferred_candidates": deferred_candidates,
            }
            if drive_posture
            else None,
        )
        return {
            "status": "idle",
            "planned": 0,
            "tasks": [],
            **context.drive_input_fields_from_evaluation(evaluation),
            "drive_posture": drive_posture,
            "governance_channels": governance_channels,
            "governance_event_stream": governance_event_stream,
            "deferred_candidates": deferred_candidates,
        }

    evaluation_fields = context.drive_input_fields_from_evaluation(evaluation)
    persistence_history_snapshot = context.load_drive_history()
    persistence_governance_snapshot = context.load_governance_events()
    persistence_cognition_snapshot = context.load_cognition_state()
    persisted_evaluation = context.persist_evaluation(
        deliberation=dict(evaluation.get("deliberation") or {}),
        drive_input=dict(evaluation_fields.get("drive_input") or {}),
        governance_channels=governance_channels,
        self_regulation=dict(evaluation.get("self_regulation") or {}),
        candidate_items=candidate_items,
        lm_reasoning_state=context.lm_generation_application_state().reasoning_state,
    )
    candidate_items = list(persisted_evaluation["candidate_items"])
    governance_event_stream = dict(persisted_evaluation["governance_event_stream"])

    try:
        plan_result = await context.plan_autonomous_chain_task({"items": candidate_items})
    except Exception:
        context.restore_evaluation_snapshots(
            drive_history=persistence_history_snapshot,
            governance_events=persistence_governance_snapshot,
            cognition_state=persistence_cognition_snapshot,
        )
        raise
    created_tasks = plan_result.get("tasks", [])
    if not created_tasks:
        context.restore_evaluation_snapshots(
            drive_history=persistence_history_snapshot,
            governance_events=persistence_governance_snapshot,
            cognition_state=persistence_cognition_snapshot,
        )
    if created_tasks:
        context.record_ui_activity(
            "endogenous_drive_planned",
            scene="planning",
            summary=f"内生驱动新增了 {len(created_tasks)} 个 API-B 判断在途链路项投影。",
            metadata={
                "drive_posture": drive_posture,
                "governance_channels": governance_channels,
                "governance_event_stream": governance_event_stream,
                "deferred_candidates": deferred_candidates,
                "task_ids": [task.get("task_id") for task in created_tasks],
                "tasks": [dict(task) for task in created_tasks if isinstance(task, dict)],
                "endogenous_drive_keys": [
                    task.get("metadata", {}).get("endogenous_drive_key")
                    for task in created_tasks
                ],
            },
        )
        await context.touch_gateway_activity(
            "autonomous_chain_plan",
            metadata={
                "action": "endogenous_drive",
                "count": len(created_tasks),
                "endogenous_drive_keys": [
                    task.get("metadata", {}).get("endogenous_drive_key")
                    for task in created_tasks
                ],
            },
        )

    return {
        "status": "planned",
        "planned": len(created_tasks),
        "tasks": created_tasks,
        **evaluation_fields,
        "drive_posture": drive_posture,
        "governance_channels": governance_channels,
        "governance_event_stream": governance_event_stream,
        "deferred_candidates": deferred_candidates,
    }
