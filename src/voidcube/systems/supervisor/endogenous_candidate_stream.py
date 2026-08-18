"""Explicit-input assembler for deterministic endogenous candidates."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set

from .endogenous_body_projection import (
    build_body_improvement_projection,
)
from .endogenous_candidate_eligibility import (
    CandidateStreamEligibility,
    resolve_candidate_stream_eligibility,
)
from .endogenous_candidate_factories import (
    build_body_improvement_candidate,
    build_governance_hygiene_review_candidate,
    build_memory_maintenance_candidate,
    build_truthfulness_review_candidate,
)
from .endogenous_candidate_pipeline import (
    EndogenousTaskCandidate,
    apply_adaptive_candidate_budget,
    merge_lm_led_candidate_stream,
)
from .endogenous_cognitive_memory import (
    build_cognitive_assessment_memory,
    build_self_iteration_trend_memory,
)
from .endogenous_deliberation import build_deliberation_report
from .endogenous_drive_context import (
    build_drive_context,
    get_shell_slot_meta,
)
from .endogenous_drive_judgement import (
    build_drive_judgement_metadata,
)
from .endogenous_materialization import (
    has_governance_hygiene_review_signal,
    has_historical_governance_hygiene_review_signal,
    resolve_candidate_eligibility_plan,
)
from .endogenous_pressure import (
    build_backlog_pressure_penalties,
    governance_hygiene_urgency,
    memory_maintenance_urgency,
)
from .endogenous_policy import has_truthfulness_review_signal
from .endogenous_learning import (
    build_cognitive_assessment_review_candidate,
    build_exploratory_learning_candidate,
    build_shell_baseline_learning_candidate,
    extract_learning_topic,
    filter_learning_topics,
    idle_learning_urgency,
    stable_learning_topic_key,
)


def prepare_candidate_stream(
    *,
    drive_input: Dict[str, Any],
    existing_keys: Set[str],
    deliberation_report: Any = None,
) -> Dict[str, Any]:
    activity = dict(drive_input.get("activity") or {})
    drive_context = build_drive_context(drive_input)
    policy = drive_context["policy"]
    shell_slot_meta = get_shell_slot_meta(drive_input)
    decisions_by_family = dict(drive_input.get("task_family_decisions") or {})
    decisions_by_governance = dict(
        drive_input.get("governance_task_type_decisions") or {}
    )
    memory_plan = resolve_candidate_eligibility_plan(
        "memory_maintenance", decisions_by_family, decisions_by_governance
    )
    self_learning_plan = resolve_candidate_eligibility_plan(
        "self_learning", decisions_by_family, decisions_by_governance
    )
    autonomous_improvement_plan = resolve_candidate_eligibility_plan(
        "general_self_evolution", decisions_by_family, decisions_by_governance
    )
    deliberation = deliberation_report or build_deliberation_report(
        drive_input=drive_input
    )
    perception = deliberation.perception
    body_projection = build_body_improvement_projection(
        drive_context=drive_context,
        shell_slot_meta=shell_slot_meta,
    )
    governance_signal_present = has_governance_hygiene_review_signal(
        perception.pending_review_count,
        perception.stale_backlog_count,
        perception.api_b_judgement_count,
    ) or has_historical_governance_hygiene_review_signal(
        list(dict(drive_context.get("drive_history") or {}).get("outcomes") or [])
    )
    eligibility = resolve_candidate_stream_eligibility(
        api_b_judgement_tasks=list(drive_context.get("api_b_judgement_tasks") or []),
        existing_keys=existing_keys,
        memory_planning_eligible=bool(memory_plan.get("eligible_for_planning")),
        self_learning_planning_eligible=bool(
            self_learning_plan.get("eligible_for_planning")
        ),
        autonomous_improvement_planning_eligible=bool(
            autonomous_improvement_plan.get("eligible_for_planning")
        ),
        truthfulness_signal_present=has_truthfulness_review_signal(perception),
        shell_slot_id=str(shell_slot_meta.get("slot_id") or "shell"),
        shell_worktree=str(shell_slot_meta.get("worktree_path") or ""),
        has_learning_history=perception.has_learning_history,
        governance_signal_present=governance_signal_present,
        body_projection_available=bool(body_projection.get("available")),
        body_growth_blocked=deliberation.reflection.body_growth_blocked,
        body_growth_quota=deliberation.adaptive_policy.body_growth_quota,
        memory_maintenance_status=dict(
            drive_input.get("memory_maintenance_status") or {}
        ),
    )
    backlog_pressure_penalties = build_backlog_pressure_penalties(drive_context)
    intents_by_kind = {
        str(intent.candidate_kind or ""): intent
        for intent in deliberation.intents
        if intent.candidate_kind
    }
    drive_judgements = {
        candidate_kind: build_drive_judgement_metadata(
            intent=intents_by_kind.get(candidate_kind),
            candidate_kind=candidate_kind,
            all_intents=list(deliberation.intents),
            needs=list(deliberation.needs),
            perception=deliberation.perception,
            world_model=deliberation.world_model,
            reflection=deliberation.reflection,
            adaptive_policy=deliberation.adaptive_policy,
        )
        for candidate_kind in (
            "memory_maintenance",
            "truthfulness_review",
            "shell_baseline_learning",
            "exploratory_learning",
            "governance_hygiene_review",
            "body_improvement",
        )
    }
    if eligibility.shell_baseline_learning or eligibility.exploratory_learning:
        cognitive_assessment_memory = build_cognitive_assessment_memory(drive_context)
        self_iteration_trend_memory = build_self_iteration_trend_memory(drive_context)
    else:
        cognitive_assessment_memory = {}
        self_iteration_trend_memory = {}
    return {
        "drive_input": drive_input,
        "activity": activity,
        "drive_context": drive_context,
        "policy": policy,
        "shell_slot_meta": shell_slot_meta,
        "existing_keys": existing_keys,
        "perception": perception,
        "adaptive_policy": deliberation.adaptive_policy,
        "eligibility": eligibility,
        "body_projection": body_projection,
        "cognitive_assessment_memory": cognitive_assessment_memory,
        "self_iteration_trend_memory": self_iteration_trend_memory,
        "backlog_pressure_penalties": backlog_pressure_penalties,
        "memory_maintenance_urgency": memory_maintenance_urgency(drive_input),
        "governance_hygiene_urgency": governance_hygiene_urgency(drive_context),
        "drive_judgements": drive_judgements,
        "deliberation": deliberation,
        "memory_plan": memory_plan,
        "self_learning_plan": self_learning_plan,
        "autonomous_improvement_plan": autonomous_improvement_plan,
    }


def assemble_prepared_candidate_stream(
    *,
    preparation: Mapping[str, Any],
    lm_candidates: List[EndogenousTaskCandidate],
) -> List[EndogenousTaskCandidate]:
    return build_candidate_stream(
        drive_input=dict(preparation["drive_input"]),
        activity=dict(preparation["activity"]),
        drive_context=dict(preparation["drive_context"]),
        policy=dict(preparation["policy"]),
        shell_slot_meta=dict(preparation["shell_slot_meta"]),
        existing_keys=preparation["existing_keys"],
        perception=preparation["perception"],
        adaptive_policy=preparation["adaptive_policy"],
        eligibility=preparation["eligibility"],
        body_projection=dict(preparation["body_projection"]),
        lm_candidates=lm_candidates,
        cognitive_assessment_memory=dict(
            preparation["cognitive_assessment_memory"]
        ),
        self_iteration_trend_memory=dict(
            preparation["self_iteration_trend_memory"]
        ),
        backlog_pressure_penalties=preparation["backlog_pressure_penalties"],
        memory_maintenance_urgency=preparation["memory_maintenance_urgency"],
        governance_hygiene_urgency=preparation["governance_hygiene_urgency"],
        drive_judgements=preparation["drive_judgements"],
    )


def build_candidate_stream(
    *,
    drive_input: Dict[str, Any],
    activity: Dict[str, Any],
    drive_context: Dict[str, Any],
    policy: Dict[str, Any],
    shell_slot_meta: Dict[str, Any],
    existing_keys: Set[str],
    perception: Any,
    adaptive_policy: Any,
    eligibility: CandidateStreamEligibility,
    body_projection: Dict[str, Any],
    lm_candidates: List[EndogenousTaskCandidate],
    cognitive_assessment_memory: Dict[str, Any],
    self_iteration_trend_memory: Dict[str, Any],
    backlog_pressure_penalties: Mapping[str, float],
    memory_maintenance_urgency: float,
    governance_hygiene_urgency: float,
    drive_judgements: Mapping[str, Dict[str, Any]],
) -> List[EndogenousTaskCandidate]:
    candidates: List[EndogenousTaskCandidate] = []

    if eligibility.memory_maintenance:
        candidates.append(
            build_memory_maintenance_candidate(
                urgency=memory_maintenance_urgency,
                backlog_pressure_penalty=backlog_pressure_penalties["memory_maintenance"],
                adaptive_policy=adaptive_policy,
                drive_judgement=drive_judgements["memory_maintenance"],
                observation_checks=dict(drive_input.get("checks") or {}),
                idle_seconds=dict(drive_input.get("idle_seconds") or {}),
            )
        )

    if eligibility.truthfulness_review:
        candidates.append(
            build_truthfulness_review_candidate(
                recent_errors=perception.recent_errors,
                uncertainty_count=perception.uncertainty_count,
                correction_signals=perception.correction_signals,
                runtime_signal_present=drive_input.get("correction_signals") is not None,
                backlog_pressure_penalty=backlog_pressure_penalties["self_learning"],
                adaptive_policy=adaptive_policy,
                drive_judgement=drive_judgements["truthfulness_review"],
            )
        )

    if eligibility.shell_baseline_learning or eligibility.exploratory_learning:
        active_sessions = perception.active_sessions
        shell_slot_id = str(shell_slot_meta.get("slot_id") or "shell").strip()
        shell_worktree = str(shell_slot_meta.get("worktree_path") or "").strip()
        baseline_key = f"creativity:self_learning:shell_baseline:{shell_slot_id or 'shell'}"
        autonomous_chain_gate_active = drive_input.get("autonomous_chain_gate_active", False)
        mechanical_topic = extract_learning_topic(activity)
        topics: list[dict] = []
        if mechanical_topic:
            topics = [
                {
                    "title": mechanical_topic,
                    "summary": (
                        f"Use autonomous-chain capacity to research '{mechanical_topic}' — the most recent "
                        "user-discussed topic that may benefit from deeper investigation."
                    ),
                }
            ]
        topics = filter_learning_topics(
            topics,
            drive_context=drive_context,
            existing_keys=existing_keys,
            cooldown_hours=int(policy.get("learning_topic_cooldown_hours", 24) or 24),
            overlap_threshold=float(policy.get("topic_overlap_threshold", 0.6) or 0.6),
            max_topics=3,
        )

        if shell_worktree and not perception.has_learning_history and eligibility.shell_baseline_learning:
            candidates.append(
                build_shell_baseline_learning_candidate(
                    stable_key=baseline_key,
                    active_sessions=active_sessions,
                    shell_slot_id=shell_slot_id,
                    shell_worktree=shell_worktree,
                    trigger="bootstrap_shell_baseline",
                    bootstrap=True,
                    urgency=idle_learning_urgency(
                        active_sessions=active_sessions,
                        topic_source="shell_baseline_bootstrap",
                        autonomous_chain_gate=False,
                    ),
                    backlog_pressure_penalty=backlog_pressure_penalties["self_learning"],
                    drive_judgement=drive_judgements["shell_baseline_learning"],
                    adaptive_policy=adaptive_policy,
                )
            )
            existing_keys.add(baseline_key)

        generated_count = 0
        for topic in topics:
            topic_key = stable_learning_topic_key(topic["title"])
            if topic_key in existing_keys or not eligibility.exploratory_learning:
                continue
            candidates.append(
                build_exploratory_learning_candidate(
                    topic=topic,
                    active_sessions=active_sessions,
                    urgency=idle_learning_urgency(
                        active_sessions=active_sessions,
                        topic_source="activity_metadata",
                        autonomous_chain_gate=autonomous_chain_gate_active,
                    ),
                    backlog_pressure_penalty=backlog_pressure_penalties["self_learning"],
                    adaptive_policy=adaptive_policy,
                    drive_judgement=drive_judgements["exploratory_learning"],
                )
            )
            existing_keys.add(topic_key)
            generated_count += 1
            if generated_count >= 2:
                break

        if generated_count == 0 and cognitive_assessment_memory.get("available"):
            target = str(
                cognitive_assessment_memory.get("self_iteration_target")
                or self_iteration_trend_memory.get("dominant_target")
                or adaptive_policy.preferred_focus
                or "endogenous_judgement"
            ).strip()
            judgement = str(
                cognitive_assessment_memory.get("current_judgement")
                or cognitive_assessment_memory.get("dominant_constraint")
                or "recent endogenous judgement"
            ).strip()
            review_key = (
                "creativity:self_learning:cognitive_review:"
                f"{stable_learning_topic_key(target or judgement)}"
            )
            if review_key not in existing_keys and eligibility.exploratory_learning:
                candidates.append(
                    build_cognitive_assessment_review_candidate(
                        target=target,
                        judgement=judgement,
                        cognitive_assessment_memory=cognitive_assessment_memory,
                        active_sessions=active_sessions,
                        preferred_focus=adaptive_policy.preferred_focus,
                        backlog_pressure_penalty=backlog_pressure_penalties["self_learning"],
                        adaptive_policy=adaptive_policy,
                        drive_judgement=drive_judgements["exploratory_learning"],
                    )
                )
                existing_keys.add(review_key)

    if eligibility.governance_hygiene_review:
        candidates.append(
            build_governance_hygiene_review_candidate(
                urgency=governance_hygiene_urgency,
                api_b_judgement_count=int(drive_context.get("api_b_judgement_count") or 0),
                adaptive_policy=adaptive_policy,
                drive_judgement=drive_judgements["governance_hygiene_review"],
            )
        )

    if eligibility.body_improvement:
        stable_key = f"creativity:body_improvement:{body_projection['mapping_key']}"
        if stable_key not in existing_keys:
            candidates.append(
                build_body_improvement_candidate(
                    body_projection=body_projection,
                    backlog_pressure_penalty=backlog_pressure_penalties["body_improvement"],
                    adaptive_policy=adaptive_policy,
                    drive_judgement=drive_judgements["body_improvement"],
                )
            )
            existing_keys.add(stable_key)

    if lm_candidates:
        candidates = merge_lm_led_candidate_stream(
            lm_candidates=lm_candidates,
            heuristic_candidates=candidates,
            adaptive_policy=adaptive_policy,
        )
    return apply_adaptive_candidate_budget(
        candidates,
        adaptive_policy=adaptive_policy,
    )
