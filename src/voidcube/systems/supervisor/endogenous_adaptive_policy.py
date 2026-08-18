"""Pure adaptive-policy projection for the endogenous drive."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from .endogenous_policy import (
    TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD,
    has_memory_backlog_recovery_window,
    has_truthfulness_review_signal,
)
from .endogenous_drive_context import normalize_strategy_memory
from .endogenous_history import (
    normalize_historical_outcomes,
    summarize_historical_pressure,
)


_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"


class PerceptionPolicyInput(Protocol):
    user_mode: str
    system_posture: str
    correction_signals: int
    pending_review_count: int
    stale_backlog_count: int
    api_b_judgement_count: int
    api_a_handoff_count: int
    api_a_running_count: int


class WorldModelPolicyInput(Protocol):
    truthfulness_pressure: float
    memory_pressure: float
    body_upgrade_readiness: float


class ReflectionPolicyInput(Protocol):
    learning_yield_state: str
    api_b_judgement_blockage_pressure: float
    repeated_drive_pressure: float
    body_growth_blocked: bool
    autonomy_readiness: float
    recent_learning_quality: float
    dominant_constraint: str


def clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def strategy_context_key(*, user_mode: str, system_posture: str, dominant_constraint: str) -> str:
    normalized_user_mode = str(user_mode or "unknown").strip().lower() or "unknown"
    normalized_posture = str(system_posture or "unknown").strip().lower() or "unknown"
    normalized_constraint = (
        str(dominant_constraint or "none").strip().lower() or "none"
    )
    return f"{normalized_user_mode}|{normalized_posture}|{normalized_constraint}"


def build_adaptive_policy(
    *,
    perception: PerceptionPolicyInput,
    world_model: WorldModelPolicyInput,
    reflection: ReflectionPolicyInput,
    drive_context: Dict[str, Any],
) -> Dict[str, Any]:
    drive_history = dict(drive_context.get("drive_history") or {})
    policy = dict(drive_context.get("policy") or {})
    strategy_memory = normalize_strategy_memory(drive_history.get("strategy_memory"))
    historical_outcomes = normalize_historical_outcomes(
        [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
    )
    recent_historical_outcomes = historical_outcomes[:12]
    recent_self_learning_outcomes = [
        item
        for item in historical_outcomes
        if str(item.get("task_family") or item.get("governance_task_type") or "")
        .strip()
        .lower()
        == "self_learning"
    ][:12]
    historical_pressure = summarize_historical_pressure(
        recent_historical_outcomes=recent_historical_outcomes,
        recent_self_learning_outcomes=recent_self_learning_outcomes,
    )
    context_key = strategy_context_key(
        user_mode=perception.user_mode,
        system_posture=perception.system_posture,
        dominant_constraint=reflection.dominant_constraint,
    )
    return build_adaptive_policy_projection(
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        policy=policy,
        strategy_memory=strategy_memory,
        historical_outcomes=historical_outcomes,
        historical_pressure=historical_pressure,
        context_key=context_key,
    )


def build_adaptive_policy_projection(
    *,
    perception: PerceptionPolicyInput,
    world_model: WorldModelPolicyInput,
    reflection: ReflectionPolicyInput,
    policy: Dict[str, Any],
    strategy_memory: Dict[str, Any],
    historical_outcomes: List[Dict[str, Any]],
    historical_pressure: Dict[str, Any],
    context_key: str,
) -> Dict[str, Any]:
    stats: Dict[str, Dict[str, int]] = {}
    for item in historical_outcomes[:18]:
        family = str(
            item.get("task_family")
            or item.get("governance_task_type")
            or item.get("execution_kind")
            or "unknown"
        ).strip().lower()
        if not family:
            continue
        bucket = stats.setdefault(
            family,
            {"completed": 0, "failed": 0, "dragging": 0},
        )
        status = str(item.get("status") or "").strip().lower()
        if status == "completed":
            bucket["completed"] += 1
        elif status in {"failed", "cancelled"}:
            bucket["failed"] += 1
        elif status in {
            "approved",
            "deferred",
            "paused",
            "awaiting_review",
            "awaiting_user_consent",
            "retry",
        }:
            bucket["dragging"] += 1

    def family_success(families: List[str], default: float = 0.5) -> float:
        completed = failed = dragging = 0
        for family in families:
            bucket = stats.get(family, {})
            completed += int(bucket.get("completed") or 0)
            failed += int(bucket.get("failed") or 0)
            dragging += int(bucket.get("dragging") or 0)
        total = completed + failed + dragging
        if total <= 0:
            return default
        return completed / total

    historical_completed = 0
    historical_failed = 0
    historical_dragging = 0
    for bucket in stats.values():
        historical_completed += int(bucket.get("completed") or 0)
        historical_failed += int(bucket.get("failed") or 0)
        historical_dragging += int(bucket.get("dragging") or 0)
    historical_total = historical_completed + historical_failed + historical_dragging
    historical_drag_ratio = (
        (historical_failed + historical_dragging) / historical_total
        if historical_total > 0
        else 0.0
    )

    scoped_historical_scope = str(historical_pressure["scope"] or "global")
    scoped_historical_total = int(historical_pressure["total"] or 0)
    scoped_historical_drag_ratio = float(historical_pressure["drag_ratio"] or 0.0)
    historical_has_temporal_markers = bool(
        historical_pressure.get("has_temporal_markers")
    )
    recent_relapse_drag_count = int(
        historical_pressure["recent_relapse_drag_count"] or 0
    )
    recent_relapse_drag_ratio = float(
        historical_pressure["recent_relapse_drag_ratio"] or 0.0
    )

    learning_success = family_success(["self_learning"], default=0.55)
    backlog_success = family_success(
        ["general_self_evolution", "self_evolution"], default=0.45
    )
    body_success = family_success(["body_upgrade", "body_improvement"], default=0.4)
    memory_success = family_success(["memory_maintenance"], default=0.65)

    focus_stats = dict(strategy_memory.get("focus_stats") or {})
    contextual_focus_stats = dict(
        dict(strategy_memory.get("contextual_focus_stats") or {}).get(context_key) or {}
    )
    agenda_topic_stats = dict(strategy_memory.get("agenda_topic_stats") or {})
    observation_target_stats = dict(strategy_memory.get("observation_target_stats") or {})

    def effectiveness_from_bucket(bucket: Dict[str, Any], default: float) -> float:
        completed = int(bucket.get("completed") or 0)
        failed = int(bucket.get("failed") or 0)
        dragging = int(bucket.get("dragging") or 0)
        judged = int(bucket.get("judged") or 0)
        resolved = completed + failed + dragging
        if judged <= 0 and resolved <= 0:
            return default
        if resolved <= 0:
            return default
        success = completed / resolved
        drag_penalty = dragging / resolved
        failure_penalty = failed / resolved
        return clamp01(success - drag_penalty * 0.18 - failure_penalty * 0.24)

    def focus_effectiveness(focus: str, default: float) -> float:
        global_bucket = dict(focus_stats.get(focus) or {})
        contextual_bucket = dict(contextual_focus_stats.get(focus) or {})
        global_effect = effectiveness_from_bucket(global_bucket, default)
        if not contextual_bucket:
            return global_effect
        contextual_effect = effectiveness_from_bucket(contextual_bucket, global_effect)
        contextual_judged = int(contextual_bucket.get("judged") or 0)
        global_judged = int(global_bucket.get("judged") or 0)
        if contextual_judged <= 0:
            return global_effect
        confidence = min(0.75, 0.35 + contextual_judged * 0.08 + global_judged * 0.02)
        return clamp01(global_effect * (1.0 - confidence) + contextual_effect * confidence)

    focus_effectiveness_values = {
        "truthfulness": focus_effectiveness("truthfulness", default=0.56),
        "memory_continuity": focus_effectiveness("memory_continuity", default=0.58),
        "learning_expansion": focus_effectiveness("learning_expansion", default=0.54),
        "governance_hygiene": focus_effectiveness("governance_hygiene", default=0.48),
        "body_growth": focus_effectiveness("body_growth", default=0.44),
        "observation": focus_effectiveness("observation", default=0.52),
    }
    observation_recovery_advantage = max(
        0.0,
        focus_effectiveness_values["observation"]
        - focus_effectiveness_values["learning_expansion"],
    )
    contextual_observation_available = bool(contextual_focus_stats.get("observation"))
    unresolved_observation_pressure = 0.0
    observation_recovery_signal = 0.0
    observation_pressure_samples: list[float] = []
    observation_recovery_samples: list[float] = []
    for target_stats in observation_target_stats.values():
        if not isinstance(target_stats, dict):
            continue
        recommended = max(0, int(target_stats.get("recommended") or 0))
        resolved = max(0, int(target_stats.get("resolved") or 0))
        stalled = max(0, int(target_stats.get("stalled") or 0))
        last_risk = clamp01(target_stats.get("last_risk") or 0.0)
        if recommended <= 0:
            continue
        unresolved_ratio = max(0.0, (recommended - resolved) / max(recommended, 1))
        recovery_ratio = resolved / max(recommended, 1)
        pressure_sample = last_risk * 0.04
        if recommended >= 2 or stalled > 0:
            pressure_sample += (
                unresolved_ratio * 0.12
                + min(stalled, 3) * 0.05
                + last_risk * 0.04
            )
        observation_pressure_samples.append(pressure_sample)
        observation_recovery_samples.append(recovery_ratio * 0.08)
    if observation_pressure_samples:
        unresolved_observation_pressure = clamp01(
            sum(observation_pressure_samples) / len(observation_pressure_samples)
            + min(0.06, max(0, len(observation_pressure_samples) - 1) * 0.02)
        )
    if observation_recovery_samples:
        observation_recovery_signal = clamp01(
            sum(observation_recovery_samples) / len(observation_recovery_samples)
        )

    agenda_drag_pressure = 0.0
    agenda_resolution_signal = 0.0
    for topic_stats in agenda_topic_stats.values():
        if not isinstance(topic_stats, dict):
            continue
        dragging = max(0, int(topic_stats.get("dragging") or 0))
        active_cycles = max(0, int(topic_stats.get("active_cycles") or 0))
        resolved = max(0, int(topic_stats.get("resolved") or 0))
        seen = max(0, int(topic_stats.get("seen") or 0))
        if seen <= 0:
            continue
        agenda_drag_pressure += (
            max(0.0, (dragging + max(active_cycles - resolved, 0)) / max(seen, 1))
            * 0.06
        )
        agenda_resolution_signal += (resolved / max(seen, 1)) * 0.05

    learning_expansion_bias = clamp01(
        0.52
        + (learning_success - 0.5) * 0.4
        + (0.08 if reflection.learning_yield_state == "strong" else 0.0)
        - reflection.api_b_judgement_blockage_pressure * 0.18
        + (focus_effectiveness_values["learning_expansion"] - 0.5) * 0.16
        - unresolved_observation_pressure * 0.22
        + observation_recovery_signal * 0.18
        + agenda_resolution_signal * 0.12
        - float(policy.get("dynamic_learning_expansion_suppression") or 0.0)
    )
    truthfulness_bias = clamp01(
        0.56
        + world_model.truthfulness_pressure * 0.32
        + max(0.0, 0.55 - learning_success) * 0.08
        + (focus_effectiveness_values["truthfulness"] - 0.5) * 0.18
        + min(
            0.18,
            sum(
                (
                    clamp01(stats.get("last_risk") or 0.0) * 0.1
                    + max(0, int(stats.get("stalled") or 0)) * 0.03
                )
                for target, stats in observation_target_stats.items()
                if target in {"truthfulness", "latent_truthfulness"}
                and isinstance(stats, dict)
            ),
        )
        + float(policy.get("dynamic_truthfulness_bias_boost") or 0.0)
    )
    memory_continuity_bias = clamp01(
        0.58
        + (memory_success - 0.5) * 0.18
        + world_model.memory_pressure * 0.22
        + (focus_effectiveness_values["memory_continuity"] - 0.5) * 0.14
    )
    governance_hygiene_bias = clamp01(
        0.44
        + reflection.api_b_judgement_blockage_pressure * 0.34
        + max(0.0, 0.5 - backlog_success) * 0.22
        + reflection.repeated_drive_pressure * 0.1
        + (focus_effectiveness_values["governance_hygiene"] - 0.45) * 0.16
        + min(
            0.16,
            sum(
                (
                    clamp01(stats.get("last_risk") or 0.0) * 0.08
                    + max(0, int(stats.get("stalled") or 0)) * 0.03
                )
                for target, stats in observation_target_stats.items()
                if target == _API_B_JUDGEMENT_BLOCKAGE
                and isinstance(stats, dict)
            ),
        )
        + agenda_drag_pressure * 0.08
    )
    body_growth_bias = clamp01(
        0.42
        + (body_success - 0.45) * 0.28
        + world_model.body_upgrade_readiness * 0.16
        + reflection.recent_learning_quality * 0.16
        - (0.18 if reflection.body_growth_blocked else 0.0)
        - reflection.api_b_judgement_blockage_pressure * 0.12
        + (focus_effectiveness_values["body_growth"] - 0.42) * 0.14
        - unresolved_observation_pressure * 0.08
    )
    historical_observation_pressure = 0.0
    historical_order_uncertain = (
        reflection.dominant_constraint == "historical_underdelivery"
        and not historical_has_temporal_markers
        and scoped_historical_total >= 7
        and scoped_historical_drag_ratio >= 0.6
    )
    if reflection.dominant_constraint == "historical_underdelivery":
        historical_observation_pressure = clamp01(
            0.1
            + max(0.0, scoped_historical_drag_ratio - 0.55) * 0.45
            + max(0.0, 0.42 - reflection.autonomy_readiness) * 0.4
            + (
                0.06
                if recent_relapse_drag_count >= 2
                and recent_relapse_drag_ratio >= 0.66
                else 0.0
            )
            + (0.08 if historical_order_uncertain else 0.0)
        )
    observation_bias = clamp01(
        0.3
        + reflection.api_b_judgement_blockage_pressure * 0.28
        + max(0.0, 0.52 - reflection.autonomy_readiness) * 0.45
        + max(0.0, 0.55 - learning_success) * 0.14
        + (focus_effectiveness_values["observation"] - 0.5) * 0.34
        + (0.22 if reflection.dominant_constraint == "weak_learning_yield" else 0.0)
        + (0.18 if reflection.dominant_constraint == "historical_underdelivery" else 0.0)
        + (
            observation_recovery_advantage * 0.28
            if reflection.dominant_constraint
            in {"weak_learning_yield", "historical_underdelivery"}
            else 0.0
        )
        + (
            0.08
            if contextual_observation_available
            and reflection.dominant_constraint
            in {"weak_learning_yield", "historical_underdelivery"}
            else 0.0
        )
        + unresolved_observation_pressure * 0.36
        - observation_recovery_signal * 0.18
        + agenda_drag_pressure * 0.12
        + recent_relapse_drag_ratio * 0.08
        + historical_observation_pressure
    )
    candidate_throttle = clamp01(
        0.18
        + reflection.api_b_judgement_blockage_pressure * 0.32
        + reflection.repeated_drive_pressure * 0.24
        + max(0.0, 0.5 - reflection.autonomy_readiness) * 0.3
        + max(0.0, 0.5 - focus_effectiveness_values["learning_expansion"]) * 0.06
        + max(0.0, 0.5 - focus_effectiveness_values["body_growth"]) * 0.04
        + (0.08 if reflection.dominant_constraint == "weak_learning_yield" else 0.0)
        + unresolved_observation_pressure * 0.34
        + agenda_drag_pressure * 0.1
        - observation_recovery_signal * 0.1
        + recent_relapse_drag_ratio * 0.12
        + float(policy.get("dynamic_candidate_throttle_boost") or 0.0)
    )
    observation_bias = clamp01(
        observation_bias + float(policy.get("dynamic_observation_bias_boost") or 0.0)
    )
    api_a_execution_flow_pressure = clamp01(
        perception.api_a_handoff_count * 0.14
        + perception.api_a_running_count * 0.24
    )
    if api_a_execution_flow_pressure > 0.0:
        learning_expansion_bias = clamp01(
            learning_expansion_bias - api_a_execution_flow_pressure * 0.08
        )
        body_growth_bias = clamp01(
            body_growth_bias - api_a_execution_flow_pressure * 0.22
        )
        observation_bias = clamp01(
            observation_bias + api_a_execution_flow_pressure * 0.06
        )
        candidate_throttle = clamp01(
            candidate_throttle + api_a_execution_flow_pressure * 0.18
        )

    focus_candidates = {
        "truthfulness": truthfulness_bias,
        "memory_continuity": memory_continuity_bias,
        "learning_expansion": learning_expansion_bias,
        "governance_hygiene": governance_hygiene_bias,
        "body_growth": body_growth_bias,
        "observation": observation_bias,
    }
    preferred_focus = max(focus_candidates.items(), key=lambda item: item[1])[0]
    if (
        reflection.dominant_constraint == "none"
        and 0 < perception.correction_signals < TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
        and truthfulness_bias >= memory_continuity_bias - 0.02
        and observation_bias < 0.68
        and candidate_throttle < 0.65
    ):
        preferred_focus = "truthfulness"
    if (
        reflection.dominant_constraint == "historical_underdelivery"
        and preferred_focus == "truthfulness"
        and observation_bias >= truthfulness_bias - 0.12
    ):
        preferred_focus = "observation"
    if (
        reflection.dominant_constraint == "historical_underdelivery"
        and preferred_focus == "memory_continuity"
        and not has_truthfulness_review_signal(perception)
        and reflection.autonomy_readiness < 0.4
        and observation_bias >= 0.56
        and memory_continuity_bias <= observation_bias + 0.1
    ):
        preferred_focus = "observation"
    if (
        has_memory_backlog_recovery_window(
            perception=perception,
            reflection=reflection,
        )
        and preferred_focus == "observation"
        and memory_continuity_bias >= max(0.6, truthfulness_bias - 0.02)
        and observation_bias <= memory_continuity_bias + 0.05
    ):
        preferred_focus = "memory_continuity"
    if (
        reflection.dominant_constraint == "historical_underdelivery"
        and observation_bias >= 0.72
        and preferred_focus == "memory_continuity"
    ):
        preferred_focus = "observation"
    if (
        historical_order_uncertain
        and preferred_focus == "memory_continuity"
        and observation_bias >= 0.64
    ):
        preferred_focus = "observation"
    if has_truthfulness_review_signal(perception):
        preferred_focus = "truthfulness"
    if (
        scoped_historical_drag_ratio >= 0.66
        and (
            preferred_focus == "observation"
            or reflection.autonomy_readiness <= 0.18
            or observation_bias >= 0.58
        )
    ):
        candidate_budget = 1
    elif historical_order_uncertain:
        candidate_budget = 1
    elif (
        reflection.dominant_constraint == "historical_underdelivery"
        and recent_relapse_drag_ratio >= 0.66
        and recent_relapse_drag_count >= 2
    ):
        candidate_budget = 1
    elif candidate_throttle >= 0.72:
        candidate_budget = 1
    elif candidate_throttle >= 0.45:
        candidate_budget = 2
    else:
        candidate_budget = 4
    if preferred_focus == "observation" or observation_bias >= 0.7:
        exploratory_learning_quota = 0
    elif candidate_throttle >= 0.65:
        exploratory_learning_quota = 0
    elif candidate_throttle >= 0.4 or preferred_focus == "governance_hygiene":
        exploratory_learning_quota = 1
    else:
        exploratory_learning_quota = 2
    if perception.api_a_running_count > 0:
        exploratory_learning_quota = 0
    elif perception.api_a_handoff_count > 0:
        exploratory_learning_quota = min(exploratory_learning_quota, 1)
    body_growth_quota = (
        1
        if (
            body_growth_bias >= 0.58
            and candidate_throttle < 0.62
            and preferred_focus in {"body_growth", "learning_expansion"}
        )
        else 0
    )
    if perception.api_a_handoff_count > 0 or perception.api_a_running_count > 0:
        body_growth_quota = 0

    rationale_parts = [
        f"preferred focus is {preferred_focus}",
        f"candidate throttle is {candidate_throttle:.2f}",
        f"candidate budget is {candidate_budget}",
        f"learning bias is {learning_expansion_bias:.2f}",
        f"governance hygiene bias is {governance_hygiene_bias:.2f}",
    ]
    if focus_stats:
        rationale_parts.append(
            f"strategy memory favors {preferred_focus} at {focus_effectiveness_values.get(preferred_focus, 0.5):.2f} effectiveness"
        )
    if contextual_focus_stats:
        rationale_parts.append(f"context posture memory is active for {context_key}")
    if observation_bias >= 0.6:
        rationale_parts.append("observation bias is elevated because autonomous output should slow down")
    if api_a_execution_flow_pressure > 0.0:
        rationale_parts.append("API-A 执行窗口仍在流动，因此先等待回流沉淀再扩大自主产出")

    return {
        "learning_expansion_bias": learning_expansion_bias,
        "truthfulness_bias": truthfulness_bias,
        "memory_continuity_bias": memory_continuity_bias,
        "governance_hygiene_bias": governance_hygiene_bias,
        "body_growth_bias": body_growth_bias,
        "observation_bias": observation_bias,
        "candidate_throttle": candidate_throttle,
        "candidate_budget": candidate_budget,
        "exploratory_learning_quota": exploratory_learning_quota,
        "body_growth_quota": body_growth_quota,
        "preferred_focus": preferred_focus,
        "rationale": "; ".join(rationale_parts) + ".",
        "source_evidence": [
            f"learning_success={learning_success:.2f}",
            f"backlog_success={backlog_success:.2f}",
            f"body_success={body_success:.2f}",
            f"memory_success={memory_success:.2f}",
            f"historical_drag_scope={scoped_historical_scope}",
            f"historical_drag_ratio={historical_drag_ratio:.2f}",
            f"historical_has_temporal_markers={historical_has_temporal_markers}",
            f"historical_order_uncertain={historical_order_uncertain}",
            f"scoped_historical_drag_ratio={scoped_historical_drag_ratio:.2f}",
            f"recent_relapse_drag_count={recent_relapse_drag_count}",
            f"recent_relapse_drag_ratio={recent_relapse_drag_ratio:.2f}",
            f"api_b_judgement_blockage_pressure={reflection.api_b_judgement_blockage_pressure:.2f}",
            f"autonomy_readiness={reflection.autonomy_readiness:.2f}",
            f"context_key={context_key}",
            f"observation_recovery_advantage={observation_recovery_advantage:.2f}",
            f"unresolved_observation_pressure={unresolved_observation_pressure:.2f}",
            f"observation_recovery_signal={observation_recovery_signal:.2f}",
            f"historical_observation_pressure={historical_observation_pressure:.2f}",
            f"agenda_drag_pressure={agenda_drag_pressure:.2f}",
            f"agenda_resolution_signal={agenda_resolution_signal:.2f}",
            f"dynamic_candidate_throttle_boost={float(policy.get('dynamic_candidate_throttle_boost') or 0.0):.2f}",
            f"dynamic_observation_bias_boost={float(policy.get('dynamic_observation_bias_boost') or 0.0):.2f}",
            f"dynamic_truthfulness_bias_boost={float(policy.get('dynamic_truthfulness_bias_boost') or 0.0):.2f}",
            f"dynamic_learning_expansion_suppression={float(policy.get('dynamic_learning_expansion_suppression') or 0.0):.2f}",
            f"api_a_execution_flow_pressure={api_a_execution_flow_pressure:.2f}",
            f"api_a_handoff_count={perception.api_a_handoff_count}",
            f"api_a_running_count={perception.api_a_running_count}",
            f"focus_effectiveness[{preferred_focus}]={focus_effectiveness_values.get(preferred_focus, 0.5):.2f}",
            f"candidate_budget={candidate_budget}",
            f"exploratory_learning_quota={exploratory_learning_quota}",
            f"body_growth_quota={body_growth_quota}",
        ],
    }
