"""Application orchestration for the Supervisor web-room state snapshot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List

from systems.supervisor.ui_autonomous_projection import project_autonomous_observation
from systems.supervisor.ui_cognition_projection import (
    project_cognition_judgement,
    project_cognition_uncertainty,
)
from systems.supervisor.ui_projection import (
    observation_count,
    project_observation_board,
    project_recent_autonomous_activity,
)
from systems.supervisor.ui_state_projection import (
    project_supervisor_scene,
    project_ui_metrics,
)


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


def load_ui_memory_token_usage() -> JsonDict:
    """Read optional memory token telemetry without making it a UI state owner."""

    try:
        from memai.llm_client import get_memory_token_usage

        raw = get_memory_token_usage()
        context_length = raw.get("context_length", 65536)
        total = raw.get("total_tokens", 0)
        return {
            "total_tokens": total,
            "prompt_tokens": raw.get("prompt_tokens", 0),
            "completion_tokens": raw.get("completion_tokens", 0),
            "request_count": raw.get("request_count", 0),
            "context_length": context_length,
            "context_percent": (
                round((total / context_length) * 100)
                if context_length > 0
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
        "api_a_handoff_count": perception.get("api_a_handoff_count", 0),
        "api_a_running_count": perception.get("api_a_running_count", 0),
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
    scene, title, summary = project_supervisor_scene(
        autonomous_observation=autonomous_observation,
        observation_input_available=observation_input_available,
        error_count=error_count,
        memory_active=tier1_stats.get("memory_active", False),
    )
    stellar_mode = context.stellar_mode_status()
    voice_status = context.voice_status()
    if stellar_mode.get("mode") == "daily_companion":
        scene = "idle"
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
    autonomous_runtime["api_a_handoff_count"] = observation_count(
        autonomous_counts.get("api_a_handoff")
    )
    autonomous_runtime["api_a_running_count"] = observation_count(
        autonomous_counts.get("api_a_running")
    )
    autonomous_observation["runtime"] = autonomous_runtime
    autonomous_observation["board"] = project_observation_board(
        autonomous_observation,
        recent_activity=recent_autonomous_activity,
    )
    autonomous_observation["metrics"] = metrics
    current_media = context.current_media()
    return {
        "status": "ok",
        "stellar_mode": stellar_mode,
        "voice": voice_status,
        "scene": scene,
        "title": title,
        "summary": summary,
        "generated_at": datetime.utcnow().isoformat(),
        "autonomous_observation": autonomous_observation,
        "mem_usage": load_ui_memory_token_usage(),
        "tier1_stats": tier1_stats,
        "body_status": body_status,
        "error_count": error_count,
        "timeline": observation_timeline[:10],
        "lm_input": lm_input,
        "cognition": cognition,
        "media": {
            "current": current_media,
            "queue_length": 1 if current_media else 0,
        },
    }
