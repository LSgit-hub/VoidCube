"""Explicit-input assembler for deterministic endogenous candidates."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Set

from systems.supervisor.endogenous_candidate_eligibility import (
    CandidateStreamEligibility,
)
from systems.supervisor.endogenous_candidate_factories import (
    build_body_improvement_candidate,
    build_governance_hygiene_review_candidate,
    build_memory_maintenance_candidate,
    build_truthfulness_review_candidate,
)
from systems.supervisor.endogenous_candidate_pipeline import (
    EndogenousTaskCandidate,
    apply_adaptive_candidate_budget,
    merge_lm_led_candidate_stream,
)
from systems.supervisor.endogenous_learning import (
    build_cognitive_assessment_review_candidate,
    build_exploratory_learning_candidate,
    build_shell_baseline_learning_candidate,
    extract_learning_topic,
    filter_learning_topics,
    idle_learning_urgency,
    stable_learning_topic_key,
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
