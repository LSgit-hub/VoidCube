"""Pure need detection policy for endogenous deliberation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol

from systems.supervisor.endogenous_candidate_pipeline import clamp01
from systems.supervisor.endogenous_materialization import (
    has_governance_hygiene_review_signal,
)
from systems.supervisor.endogenous_policy import (
    has_memory_backlog_recovery_window,
    has_truthfulness_review_signal,
)


REVIEW_API_B_JUDGEMENT_NEED = "review_api_b_judgement"


@dataclass(frozen=True, slots=True)
class DriveNeed:
    need_type: str
    severity: float
    urgency: float
    confidence: float
    rationale: str
    source_evidence: List[str] = field(default_factory=list)


class PerceptionNeedsInput(Protocol):
    correction_signals: int
    recent_errors: int
    uncertainty_count: int
    learning_quality: float
    has_learning_history: bool
    shell_slot_present: bool
    api_b_judgement_count: int
    learning_backlog_count: int
    body_improvement_backlog_count: int
    stale_backlog_count: int
    pending_review_count: int
    api_a_handoff_count: int
    api_a_running_count: int
    checks: Dict[str, Any]


class WorldModelNeedsInput(Protocol):
    truthfulness_pressure: float
    learning_momentum: float
    body_upgrade_readiness: float
    memory_pressure: float
    self_confidence: float


class ReflectionNeedsInput(Protocol):
    learning_yield_state: str
    api_b_judgement_blockage_pressure: float
    api_b_judgement_blockage_state: str
    body_growth_blocked: bool
    repeated_drive_pressure: float
    autonomy_readiness: float
    dominant_constraint: str
    recent_learning_quality: float


class AdaptivePolicyNeedsInput(Protocol):
    learning_expansion_bias: float
    truthfulness_bias: float
    memory_continuity_bias: float
    governance_hygiene_bias: float
    body_growth_bias: float
    observation_bias: float
    candidate_throttle: float
    candidate_budget: int
    preferred_focus: str


def detect_needs(
    *,
    perception: PerceptionNeedsInput,
    world_model: WorldModelNeedsInput,
    reflection: ReflectionNeedsInput,
    adaptive_policy: AdaptivePolicyNeedsInput,
    memory_plan: Dict[str, Any],
    self_learning_plan: Dict[str, Any],
    autonomous_improvement_plan: Dict[str, Any],
    governance_review_need_type: str = REVIEW_API_B_JUDGEMENT_NEED,
    historical_observation_carryover_released: bool = False,
    foundation_projection: Dict[str, Any] | None = None,
) -> List[DriveNeed]:
    needs: List[DriveNeed] = []
    truthfulness_review_active = (
        self_learning_plan.get("eligible_for_planning")
        and has_truthfulness_review_signal(perception)
    )
    memory_backlog_recovery_window = has_memory_backlog_recovery_window(
        perception=perception,
        reflection=reflection,
    )
    if memory_plan.get("eligible_for_planning"):
        memory_constraint_penalty = 0.0
        memory_recovery_bonus = 0.0
        if reflection.dominant_constraint == "historical_underdelivery":
            memory_constraint_penalty += 0.08
        if adaptive_policy.preferred_focus == "observation":
            memory_constraint_penalty += 0.06
        if (
            reflection.dominant_constraint == "none"
            and adaptive_policy.preferred_focus == "memory_continuity"
            and perception.pending_review_count <= 0
            and perception.stale_backlog_count <= 0
            and perception.api_b_judgement_count <= 0
            and not has_truthfulness_review_signal(perception)
            and reflection.learning_yield_state in {"mixed", "strong"}
        ):
            memory_constraint_penalty += 0.05
        if memory_backlog_recovery_window:
            memory_recovery_bonus += 0.12
        needs.append(
            DriveNeed(
                need_type="stabilize_memory_continuity",
                severity=clamp01(
                    world_model.memory_pressure
                    + 0.08
                    + adaptive_policy.memory_continuity_bias * 0.22
                    - memory_constraint_penalty
                    + memory_recovery_bonus
                ),
                urgency=clamp01(
                    world_model.memory_pressure
                    + 0.1
                    + adaptive_policy.memory_continuity_bias * 0.18
                    - memory_constraint_penalty * 0.82
                    + memory_recovery_bonus * 0.84
                ),
                confidence=clamp01(
                    0.68
                    + adaptive_policy.memory_continuity_bias * 0.22
                    - memory_constraint_penalty * 0.32
                    + memory_recovery_bonus * 0.18
                ),
                rationale="在全天候运行语义下，记忆连续性维护始终是监督者的常驻职责。",
                source_evidence=[
                    f"memory_idle={perception.checks.get('has_memory_idle', False)}",
                    f"memory_continuity_bias={adaptive_policy.memory_continuity_bias:.2f}",
                    f"memory_recovery_bonus={memory_recovery_bonus:.2f}",
                ],
            )
        )
    if self_learning_plan.get("eligible_for_planning") and perception.correction_signals > 0:
        truthfulness_priority_bonus = 0.0
        if truthfulness_review_active:
            truthfulness_priority_bonus += 0.08
            if adaptive_policy.preferred_focus == "truthfulness":
                truthfulness_priority_bonus += 0.04
        needs.append(
            DriveNeed(
                need_type="repair_truthfulness",
                severity=clamp01(
                    world_model.truthfulness_pressure
                    + adaptive_policy.truthfulness_bias * 0.16
                    + truthfulness_priority_bonus
                ),
                urgency=clamp01(
                    world_model.truthfulness_pressure
                    + adaptive_policy.truthfulness_bias * 0.12
                    + truthfulness_priority_bonus * 0.9
                ),
                confidence=clamp01(
                    0.72
                    + adaptive_policy.truthfulness_bias * 0.24
                    + truthfulness_priority_bonus * 0.45
                ),
                rationale="近期错误与高不确定性信号说明真实性债务正在累积，应该尽快浮出并进入复核。",
                source_evidence=[
                    f"correction_signals={perception.correction_signals}",
                    f"recent_errors={perception.recent_errors}",
                    f"uncertainty_count={perception.uncertainty_count}",
                    f"truthfulness_bias={adaptive_policy.truthfulness_bias:.2f}",
                ],
            )
        )
    if self_learning_plan.get("eligible_for_planning"):
        learning_constraint_penalty = 0.0
        learning_recovery_bonus = (
            0.03 if historical_observation_carryover_released else 0.0
        )
        if reflection.dominant_constraint == "historical_underdelivery":
            learning_constraint_penalty += 0.14
        if adaptive_policy.preferred_focus == "observation":
            learning_constraint_penalty += 0.08
        if truthfulness_review_active and adaptive_policy.preferred_focus == "truthfulness":
            learning_constraint_penalty += 0.06
        if memory_backlog_recovery_window:
            learning_constraint_penalty += 0.14
        learning_constraint_penalty += min(
            0.22,
            perception.api_a_handoff_count * 0.06
            + perception.api_a_running_count * 0.14,
        )
        needs.append(
            DriveNeed(
                need_type="expand_learning_frontier",
                severity=clamp01(
                    world_model.learning_momentum
                    - 0.02
                    + reflection.autonomy_readiness * 0.16
                    + adaptive_policy.learning_expansion_bias * 0.2
                    + learning_recovery_bonus
                    - reflection.api_b_judgement_blockage_pressure * 0.12
                    - learning_constraint_penalty
                ),
                urgency=clamp01(
                    world_model.learning_momentum
                    + reflection.recent_learning_quality * 0.15
                    + adaptive_policy.learning_expansion_bias * 0.1
                    + learning_recovery_bonus
                    - reflection.api_b_judgement_blockage_pressure * 0.08
                    - adaptive_policy.candidate_throttle * 0.12
                    - learning_constraint_penalty * 0.72
                ),
                confidence=clamp01(
                    world_model.self_confidence * 0.52
                    + reflection.autonomy_readiness * 0.22
                    + adaptive_policy.learning_expansion_bias * 0.26
                    + learning_recovery_bonus * 0.67
                    - learning_constraint_penalty * 0.46
                ),
                rationale=(
                    "当近期证据仍有增益时，学习应继续扩展；"
                    "但如果 API-B 判断在途阻塞已说明继续产出只会加压，就应主动降温。"
                ),
                source_evidence=[
                    f"learning_quality={perception.learning_quality:.2f}",
                    f"learning_backlog_count={perception.learning_backlog_count}",
                    f"has_learning_history={perception.has_learning_history}",
                    f"learning_yield_state={reflection.learning_yield_state}",
                    f"api_b_judgement_blockage_state={reflection.api_b_judgement_blockage_state}",
                    f"learning_expansion_bias={adaptive_policy.learning_expansion_bias:.2f}",
                    f"candidate_throttle={adaptive_policy.candidate_throttle:.2f}",
                    f"learning_constraint_penalty={learning_constraint_penalty:.2f}",
                    f"historical_observation_carryover_released={historical_observation_carryover_released}",
                    f"api_a_handoff_count={perception.api_a_handoff_count}",
                    f"api_a_running_count={perception.api_a_running_count}",
                ],
            )
        )
    if (
        autonomous_improvement_plan.get("eligible_for_planning")
        and perception.shell_slot_present
        and perception.learning_quality >= 60.0
        and not reflection.body_growth_blocked
        and perception.api_a_handoff_count <= 0
        and perception.api_a_running_count <= 0
    ):
        needs.append(
            DriveNeed(
                need_type="prepare_body_growth",
                severity=clamp01(
                    world_model.body_upgrade_readiness
                    - 0.02
                    + reflection.autonomy_readiness * 0.12
                    + adaptive_policy.body_growth_bias * 0.18
                ),
                urgency=clamp01(
                    world_model.body_upgrade_readiness
                    + reflection.recent_learning_quality * 0.08
                    + adaptive_policy.body_growth_bias * 0.1
                    - adaptive_policy.candidate_throttle * 0.08
                ),
                confidence=clamp01(
                    0.5
                    + world_model.self_confidence * 0.12
                    + reflection.autonomy_readiness * 0.1
                    + adaptive_policy.body_growth_bias * 0.28
                ),
                rationale="只有当近期学习确实产出有效收益，且替身改进没有被近期输出压力卡住时，才应准备自主改进。",
                source_evidence=[
                    f"learning_quality={perception.learning_quality:.2f}",
                    f"shell_slot_present={perception.shell_slot_present}",
                    f"body_improvement_backlog_count={perception.body_improvement_backlog_count}",
                    f"body_growth_blocked={reflection.body_growth_blocked}",
                    f"body_growth_bias={adaptive_policy.body_growth_bias:.2f}",
                    f"api_a_handoff_count={perception.api_a_handoff_count}",
                    f"api_a_running_count={perception.api_a_running_count}",
                ],
            )
        )
    if autonomous_improvement_plan.get("eligible_for_planning"):
        governance_review_active = has_governance_hygiene_review_signal(
            perception.pending_review_count,
            perception.stale_backlog_count,
            perception.api_b_judgement_count,
        )
        backlog_need_score = clamp01(
            0.2
            + (
                min(perception.api_b_judgement_count, 5) * 0.08
                if governance_review_active
                else 0.0
            )
            + min(perception.stale_backlog_count + perception.pending_review_count, 4) * 0.08
            + reflection.api_b_judgement_blockage_pressure * 0.18
            + adaptive_policy.governance_hygiene_bias * 0.16
        )
        needs.append(
            DriveNeed(
                need_type=governance_review_need_type,
                severity=backlog_need_score,
                urgency=clamp01(
                    backlog_need_score
                    - 0.02
                    + reflection.repeated_drive_pressure * 0.08
                    + adaptive_policy.governance_hygiene_bias * 0.12
                ),
                confidence=clamp01(
                    0.56
                    + reflection.api_b_judgement_blockage_pressure * 0.16
                    + adaptive_policy.governance_hygiene_bias * 0.22
                ),
                rationale="当内生输出反复出现却没有真正闭环、治理压力持续累积时，治理卫生复核就应被抬高优先级。",
                source_evidence=[
                    f"api_b_judgement_count={perception.api_b_judgement_count}",
                    f"stale_backlog_count={perception.stale_backlog_count}",
                    f"pending_review_count={perception.pending_review_count}",
                    f"repeated_drive_pressure={reflection.repeated_drive_pressure:.2f}",
                    f"governance_hygiene_bias={adaptive_policy.governance_hygiene_bias:.2f}",
                ],
            )
        )
    foundation_task_needs = {
        "fill_self_cognition": (
            "complete_self_cognition",
            "当前代码自我认知事实缺失或不完整，应先补齐只读快照。",
        ),
        "fill_research_knowledge": (
            "complete_research_knowledge",
            "当前外部知识事实缺失、过期或质量不足，应先补齐离线知识 artifact。",
        ),
        "run_evolution_evaluation": (
            "run_evolution_evaluation",
            "当前对比实验事实缺失或未形成稳定结论，应先补充影子评测证据。",
        ),
    }
    for task in list(dict(foundation_projection or {}).get("shadow_tasks") or []):
        if not isinstance(task, dict) or task.get("execution_allowed") is not False:
            continue
        task_kind = str(task.get("task_kind") or "").strip()
        need_definition = foundation_task_needs.get(task_kind)
        if need_definition is None:
            continue
        need_type, default_rationale = need_definition
        # Foundation suggestions remain shadow observations and cannot outrank
        # an existing operational need in the endogenous drive.
        priority = min(0.45, clamp01(task.get("priority") or 0.28))
        needs.append(
            DriveNeed(
                need_type=need_type,
                severity=priority,
                urgency=priority,
                confidence=0.9,
                rationale=str(task.get("rationale") or default_rationale),
                source_evidence=[
                    "foundation_mode=shadow_read_only",
                    f"foundation_task={task_kind}",
                    *[
                        str(item)
                        for item in list(task.get("evidence_refs") or [])
                        if str(item).strip()
                    ],
                    *[
                        f"foundation_trigger={str(item)}"
                        for item in list(task.get("trigger_reasons") or [])
                        if str(item).strip()
                    ],
                ],
            )
        )
    if (
        reflection.api_b_judgement_blockage_pressure >= 0.45
        or reflection.autonomy_readiness <= 0.42
        or adaptive_policy.observation_bias >= 0.58
        or (
            reflection.dominant_constraint == "historical_underdelivery"
            and adaptive_policy.observation_bias >= 0.68
        )
    ):
        observation_constraint_bonus = 0.0
        if reflection.dominant_constraint == "historical_underdelivery":
            observation_constraint_bonus += 0.08
            if adaptive_policy.observation_bias >= 0.72:
                observation_constraint_bonus += 0.06
            if int(adaptive_policy.candidate_budget) <= 1:
                observation_constraint_bonus += 0.04
        if adaptive_policy.preferred_focus == "observation":
            observation_constraint_bonus += 0.06
        observation_release_penalty = 0.0
        if historical_observation_carryover_released:
            observation_release_penalty += 0.04
        if (
            memory_backlog_recovery_window
            and adaptive_policy.memory_continuity_bias
            >= max(0.58, adaptive_policy.truthfulness_bias - 0.02)
        ):
            observation_release_penalty += 0.08
            if adaptive_policy.preferred_focus == "observation":
                observation_release_penalty += 0.04
            if adaptive_policy.observation_bias <= adaptive_policy.memory_continuity_bias + 0.04:
                observation_release_penalty += 0.04
        needs.append(
            DriveNeed(
                need_type="observe_before_acting",
                severity=clamp01(
                    0.34
                    + reflection.api_b_judgement_blockage_pressure * 0.32
                    + max(0.0, 0.5 - reflection.autonomy_readiness) * 0.45
                    + adaptive_policy.observation_bias * 0.18
                    + observation_constraint_bonus
                    - observation_release_penalty
                ),
                urgency=clamp01(
                    0.28
                    + reflection.api_b_judgement_blockage_pressure * 0.28
                    + max(0.0, 0.45 - reflection.autonomy_readiness) * 0.4
                    + adaptive_policy.observation_bias * 0.14
                    + observation_constraint_bonus * 0.85
                    - observation_release_penalty * 0.82
                ),
                confidence=clamp01(
                    0.62
                    + adaptive_policy.observation_bias * 0.28
                    - observation_release_penalty * 0.32
                ),
                rationale="当重复产出持续撞上阻塞，或自主就绪度还不够稳时，内生驱动应主动放慢并先补观察。",
                source_evidence=[
                    f"api_b_judgement_blockage_pressure={reflection.api_b_judgement_blockage_pressure:.2f}",
                    f"autonomy_readiness={reflection.autonomy_readiness:.2f}",
                    f"dominant_constraint={reflection.dominant_constraint}",
                    f"observation_bias={adaptive_policy.observation_bias:.2f}",
                    f"observation_release_penalty={observation_release_penalty:.2f}",
                ],
            )
        )
    needs.sort(
        key=lambda item: (
            item.severity * 0.45
            + item.urgency * 0.35
            + item.confidence * 0.20
        ),
        reverse=True,
    )
    return needs
