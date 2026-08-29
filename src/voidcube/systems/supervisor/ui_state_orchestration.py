"""Application orchestration for the Supervisor web-room state snapshot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List

from .ui_autonomous_projection import project_autonomous_observation
from .ui_cognition_projection import (
    project_cognition_judgement,
    project_cognition_uncertainty,
)
from .ui_projection import (
    observation_count,
    project_observation_board,
    project_recent_autonomous_activity,
)
from .ui_state_projection import project_supervisor_scene_state, project_ui_metrics


JsonDict = Dict[str, Any]


@dataclass(frozen=True, slots=True)
class SupervisorUIStateContext:
    """Explicit resources and adapters needed to assemble one UI snapshot."""

    runtime_config: Any
    list_chain_projection_tasks: Callable[[], List[Any]]
    serialize_chain_task: Callable[[Any], JsonDict]
    latest_drive_candidates: Callable[[], List[JsonDict]]
    load_observation_input_snapshot: Callable[[], Awaitable[Any]]
    load_memory_stats: Callable[[], Awaitable[JsonDict]]
    load_observation_timeline: Callable[..., Awaitable[List[JsonDict]]]
    load_body_status: Callable[[List[JsonDict]], JsonDict]
    attach_trace_details: Callable[[JsonDict], Awaitable[JsonDict]]
    load_cognition_state: Callable[[], JsonDict]
    stellar_mode_status: Callable[[], JsonDict]
    voice_status: Callable[[], JsonDict]
    current_media: Callable[[], Any]
    media_queue_length: Callable[[], int] = lambda: 0
    load_employee_execution_context: Callable[[], JsonDict] = lambda: {}
    current_ui_phase: Callable[[], JsonDict] = lambda: {}


def load_ui_memory_token_usage() -> JsonDict:
    """Read optional memory token telemetry without making it a UI state owner.

    Returns per-request context utilisation (last call's prompt_tokens vs the
    model's context window) together with cumulative totals.  The cumulative
    total divided by context_length is intentionally NOT exposed as a
    percentage — it's an odometer reading, not a tank-level gauge.
    """

    try:
        from memai.llm_client import get_memory_token_usage

        raw = get_memory_token_usage()
        context_length = raw.get("context_length", 65536)
        total = raw.get("total_tokens", 0)
        last_prompt = raw.get("last_prompt_tokens", 0)
        return {
            "total_tokens": total,
            "prompt_tokens": raw.get("prompt_tokens", 0),
            "completion_tokens": raw.get("completion_tokens", 0),
            "request_count": raw.get("request_count", 0),
            "context_length": context_length,
            "last_prompt_tokens": last_prompt,
            # Per-request context utilisation — how full the window was for the
            # most recent API-B call.  Falls back to 0 when no calls have run.
            "last_request_usage_percent": (
                round((last_prompt / context_length) * 100)
                if context_length > 0 and last_prompt > 0
                else 0
            ),
        }
    except Exception:
        return {}


def normalize_ui_cognition_snapshot(snapshot: JsonDict) -> JsonDict:
    """Project bounded cognition fields used by the web-room panels."""

    cognition: JsonDict = {}
    perception = dict(snapshot.get("perception") or {})
    world_model = dict(snapshot.get("world_model") or {})
    cognition["perception"] = {
        "system_posture": perception.get("system_posture", "balanced"),
        "user_mode": perception.get("user_mode", "未识别"),
        "api_b_judgement_count": perception.get("api_b_judgement_count", 0),
        "employee_dispatch_count": perception.get("employee_dispatch_count", 0),
        "employee_running_count": perception.get("employee_running_count", 0),
        "active_sessions": perception.get("active_sessions", 0),
        "recent_errors": perception.get("recent_errors", 0),
        "learning_quality": perception.get("learning_quality", 0),
        "correction_signals": perception.get("correction_signals", 0),
        "idle_seconds": perception.get("idle_seconds", {}),
    }
    cognition["world_model"] = {
        "governance_load_state": world_model.get("governance_load_state", "未识别"),
        "memory_pressure": world_model.get("memory_pressure", 0),
        "truthfulness_pressure": world_model.get("truthfulness_pressure", 0),
        "learning_momentum": world_model.get("learning_momentum", 0),
        "body_upgrade_readiness": world_model.get("body_upgrade_readiness", 0),
        "self_confidence": world_model.get("self_confidence", 0),
    }
    cognition["needs"] = [
        {
            "need_type": item.get("need_type", "未分类需求"),
            "severity": item.get("severity", 0),
            "urgency": item.get("urgency", 0),
            "confidence": item.get("confidence", 0),
            "rationale": str(item.get("rationale", ""))[:200],
        }
        for item in list(snapshot.get("needs") or [])[:8]
        if isinstance(item, dict)
    ]
    cognition["intents"] = [
        {
            "intent_type": item.get("intent_type", "未命名意图"),
            "priority": item.get("priority", 0),
            "output_channel": item.get("output_channel", "task_candidates"),
            "target_horizon": item.get("target_horizon", "当前轮"),
            "rationale": str(item.get("rationale", ""))[:150],
        }
        for item in list(snapshot.get("intents") or [])[:6]
        if isinstance(item, dict)
    ]
    cognition["signals"] = [
        {
            "signal_type": item.get("signal_type", "未命名信号"),
            "priority": item.get("priority", 0),
            "message": str(item.get("message", ""))[:200],
        }
        for item in list(snapshot.get("signals") or [])[:5]
        if isinstance(item, dict)
    ]
    policy = dict(snapshot.get("adaptive_policy") or {})
    cognition["adaptive_policy"] = {
        "learning_expansion_bias": policy.get("learning_expansion_bias", 0),
        "truthfulness_bias": policy.get("truthfulness_bias", 0),
        "memory_continuity_bias": policy.get("memory_continuity_bias", 0),
        "governance_hygiene_bias": policy.get("governance_hygiene_bias", 0),
        "body_growth_bias": policy.get("body_growth_bias", 0),
        "observation_bias": policy.get("observation_bias", 0),
        "candidate_throttle": policy.get("candidate_throttle", 1.0),
        "candidate_budget": policy.get("candidate_budget", 3),
        "exploratory_learning_quota": policy.get("exploratory_learning_quota", 0),
        "body_growth_quota": policy.get("body_growth_quota", 0),
        "preferred_focus": policy.get("preferred_focus", "balanced"),
    }
    cognition["judgement"] = project_cognition_judgement(snapshot)
    cognition["uncertainty"] = project_cognition_uncertainty(snapshot)
    return cognition


def _normalize_loaded_cognition_state(snapshot: JsonDict) -> JsonDict:
    if isinstance(snapshot.get("state"), dict):
        return dict(snapshot.get("state") or {})
    return dict(snapshot or {})


def _project_lm_input(snapshot: JsonDict) -> JsonDict:
    lm_input: JsonDict = {}
    proposal_cognition = dict(snapshot.get("proposal_cognition") or {})
    lm_trace = dict(proposal_cognition.get("lm_trace") or {})
    if lm_trace.get("status"):
        lm_input["status"] = lm_trace["status"]
    if lm_trace.get("model_role"):
        lm_input["model_role"] = lm_trace["model_role"]
    if lm_trace.get("proposal_count") is not None:
        lm_input["proposal_count"] = lm_trace["proposal_count"]
    recent_nodes = dict(snapshot.get("uncertainty_ledger") or {}).get(
        "recent_nodes"
    ) or []
    if recent_nodes:
        lm_input["recent_evidence_nodes"] = [
            {
                "node": item.get("node_id", ""),
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
            }
            for item in recent_nodes[:20]
            if isinstance(item, dict)
        ]
    return lm_input


def _project_memory_structure(
    tier1_stats: JsonDict,
    autonomous_observation: JsonDict,
) -> JsonDict:
    tier1 = dict(tier1_stats.get("tier1") or {})
    tier2 = dict(tier1_stats.get("tier2") or {})
    identity = dict(tier1_stats.get("identity_archive") or {})
    board = dict(autonomous_observation.get("board") or {})
    return {
        "ordinary_memory": tier1.get("total_turns"),
        "active_session_memory": tier1.get("active_turns"),
        "compressed_memory": tier1.get("compressed_turns", tier2.get("total_compressed")),
        "event_memory": tier2.get("events"),
        "scene_memory": tier2.get("scenes"),
        "arc_memory": tier2.get("arcs"),
        "identity_archive": identity.get("anchors"),
        "self_experiences": identity.get("self_experiences"),
        "governance_history": identity.get("governance_history"),
        "autonomous_history": len(list(board.get("autonomous_history") or [])),
    }


def _published_phase_matches_snapshot(
    phase: JsonDict,
    chain_projection: list[JsonDict],
    *,
    mode: str,
) -> bool:
    """Reject a phase that was superseded by a newer task snapshot."""
    phase_mode = str(phase.get("mode") or "").strip().lower()
    if phase_mode and phase_mode != mode:
        return False
    if mode == "daily_companion":
        return str(phase.get("scene") or "idle").strip().lower() == "idle"
    task_id = str(phase.get("task_id") or "").strip()
    if not task_id:
        return True
    task = next(
        (
            item
            for item in chain_projection
            if str(item.get("task_id") or "").strip() == task_id
        ),
        None,
    )
    # An activity event may arrive just before its canonical task commit.
    if task is None:
        return True

    metadata = dict(task.get("metadata") or {})
    disposition = dict(metadata.get("employee_result_disposition") or {})
    disposition_status = str(disposition.get("status") or "").strip().lower()
    canonical_status = str(task.get("status") or "").strip().lower()
    family_values = {
        str(value or "").strip().lower()
        for value in (
            task.get("governance_task_type"),
            task.get("task_family"),
            task.get("execution_kind"),
            metadata.get("governance_task_type"),
            metadata.get("task_family"),
            metadata.get("execution_kind"),
        )
    }
    if disposition_status in {"awaiting_user_report", "reported_to_user"}:
        expected_scene = "idle" if mode == "daily_companion" else "planning"
    elif disposition_status in {"returned_to_xingzi", "awaiting_mem_review"}:
        expected_scene = "planning"
    elif "memory_maintenance" in family_values or any(
        "memory" in value for value in family_values
    ):
        expected_scene = "maintenance"
    elif canonical_status in {"approved", "running", "reconciling", "retry"} or (
        metadata.get("employee_assignment")
        and canonical_status not in {"planned", "deferred", "awaiting_review"}
    ):
        expected_scene = "handoff"
    elif canonical_status in {
        "planned",
        "deferred",
        "awaiting_review",
        "paused",
    }:
        expected_scene = "planning"
    else:
        expected_scene = "idle"
    return str(phase.get("scene") or "idle").strip().lower() == expected_scene


async def build_supervisor_ui_state(
    *,
    context: SupervisorUIStateContext,
) -> JsonDict:
    """Assemble one web-room snapshot from explicit runtime adapters."""

    chain_projection = [
        context.serialize_chain_task(task)
        for task in context.list_chain_projection_tasks()
    ]
    chain_projection.sort(
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    drive_candidates = context.latest_drive_candidates()
    (
        observation_input_snapshot_with_status,
        tier1_stats,
        observation_timeline,
    ) = await asyncio.gather(
        context.load_observation_input_snapshot(),
        context.load_memory_stats(),
        context.load_observation_timeline(limit=12),
    )
    observation_input_snapshot, observation_input_available = (
        observation_input_snapshot_with_status
    )
    activity = dict(observation_input_snapshot.get("activity") or {})
    counts = dict(activity.get("counts") or {})
    error_count = int(counts.get("error_count") or 0)
    body_status = context.load_body_status(chain_projection)

    autonomous_observation = project_autonomous_observation(
        chain_projection,
        drive_candidates=drive_candidates,
        active_cli_executor=dict(activity.get("active_cli_executor") or {}),
        history_tasks=chain_projection,
        timeline=observation_timeline,
    )
    try:
        autonomous_observation = await asyncio.wait_for(
            context.attach_trace_details(autonomous_observation),
            timeout=2.0,
        )
    except Exception:
        pass
    metrics = project_ui_metrics(
        chain_projection,
        autonomous_observation=autonomous_observation,
        body_status=body_status,
        error_count=error_count,
    )
    stellar_mode = context.stellar_mode_status()
    voice_status = context.voice_status()
    scene_projection = dict(
        project_supervisor_scene_state(
            autonomous_observation=autonomous_observation,
            observation_input_available=observation_input_available,
            error_count=error_count,
            memory_active=tier1_stats.get("memory_active", False),
            mode=str(stellar_mode.get("mode") or ""),
        )
    )
    if stellar_mode.get("mode") == "daily_companion":
        title = "日常陪伴中"
        latest_dialogue = dict(stellar_mode.get("latest_companion_dialogue") or {})
        latest_observation = dict(stellar_mode.get("latest_companion_observation") or {})
        if voice_status.get("active"):
            summary = "正在通过语音与你交流。"
        elif latest_dialogue:
            summary = "最近完成了一轮日常对话，继续保持陪伴。"
        elif latest_observation.get("intent_state") == "understood":
            summary = "已理解当前任务，在确有帮助前保持安静。"
        else:
            summary = "正在安静陪伴并观察 VoidCube 内部事件。"
        scene_projection.update(
            {
                "scene": "idle",
                "room_location": "sofa",
                "action": "rest",
                "title": title,
                "summary": summary,
                "stage": "companion",
                "task_id": "",
                "mode": "daily_companion",
            }
        )
    published_phase = dict(context.current_ui_phase() or {})
    published_phase_is_current = (
        int(published_phase.get("ui_phase_revision") or 0) > 0
        and _published_phase_matches_snapshot(
            published_phase,
            chain_projection,
            mode=str(stellar_mode.get("mode") or "").strip().lower(),
        )
    )
    if published_phase_is_current:
        phase_mode = str(published_phase.get("mode") or "").strip().lower()
        current_mode = str(stellar_mode.get("mode") or "").strip().lower()
        if not phase_mode or phase_mode == current_mode:
            scene_projection.update(
                {
                    "scene": str(published_phase.get("scene") or "idle"),
                    "room_location": str(
                        published_phase.get("room_location") or "sofa"
                    ),
                    "action": str(published_phase.get("action") or "rest"),
                    "title": str(
                        published_phase.get("title")
                        or scene_projection.get("title")
                        or ""
                    ),
                    "summary": str(
                        published_phase.get("summary")
                        or scene_projection.get("summary")
                        or ""
                    ),
                    "stage": str(
                        published_phase.get("stage") or "idle"
                    ).strip().lower(),
                    "task_id": str(published_phase.get("task_id") or ""),
                    "mode": phase_mode or current_mode,
                }
            )

    cognition_snapshot = _normalize_loaded_cognition_state(
        context.load_cognition_state()
    )
    lm_input = {
        "generation_enabled": bool(
            getattr(
                context.runtime_config,
                "endogenous_drive_lm_task_generation_enabled",
                False,
            )
        ),
        **_project_lm_input(cognition_snapshot),
    }
    cognition = normalize_ui_cognition_snapshot(cognition_snapshot)

    recent_autonomous_activity = project_recent_autonomous_activity(
        dict(observation_input_snapshot.get("activity") or {})
    )
    autonomous_runtime = dict(autonomous_observation.get("runtime") or {})
    autonomous_runtime["user_chain_signal"] = dict(
        observation_input_snapshot.get("user_chain_signal") or {}
    )
    autonomous_runtime["snapshot_source"] = str(
        observation_input_snapshot.get("snapshot_source") or "default"
    )
    autonomous_counts = dict(autonomous_observation.get("counts") or {})
    autonomous_runtime["employee_dispatch_count"] = observation_count(
        autonomous_counts.get("employee_dispatch")
    )
    autonomous_runtime["employee_running_count"] = observation_count(
        autonomous_counts.get("employee_running")
    )
    autonomous_observation["runtime"] = autonomous_runtime
    autonomous_observation["board"] = project_observation_board(
        autonomous_observation,
        recent_activity=recent_autonomous_activity,
    )
    memory_structure = _project_memory_structure(
        tier1_stats,
        autonomous_observation,
    )
    try:
        employee_context = dict(context.load_employee_execution_context() or {})
    except Exception:
        employee_context = {}
    employee_runs = [
        item for item in list(employee_context.get("items") or [])
        if isinstance(item, dict) and str(item.get("autonomous_task_id") or "").strip()
    ]
    if employee_runs:
        run_by_task_id = {
            str(item.get("autonomous_task_id") or "").strip(): item
            for item in employee_runs
        }
        board = autonomous_observation["board"]
        enriched_runs = []
        for card in list(board.get("employee_runs") or []):
            enriched = dict(card)
            run = run_by_task_id.get(str(card.get("task_id") or "").strip())
            if run:
                enriched.update(
                    {
                        key: value
                        for key, value in run.items()
                        if key not in {"title", "summary"}
                    }
                )
            enriched_runs.append(enriched)
        board["employee_runs"] = enriched_runs
    autonomous_observation["metrics"] = metrics
    current_media = context.current_media()
    return {
        "status": "ok",
        "stellar_mode": stellar_mode,
        "voice": voice_status,
        "scene": scene_projection["scene"],
        "title": scene_projection["title"],
        "summary": scene_projection["summary"],
        "room_location": scene_projection["room_location"],
        "scene_action": scene_projection["action"],
        "scene_stage": scene_projection["stage"],
        "scene_task_id": scene_projection["task_id"],
        "scene_mode": scene_projection["mode"],
        "ui_phase": dict(published_phase if published_phase_is_current else {}),
        "generated_at": datetime.utcnow().isoformat(),
        "autonomous_observation": autonomous_observation,
        "mem_usage": load_ui_memory_token_usage(),
        "tier1_stats": tier1_stats,
        "memory_structure": memory_structure,
        "body_status": body_status,
        "error_count": error_count,
        "timeline": observation_timeline[:10],
        "lm_input": lm_input,
        "cognition": cognition,
        "media": {
            "current": current_media,
            "queue_length": context.media_queue_length(),
        },
    }
