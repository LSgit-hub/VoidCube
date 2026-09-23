"""Application orchestration for one endogenous drive evaluation cycle."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .endogenous_candidate_pipeline import CORE_VALUES
from .endogenous_policy import (
    HISTORICAL_OBSERVATION_CARRYOVER_RELEASED,
)
from .endogenous_state_projection import (
    project_drive_history,
    project_governance_event_stream,
)
from .endogenous_body_projection import build_body_improvement_projection
from .endogenous_drive_context import build_drive_context, get_shell_slot_meta


JsonDict = Dict[str, Any]
Candidate = Any
_REPETITION_EVENT_TYPES = {
    "decision",
    "execution_finalize",
    "employee_execution_completed",
}
_REPETITION_NEGATIVE_STATUSES = {"cancelled", "failed", "deferred"}
_REPETITION_TERMINAL_EVENT_PREFIX = "employee_execution_"
_REPETITION_QUALITY_THRESHOLD = 0.4


def _outcome_timestamp(item: JsonDict) -> datetime | None:
    for key in (
        "recorded_at",
        "completed_at",
        "updated_at",
        "timestamp",
        "decided_at",
        "created_at",
    ):
        raw = item.get(key)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _outcome_timestamp_value(item: JsonDict) -> Any:
    for key in (
        "recorded_at",
        "completed_at",
        "updated_at",
        "timestamp",
        "decided_at",
        "created_at",
    ):
        value = item.get(key)
        if value:
            return value
    return None


def _outcome_event_authority(item: JsonDict) -> int:
    event_type = str(item.get("event_type") or "").strip().lower()
    if event_type.startswith(_REPETITION_TERMINAL_EVENT_PREFIX):
        return 3
    if event_type in {"execution_finalize", "execution_timeout", "execution_reconcile"}:
        return 3
    if event_type == "decision":
        return 2
    return 0


def _outcome_result(item: JsonDict) -> tuple[str, str]:
    status = str(item.get("result_status") or item.get("execution_outcome_status") or item.get("status") or "").strip().lower()
    if status in _REPETITION_NEGATIVE_STATUSES or status in {"timeout", "timed_out", "degraded"}:
        return "negative", f"status:{status}"
    raw_quality = item.get("quality_score")
    if raw_quality is None:
        return "unknown", "quality_score_missing"
    try:
        quality = float(raw_quality)
    except (TypeError, ValueError, OverflowError):
        return "unknown", "quality_score_invalid"
    if not math.isfinite(quality) or not 0.0 <= quality <= 1.0:
        return "unknown", "quality_score_invalid"
    if quality < _REPETITION_QUALITY_THRESHOLD:
        return "negative", "quality_below_threshold"
    return "positive", "quality_meets_threshold"


def analyze_repeated_self_learning_outcomes(
    outcomes: List[JsonDict],
    *,
    window_size: int = 4,
    minimum_consecutive_failures: int = 3,
) -> JsonDict:
    """Return an auditable failure streak using one final event per task."""
    try:
        normalized_window_size = int(window_size)
        normalized_minimum_failures = int(minimum_consecutive_failures)
    except (TypeError, ValueError, OverflowError):
        normalized_window_size = 0
        normalized_minimum_failures = 0
    result: JsonDict = {
        "blocked": False,
        "reason": "insufficient_valid_outcomes",
        "consecutive_negative_count": 0,
        "sampled_outcomes": [],
        "sampled_task_ids": [],
        "recent_success_at": None,
        "unknown_count": 0,
        "deduplicated_count": 0,
        "window_size": max(0, normalized_window_size),
        "minimum_consecutive_failures": max(0, normalized_minimum_failures),
    }
    if normalized_window_size <= 0 or normalized_minimum_failures <= 0:
        result["reason"] = "invalid_threshold"
        return result

    eligible: list[tuple[int, JsonDict]] = []
    for index, item in enumerate(outcomes):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip().lower()
        terminal_event = event_type.startswith(_REPETITION_TERMINAL_EVENT_PREFIX) and event_type.rsplit("_", 1)[-1] in {"completed", "failed", "cancelled", "deferred", "timeout"}
        if event_type not in _REPETITION_EVENT_TYPES and not terminal_event:
            continue
        if str(item.get("task_family") or "").strip().lower() != "self_learning":
            continue
        eligible.append((index, dict(item)))

    latest_by_batch: dict[str, tuple[int, JsonDict]] = {}
    for index, item in eligible:
        task_id = str(item.get("task_id") or "").strip()
        batch_id = str(
            item.get("execution_batch_id")
            or item.get("batch_id")
            or item.get("attempt_id")
            or item.get("lease_id")
            or item.get("execution_id")
            or item.get("employee_run_id")
            or item.get("run_id")
            or ""
        ).strip()
        batch_key = f"{task_id}:{batch_id}" if task_id and batch_id else task_id or batch_id or f"outcome:{index}"
        current = latest_by_batch.get(batch_key)
        if current is None:
            latest_by_batch[batch_key] = (index, item)
            continue
        current_index, current_item = current
        current_timestamp = _outcome_timestamp(current_item)
        candidate_timestamp = _outcome_timestamp(item)
        current_order = (_outcome_event_authority(current_item), current_timestamp.timestamp() if current_timestamp else 0.0, -current_index)
        candidate_order = (_outcome_event_authority(item), candidate_timestamp.timestamp() if candidate_timestamp else 0.0, -index)
        if candidate_order > current_order:
            latest_by_batch[batch_key] = (index, item)

    effective = list(latest_by_batch.values())
    effective.sort(key=lambda pair: (1 if _outcome_timestamp(pair[1]) else 0, _outcome_timestamp(pair[1]).timestamp() if _outcome_timestamp(pair[1]) else 0.0, -pair[0]), reverse=True)
    sampled = [item for _, item in effective[:normalized_window_size]]
    result["deduplicated_count"] = len(effective)
    result["sampled_task_ids"] = [str(item.get("task_id") or item.get("endogenous_drive_key") or "") for item in sampled]
    for item, task_id in zip(sampled, result["sampled_task_ids"]):
        classification, classification_reason = _outcome_result(item)
        result["sampled_outcomes"].append(
            {
                "task_id": task_id,
                "event_type": str(item.get("event_type") or ""),
                "recorded_at": _outcome_timestamp_value(item),
                "status": item.get("status"),
                "result_status": item.get("result_status"),
                "quality_score": item.get("quality_score"),
                "classification": classification,
                "classification_reason": classification_reason,
            }
        )

    for item in sampled:
        classification, _ = _outcome_result(item)
        if classification == "negative":
            result["consecutive_negative_count"] += 1
            continue
        if classification == "unknown":
            result["unknown_count"] += 1
            result["reason"] = "newest_outcome_unknown"
        else:
            result["recent_success_at"] = _outcome_timestamp_value(item)
            result["reason"] = "newest_outcome_success"
        break
    if result["consecutive_negative_count"] >= normalized_minimum_failures:
        result["blocked"] = True
        result["reason"] = "repeated_negative_self_learning_outcomes"
    elif len(sampled) < normalized_minimum_failures:
        result["reason"] = "insufficient_valid_outcomes"
    return result


def repeated_self_learning_outcomes_blocked(
    outcomes: List[JsonDict],
    *,
    window_size: int = 4,
    minimum_consecutive_failures: int = 3,
) -> bool:
    return bool(analyze_repeated_self_learning_outcomes(outcomes, window_size=window_size, minimum_consecutive_failures=minimum_consecutive_failures)["blocked"])

@dataclass(frozen=True, slots=True)
class EndogenousDriveEvaluationContext:
    """Explicit runtime services required by the evaluation application flow."""

    runtime_config: Any
    resolve_drive_input_request: Callable[[JsonDict], Awaitable[JsonDict]]
    load_self_regulation: Callable[[], JsonDict]
    load_drive_history: Callable[[], JsonDict]
    normalize_strategy_memory: Callable[[Any], JsonDict]
    api_b_judgement_task_summaries: Callable[[int], List[JsonDict]]
    employee_execution_lane_task_summaries: Callable[[int], List[JsonDict]]
    build_deliberation_report: Callable[..., Any]
    generate_candidates: Callable[..., List[Candidate]]
    existing_drive_keys: Callable[[], set[str]]
    schedule_candidate_items: Callable[[List[Candidate]], List[JsonDict]]
    lm_generation_application_state: Callable[[], Any]
    derive_cognitive_self_regulation: Callable[..., JsonDict]
    release_cleared_observation_carryover: Callable[..., JsonDict]
    governance_channels_from_deliberation: Callable[[JsonDict], JsonDict]
    persist_evaluation: Callable[..., JsonDict]
    load_governance_events: Callable[[], JsonDict]
    build_cognition_state: Callable[..., JsonDict]
    record_ui_activity: Callable[..., None]
    build_response_fields: Callable[..., Dict[str, JsonDict]]
    drive_posture_from_deliberation: Callable[[JsonDict], JsonDict]
    core_values: Any
    load_evolution_foundation: Callable[[], JsonDict] | None = None


def build_endogenous_drive_policy(runtime_config: Any) -> JsonDict:
    """Project runtime tuning into the explicit policy consumed by the Engine."""

    def setting(name: str, default: Any) -> Any:
        return getattr(runtime_config, name, default) or default

    return {
        "learning_topic_cooldown_hours": int(
            setting("endogenous_drive_learning_topic_cooldown_hours", 24)
        ),
        "body_improvement_cooldown_hours": int(
            setting("endogenous_drive_body_improvement_cooldown_hours", 12)
        ),
        "topic_overlap_threshold": float(
            setting("endogenous_drive_topic_overlap_threshold", 0.6)
        ),
        "body_improvement_min_quality": float(
            setting("body_improvement_min_quality", 60.0)
        ),
        "body_improvement_editable_dirs": list(
            setting(
                "body_improvement_editable_dirs",
                ["skills/", "src/voidcube/runtime/agent/", "src/voidcube/extensions/tools/", "prompts/"],
            )
        ),
        "body_improvement_forbidden_patterns": list(
            setting(
                "body_improvement_forbidden_patterns",
                ["**/credential*", "**/.env*", "src/voidcube/systems/**"],
            )
        ),
        "body_improvement_max_files": int(
            setting("body_improvement_max_files", 5)
        ),
    }


def _merge_self_regulation(
    persisted: JsonDict,
    current: JsonDict,
) -> JsonDict:
    merged = dict(persisted)
    boost_keys = (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    )
    for key in boost_keys:
        merged[key] = round(
            min(
                1.0,
                float(persisted.get(key) or 0.0)
                + float(current.get(key) or 0.0),
            ),
            4,
        )
    merged["last_reason"] = "; ".join(
        item
        for item in (
            str(persisted.get("last_reason") or "").strip(),
            str(current.get("last_reason") or "").strip(),
        )
        if item
    ) or None
    return merged


def _apply_regulation_to_policy(policy: JsonDict, regulation: JsonDict) -> None:
    for key in (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    ):
        policy[key] = float(regulation.get(key) or 0.0)


async def evaluate_endogenous_drive(
    *,
    request: Optional[JsonDict],
    context: EndogenousDriveEvaluationContext,
) -> JsonDict:
    """Run one evaluation while keeping runtime resources behind callbacks."""

    request = dict(request or {})
    record_activity = bool(request.get("record_activity", True))
    persist_evaluation = bool(request.get("persist_evaluation", True))
    drive_input = await context.resolve_drive_input_request(request)
    evolution_foundation = (
        dict(context.load_evolution_foundation() or {})
        if context.load_evolution_foundation is not None
        else {}
    )
    drive_input["evolution_foundation"] = evolution_foundation
    persisted_self_regulation = dict(context.load_self_regulation() or {})
    api_b_judgement_tasks = context.api_b_judgement_task_summaries(24)
    employee_execution_lane_tasks = context.employee_execution_lane_task_summaries(24)
    drive_input["api_b_judgement_tasks"] = api_b_judgement_tasks
    drive_input["employee_execution_lane_tasks"] = employee_execution_lane_tasks
    drive_input["autonomous_chain_live_tasks"] = [
        *api_b_judgement_tasks,
        *employee_execution_lane_tasks,
    ]
    history_snapshot = context.load_drive_history()
    repetition_audit = analyze_repeated_self_learning_outcomes(
        list(dict(history_snapshot or {}).get("outcomes") or [])
    )
    drive_input["self_iteration_repetition_blocked"] = bool(repetition_audit["blocked"])
    drive_input["self_iteration_repetition_audit"] = repetition_audit
    drive_input["endogenous_drive_policy"] = build_endogenous_drive_policy(
        context.runtime_config
    )
    drive_input["candidate_generation"] = build_body_improvement_projection(
        drive_context=build_drive_context(drive_input),
        shell_slot_meta=get_shell_slot_meta(drive_input),
    )
    drive_input["drive_history"] = project_drive_history(
        context.load_drive_history(),
        normalize_strategy_memory=context.normalize_strategy_memory,
    )
    self_regulation = dict(persisted_self_regulation)
    _apply_regulation_to_policy(
        drive_input["endogenous_drive_policy"],
        self_regulation,
    )
    max_candidates = int(
        request.get(
            "max_candidates",
            getattr(context.runtime_config, "endogenous_drive_max_candidates", 0),
        )
    )

    deliberation = context.build_deliberation_report(drive_input=drive_input)
    deliberation_dict = deliberation.to_dict()
    candidates = await asyncio.to_thread(
        context.generate_candidates,
        drive_input=drive_input,
        existing_drive_keys=context.existing_drive_keys(),
        max_candidates=max_candidates,
        deliberation_report=deliberation,
    )
    candidate_items = context.schedule_candidate_items(candidates)
    lm_application_state = context.lm_generation_application_state()
    lm_reasoning_state = dict(lm_application_state.reasoning_state or {})
    cognitive_self_regulation = context.derive_cognitive_self_regulation(
        drive_history=drive_input["drive_history"],
        lm_reasoning_state=lm_reasoning_state,
        deliberation=deliberation_dict,
    )
    cognitive_self_regulation = context.release_cleared_observation_carryover(
        persisted_self_regulation=self_regulation,
        cognitive_self_regulation=cognitive_self_regulation,
        deliberation=deliberation_dict,
        lm_reasoning_state=lm_reasoning_state,
        drive_history=drive_input["drive_history"],
    )
    observation_carryover_released = bool(
        cognitive_self_regulation.get(
            HISTORICAL_OBSERVATION_CARRYOVER_RELEASED,
            False,
        )
    )
    if observation_carryover_released:
        drive_input["endogenous_drive_policy"][
            HISTORICAL_OBSERVATION_CARRYOVER_RELEASED
        ] = True
    combined_self_regulation = _merge_self_regulation(
        self_regulation,
        cognitive_self_regulation,
    )
    _apply_regulation_to_policy(
        drive_input["endogenous_drive_policy"],
        combined_self_regulation,
    )
    boost_keys = (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    )
    if observation_carryover_released or any(
        float(cognitive_self_regulation.get(key) or 0.0) > 0.0
        for key in boost_keys
    ):
        deliberation = context.build_deliberation_report(drive_input=drive_input)
        deliberation_dict = deliberation.to_dict()
        candidates = await asyncio.to_thread(
            context.generate_candidates,
            drive_input=drive_input,
            existing_drive_keys=context.existing_drive_keys(),
            max_candidates=max_candidates,
            deliberation_report=deliberation,
            lm_proposals_override=lm_application_state.candidate_repass_proposals,
        )
        candidate_items = context.schedule_candidate_items(candidates)

    governance_channels = context.governance_channels_from_deliberation(
        deliberation_dict
    )
    if persist_evaluation:
        persisted_evaluation = context.persist_evaluation(
            deliberation=deliberation_dict,
            drive_input=drive_input,
            governance_channels=governance_channels,
            self_regulation=combined_self_regulation,
            candidate_items=candidate_items,
            lm_reasoning_state=lm_reasoning_state,
        )
        candidate_items = list(persisted_evaluation["candidate_items"])
        governance_event_stream = dict(
            persisted_evaluation["governance_event_stream"]
        )
        cognition_state = dict(persisted_evaluation["cognition_state"])
    else:
        governance_event_stream = project_governance_event_stream(
            context.load_governance_events()
        )
        cognition_state = context.build_cognition_state(
            deliberation=deliberation_dict,
            governance_channels=governance_channels,
            governance_event_stream=governance_event_stream,
            self_regulation=combined_self_regulation,
            candidate_items=candidate_items,
            lm_reasoning_state=lm_reasoning_state,
        )
    if record_activity:
        context.record_ui_activity(
            "endogenous_drive_evaluated",
            scene="planning",
            summary=f"内生驱动已完成一轮认知评估，并形成了 {len(candidates)} 个候选判断投影。",
            metadata={
                "count": len(candidates),
                "candidate_keys": [candidate.stable_key for candidate in candidates],
                "candidates": [dict(item) for item in candidate_items],
                "deliberation": deliberation_dict,
                "cognition_state": cognition_state,
            },
        )
    response_fields = context.build_response_fields(drive_input=drive_input)
    return {
        "status": "evaluated",
        "enabled": bool(
            getattr(context.runtime_config, "endogenous_drive_enabled", False)
        ),
        "core_values": context.core_values,
        **response_fields,
        "deliberation": deliberation_dict,
        "candidates": candidate_items,
        "count": len(candidates),
        "drive_posture": context.drive_posture_from_deliberation(deliberation_dict),
        "governance_channels": governance_channels,
        "governance_event_stream": governance_event_stream,
        "self_regulation": combined_self_regulation,
        "cognitive_self_regulation": cognitive_self_regulation,
        "cognition_state": cognition_state,
        "generation_diagnostics": dict(getattr(lm_application_state, "generation_diagnostics", {}) or {}),
    }
