"""Pure LM proposal eligibility, cognitive scoring, and candidate materialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from systems.supervisor.endogenous_candidate_factories import body_improvement_constraints
from systems.supervisor.endogenous_candidate_pipeline import (
    AdaptivePolicyLike,
    EndogenousTaskCandidate,
    active_api_b_judgement_candidate_kinds,
    adaptive_factor_for_candidate,
    build_scored_candidate,
    clamp01,
)
from systems.supervisor.endogenous_learning import stable_learning_topic_key
from systems.supervisor.endogenous_proposals import (
    NormalizedLmProposal,
    constraints_for_lm_candidate_kind,
    normalize_lm_proposal,
)


@dataclass(frozen=True, slots=True)
class LmCandidateKindSpec:
    stable_prefix: str
    governance_task_type: str
    task_family: str
    execution_kind: Optional[str]
    value_tags: tuple[str, ...]


LM_CANDIDATE_KIND_SPECS: Dict[str, LmCandidateKindSpec] = {
    "memory_maintenance": LmCandidateKindSpec(
        "lm:continuity:memory_maintenance",
        "memory_maintenance",
        "memory_maintenance",
        "memory_maintenance",
        ("continuity",),
    ),
    "truthfulness_review": LmCandidateKindSpec(
        "lm:truthfulness:review",
        "self_learning",
        "self_learning",
        None,
        ("truthfulness",),
    ),
    "exploratory_learning": LmCandidateKindSpec(
        "lm:creativity:exploratory",
        "self_learning",
        "self_learning",
        None,
        ("creativity",),
    ),
    "shell_baseline_learning": LmCandidateKindSpec(
        "lm:creativity:shell_baseline",
        "self_learning",
        "self_learning",
        None,
        ("creativity",),
    ),
    "governance_hygiene_review": LmCandidateKindSpec(
        "lm:continuity:governance_hygiene",
        "self_evolution",
        "general_self_evolution",
        "general_self_evolution",
        ("continuity", "truthfulness"),
    ),
    "body_improvement": LmCandidateKindSpec(
        "lm:creativity:body_improvement",
        "self_evolution",
        "body_upgrade",
        "body_improvement",
        ("creativity", "continuity"),
    ),
}


def resolve_candidate_eligibility_plan(
    family: str,
    decisions_by_family: Dict[str, Any],
    decisions_by_governance: Dict[str, Any],
) -> Dict[str, Any]:
    if family in decisions_by_family:
        return dict(decisions_by_family[family] or {})
    governance = (
        family
        if family in {"memory_maintenance", "self_learning", "user"}
        else "self_evolution"
    )
    return dict(decisions_by_governance.get(governance) or {})


def has_governance_hygiene_review_signal(
    pending_review_count: int,
    stale_backlog_count: int,
    api_b_judgement_count: int,
) -> bool:
    return (
        pending_review_count > 0
        or stale_backlog_count > 0
        or api_b_judgement_count > 3
    )


def has_historical_governance_hygiene_review_signal(
    outcomes: List[Dict[str, Any]],
) -> bool:
    dragging = 0
    for item in outcomes[:12]:
        if not isinstance(item, dict):
            continue
        family = str(
            item.get("task_family")
            or item.get("governance_task_type")
            or ""
        ).strip().lower()
        if family not in {"general_self_evolution", "self_evolution"}:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {
            "approved",
            "deferred",
            "paused",
            "awaiting_review",
            "awaiting_user_consent",
            "retry",
        }:
            dragging += 1
        if dragging >= 2:
            return True
    return False


def eligible_lm_candidate_kinds(
    *,
    active_candidate_kinds: set[str],
    self_evolution_eligible: bool,
    body_projection_available: bool,
    body_growth_quota: int,
    governance_signal_present: bool,
) -> set[str]:
    eligible = set(LM_CANDIDATE_KIND_SPECS) - set(active_candidate_kinds)
    if (
        not self_evolution_eligible
        or not body_projection_available
        or body_growth_quota <= 0
    ):
        eligible.discard("body_improvement")
    if not governance_signal_present:
        eligible.discard("governance_hygiene_review")
    return eligible


def resolve_lm_candidate_eligibility(
    *,
    api_b_judgement_tasks: List[Dict[str, Any]],
    self_evolution_eligible: bool,
    body_projection_available: bool,
    body_growth_quota: int,
    pending_review_count: int,
    stale_backlog_count: int,
    api_b_judgement_count: int,
    historical_outcomes: List[Dict[str, Any]],
) -> set[str]:
    active_candidate_kinds = active_api_b_judgement_candidate_kinds(
        api_b_judgement_tasks
    )
    governance_signal_present = has_governance_hygiene_review_signal(
        pending_review_count,
        stale_backlog_count,
        api_b_judgement_count,
    ) or has_historical_governance_hygiene_review_signal(historical_outcomes)
    return eligible_lm_candidate_kinds(
        active_candidate_kinds=active_candidate_kinds,
        self_evolution_eligible=self_evolution_eligible,
        body_projection_available=body_projection_available,
        body_growth_quota=body_growth_quota,
        governance_signal_present=governance_signal_present,
    )


def score_lm_proposal_cognitive_alignment(
    *,
    candidate_kind: str,
    task_type: str,
    evidence_level: str,
    risk_level: str,
    observation_required: bool,
    execution_mode: str,
    blocking_factors: List[str],
    reference_alignment: Dict[str, Any],
    evidence_packet: Dict[str, Any],
    posture_alignment: List[str],
    priority_basis: List[str],
) -> Dict[str, Any]:
    task_type_priors = dict(evidence_packet.get("task_type_priors") or {})
    priors = [
        dict(item)
        for item in list(task_type_priors.get("priors") or [])
        if isinstance(item, dict)
    ]
    prior_map = {
        str(item.get("task_type") or "").strip(): dict(item)
        for item in priors
        if str(item.get("task_type") or "").strip()
    }
    prior_row = prior_map.get(task_type, {})
    prior_score = clamp01(prior_row.get("score") or 0.0)
    top_priority_task_type = str(
        task_type_priors.get("top_priority_task_type") or ""
    ).strip()
    top_priority_score = clamp01(task_type_priors.get("top_priority_score") or 0.0)

    credibility = dict(evidence_packet.get("evidence_credibility_summary") or {})
    weak_channels = [
        str(item).strip()
        for item in list(credibility.get("weak_or_missing_channels") or [])
        if str(item).strip()
    ]
    high_channels = [
        str(item).strip()
        for item in list(credibility.get("high_credibility_channels") or [])
        if str(item).strip()
    ]
    self_model_snapshot = dict(evidence_packet.get("self_model_snapshot") or {})
    cognitive_posture = dict(evidence_packet.get("cognitive_posture") or {})
    self_gaps = [
        str(item).strip()
        for item in list(self_model_snapshot.get("self_understanding_gaps") or [])
        if str(item).strip()
    ]
    reasons: List[str] = []
    score = 0.34

    if top_priority_task_type and task_type == top_priority_task_type:
        score += 0.26
        reasons.append("matches_program_top_task_type_prior")
    elif prior_score >= 0.55:
        score += 0.16
        reasons.append("matches_high_program_task_type_prior")
    else:
        score -= 0.06
        reasons.append("task_type_is_not_favored_by_current_program_priors")

    alignment_score = clamp01(reference_alignment.get("alignment_score") or 0.0)
    score += alignment_score * 0.18
    if alignment_score >= 0.75:
        reasons.append("reference_alignment_is_strong")
    elif alignment_score < 0.5:
        score -= 0.08
        reasons.append("reference_alignment_is_weak")
    grounding_penalty = clamp01(reference_alignment.get("grounding_penalty") or 0.0)
    if grounding_penalty > 0.0:
        score -= grounding_penalty * 0.35
        reasons.append("reference_grounding_penalty_is_active")
    missing_primary_evidence_nodes = [
        str(item).strip()
        for item in list(reference_alignment.get("missing_primary_evidence_nodes") or [])[:4]
        if str(item).strip()
    ]
    missing_primary_agenda_nodes = [
        str(item).strip()
        for item in list(reference_alignment.get("missing_primary_agenda_nodes") or [])[:4]
        if str(item).strip()
    ]
    if missing_primary_evidence_nodes:
        score -= 0.11
        reasons.append("proposal_does_not_bind_primary_evidence_nodes")
    if missing_primary_agenda_nodes:
        score -= 0.11
        reasons.append("proposal_does_not_bind_primary_agenda_nodes")
    if not reference_alignment.get("matched_evidence_nodes"):
        score -= 0.08
        reasons.append("proposal_does_not_reference_evidence_graph")
    if not reference_alignment.get("matched_agenda_nodes"):
        score -= 0.08
        reasons.append("proposal_does_not_reference_agenda_graph")

    if task_type == "improvement":
        if self_gaps:
            score -= 0.1
            reasons.append("improvement_is_early_while_self_model_gaps_remain")
        if weak_channels:
            score -= 0.08
            reasons.append("improvement_runs_against_weak_or_missing_channels")
        if evidence_level == "strong" and risk_level != "high" and not weak_channels:
            score += 0.08
            reasons.append("improvement_has_strong_enough_evidence")

    if task_type in {"observation", "review"} and weak_channels:
        score += 0.08
        reasons.append("conservative_task_type_matches_weak_channel_context")
    if task_type == "learning" and self_gaps:
        score += 0.07
        reasons.append("learning_can_reduce_current_self_model_gaps")
    if evidence_level == "weak" and task_type in {"observation", "review"}:
        score += 0.06
        reasons.append("weak_evidence_is_handled_conservatively")
    if evidence_level == "weak" and task_type == "improvement":
        score -= 0.12
        reasons.append("weak_evidence_conflicts_with_improvement_shape")

    if risk_level == "high" and execution_mode == "guarded_execution":
        score += 0.05
        reasons.append("high_risk_is_at_least_guarded")
    elif risk_level == "high":
        score -= 0.08
        reasons.append("high_risk_is_not_guarded_enough")
    if observation_required and task_type in {"observation", "review"}:
        score += 0.04
        reasons.append("observation_requirement_matches_task_type")
    if blocking_factors and task_type in {"observation", "review"}:
        score += 0.03
        reasons.append("blocking_factors_are_handled_with_conservative_task_shape")
    if candidate_kind == "body_improvement" and weak_channels:
        score -= 0.06
        reasons.append("body_improvement_should_wait_for_stronger_channels")

    posture_name = str(cognitive_posture.get("name") or "").strip().lower()
    if posture_alignment:
        score += 0.05
        reasons.append("proposal_explicitly_states_posture_alignment")
    if priority_basis:
        score += 0.04
        reasons.append("proposal_explicitly_states_priority_basis")
    if posture_name == "truthfulness_first" and task_type == "review":
        score += 0.06
        reasons.append("task_shape_matches_truthfulness_first_posture")
    elif posture_name == "evidence_repair_first" and task_type in {"review", "observation"}:
        score += 0.06
        reasons.append("task_shape_matches_evidence_repair_first_posture")
    elif posture_name == "observe_first" and task_type in {"observation", "review"}:
        score += 0.06
        reasons.append("task_shape_matches_observe_first_posture")
    elif posture_name == "conservative" and task_type in {"maintenance", "observation", "review"}:
        score += 0.05
        reasons.append("task_shape_matches_conservative_posture")
    elif posture_name in {"truthfulness_first", "evidence_repair_first", "observe_first"} and task_type == "improvement":
        score -= 0.08
        reasons.append("task_shape_conflicts_with_current_cognitive_posture")

    score = clamp01(score)
    quality = "strong" if score >= 0.7 else "partial" if score >= 0.45 else "weak"
    return {
        "score": round(score, 4),
        "quality": quality,
        "task_type_prior_score": round(prior_score, 4),
        "top_priority_task_type": top_priority_task_type,
        "top_priority_score": round(top_priority_score, 4),
        "weak_or_missing_channels": weak_channels,
        "high_credibility_channels": high_channels,
        "self_understanding_gaps": self_gaps,
        "reasons": reasons[:8],
        "summary": (
            f"Proposal cognitive alignment is {quality} "
            f"(score={score:.2f}) against current program-side priors and evidence posture."
        ),
    }


BacklogPressure = Callable[[str, str, Optional[str]], float]
DriveJudgement = Callable[[str], Dict[str, Any]]


def materialize_lm_proposals(
    *,
    proposals: List[Dict[str, Any]],
    existing_keys: set[str],
    evidence_graph: Dict[str, Any],
    agenda_graph: Dict[str, Any],
    evidence_packet: Dict[str, Any],
    batch_cognitive_assessment: Dict[str, Any],
    adaptive_policy: AdaptivePolicyLike,
    body_projection: Dict[str, Any],
    eligible_candidate_kinds: set[str],
    active_sessions: int,
    backlog_pressure: BacklogPressure,
    drive_judgement: DriveJudgement,
) -> List[EndogenousTaskCandidate]:
    realized: List[EndogenousTaskCandidate] = []
    for item in proposals:
        candidate_kind = str(item.get("candidate_kind") or "").strip()
        if candidate_kind not in eligible_candidate_kinds:
            continue
        normalized = normalize_lm_proposal(
            item,
            evidence_graph=evidence_graph,
            agenda_graph=agenda_graph,
        )
        if normalized is None:
            continue
        candidate = materialize_lm_proposal(
            proposal=normalized,
            existing_keys=existing_keys,
            evidence_packet=evidence_packet,
            batch_cognitive_assessment=batch_cognitive_assessment,
            adaptive_policy=adaptive_policy,
            body_projection=body_projection,
            active_sessions=active_sessions,
            backlog_pressure=backlog_pressure,
            drive_judgement=drive_judgement,
        )
        if candidate is None:
            continue
        realized.append(candidate)
        existing_keys.add(candidate.stable_key)
    return realized


def materialize_lm_proposal(
    *,
    proposal: NormalizedLmProposal,
    existing_keys: set[str],
    evidence_packet: Dict[str, Any],
    batch_cognitive_assessment: Dict[str, Any],
    adaptive_policy: AdaptivePolicyLike,
    body_projection: Dict[str, Any],
    active_sessions: int,
    backlog_pressure: BacklogPressure,
    drive_judgement: DriveJudgement,
) -> Optional[EndogenousTaskCandidate]:
    spec = LM_CANDIDATE_KIND_SPECS.get(proposal.candidate_kind)
    if spec is None:
        return None
    stable_key = f"{spec.stable_prefix}:{stable_learning_topic_key(proposal.title)}"
    body_metadata: Dict[str, Any] = {}
    body_evidence: Dict[str, Any] = {}
    constraints = constraints_for_lm_candidate_kind(proposal.candidate_kind)
    if proposal.candidate_kind == "body_improvement":
        stable_key = f"{spec.stable_prefix}:{body_projection['mapping_key']}"
        constraints.update(body_improvement_constraints(body_projection))
        body_metadata = _body_metadata(body_projection)
        body_evidence = _body_evidence(body_projection)
    if stable_key in existing_keys:
        return None

    cognitive_alignment = score_lm_proposal_cognitive_alignment(
        candidate_kind=proposal.candidate_kind,
        task_type=proposal.task_type,
        evidence_level=proposal.evidence_level,
        risk_level=proposal.risk_level,
        observation_required=proposal.observation_required,
        execution_mode=proposal.execution_mode,
        blocking_factors=proposal.blocking_factors,
        reference_alignment=proposal.reference_alignment,
        evidence_packet=evidence_packet,
        posture_alignment=proposal.posture_alignment,
        priority_basis=proposal.priority_basis,
    )
    _update_lm_constraints(constraints, proposal, cognitive_alignment)
    metadata = _build_lm_metadata(
        proposal=proposal,
        batch_cognitive_assessment=batch_cognitive_assessment,
        cognitive_alignment=cognitive_alignment,
        body_metadata=body_metadata,
        drive_judgement=drive_judgement(proposal.candidate_kind),
    )
    evidence = _build_lm_evidence(
        proposal=proposal,
        batch_cognitive_assessment=batch_cognitive_assessment,
        cognitive_alignment=cognitive_alignment,
        body_evidence=body_evidence,
        active_sessions=active_sessions,
    )
    summary = proposal.summary
    return build_scored_candidate(
        stable_key=stable_key,
        title=proposal.title,
        summary=summary,
        priority="high" if proposal.confidence >= 0.75 else "normal",
        governance_task_type=spec.governance_task_type,
        task_family=spec.task_family,
        execution_kind=spec.execution_kind,
        value_tags=list(spec.value_tags),
        candidate_kind=proposal.candidate_kind,
        score_inputs={
            "core_value_strength": 0.72,
            "urgency": proposal.confidence,
            "novelty": 0.66,
            "specificity": clamp01(0.45 + min(len(summary), 240) / 400.0),
            "execution_readiness": _execution_readiness(proposal),
            "backlog_pressure_penalty": backlog_pressure(
                spec.governance_task_type,
                spec.task_family,
                spec.execution_kind,
            ),
            "repetition_penalty": clamp01(
                float(proposal.reference_alignment.get("grounding_penalty") or 0.0) * 0.55
                + (0.12 if not proposal.referenced_evidence_nodes or not proposal.referenced_agenda_nodes else 0.0)
            ),
            "adaptive_factor": adaptive_factor_for_candidate(
                candidate_kind=proposal.candidate_kind,
                adaptive_policy=adaptive_policy,
            ),
        },
        metadata=metadata,
        evidence=evidence,
        constraints=constraints,
    )


def _execution_readiness(proposal: NormalizedLmProposal) -> float:
    alignment = proposal.reference_alignment
    return clamp01(
        0.48
        + proposal.confidence * 0.4
        - clamp01(alignment.get("grounding_penalty") or 0.0) * 0.28
        - (0.08 if list(alignment.get("missing_primary_evidence_nodes") or []) else 0.0)
        - (0.08 if list(alignment.get("missing_primary_agenda_nodes") or []) else 0.0)
    )


def _update_lm_constraints(
    constraints: Dict[str, Any],
    proposal: NormalizedLmProposal,
    cognitive_alignment: Dict[str, Any],
) -> None:
    constraints.update(
        {
            "lm_execution_mode": proposal.execution_mode,
            "lm_observation_required": proposal.observation_required,
            "reference_alignment": proposal.reference_alignment,
            "cognitive_alignment": cognitive_alignment,
            "supervisor_recommended_execution_mode": proposal.supervisor_advisory[
                "recommended_execution_mode"
            ],
            "supervisor_recommended_observation_required": proposal.supervisor_advisory[
                "recommended_observation_required"
            ],
        }
    )
    optional_fields = {
        "lm_blocking_factors": proposal.blocking_factors,
        "lm_referenced_evidence_nodes": proposal.referenced_evidence_nodes,
        "lm_referenced_agenda_nodes": proposal.referenced_agenda_nodes,
        "lm_posture_alignment": proposal.posture_alignment,
        "lm_priority_basis": proposal.priority_basis,
    }
    for key, value in optional_fields.items():
        if value:
            constraints[key] = list(value)
    reasons = proposal.supervisor_advisory.get("advisory_reasons") or []
    if reasons:
        constraints["supervisor_advisory_reasons"] = list(reasons)


def _build_lm_metadata(
    *,
    proposal: NormalizedLmProposal,
    batch_cognitive_assessment: Dict[str, Any],
    cognitive_alignment: Dict[str, Any],
    body_metadata: Dict[str, Any],
    drive_judgement: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "llm_task_generated": True,
        "llm_task_confidence": proposal.confidence,
        "llm_task_rationale": proposal.rationale,
        "llm_task_type": proposal.task_type,
        "llm_task_risk_level": proposal.risk_level,
        "llm_task_evidence_level": proposal.evidence_level,
        "llm_task_observation_required": proposal.observation_required,
        "llm_task_execution_mode": proposal.execution_mode,
        "llm_task_blocking_factors": list(proposal.blocking_factors),
        "llm_referenced_evidence_nodes": list(proposal.referenced_evidence_nodes),
        "llm_referenced_agenda_nodes": list(proposal.referenced_agenda_nodes),
        "llm_posture_alignment": list(proposal.posture_alignment),
        "llm_priority_basis": list(proposal.priority_basis),
        "llm_cognitive_assessment": dict(batch_cognitive_assessment),
        "reference_alignment": proposal.reference_alignment,
        "cognitive_alignment": cognitive_alignment,
        "supervisor_advisory": proposal.supervisor_advisory,
        **body_metadata,
        "drive_judgement": drive_judgement,
    }


def _build_lm_evidence(
    *,
    proposal: NormalizedLmProposal,
    batch_cognitive_assessment: Dict[str, Any],
    cognitive_alignment: Dict[str, Any],
    body_evidence: Dict[str, Any],
    active_sessions: int,
) -> Dict[str, Any]:
    return {
        "llm_generated": True,
        "evidence_summary": proposal.evidence_summary,
        "llm_rationale": proposal.rationale,
        "llm_task_type": proposal.task_type,
        "llm_risk_level": proposal.risk_level,
        "llm_evidence_level": proposal.evidence_level,
        "llm_observation_required": proposal.observation_required,
        "llm_execution_mode": proposal.execution_mode,
        "llm_blocking_factors": list(proposal.blocking_factors),
        "llm_referenced_evidence_nodes": list(proposal.referenced_evidence_nodes),
        "llm_referenced_agenda_nodes": list(proposal.referenced_agenda_nodes),
        "llm_posture_alignment": list(proposal.posture_alignment),
        "llm_priority_basis": list(proposal.priority_basis),
        "llm_cognitive_assessment": dict(batch_cognitive_assessment),
        "reference_alignment": proposal.reference_alignment,
        "cognitive_alignment": cognitive_alignment,
        "supervisor_advisory": proposal.supervisor_advisory,
        "active_sessions": active_sessions,
        **body_evidence,
    }


def _body_metadata(projection: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "improvement_direction_source": projection.get("mapping_source"),
        "target_paths": list(projection.get("target_paths") or []),
        "structure_domains": list(projection.get("structure_domains") or []),
        "learning_task_ids": [
            ref.get("mem_id") for ref in list(projection.get("learning_refs") or [])
        ],
        "learning_quality_score": projection.get("learning_quality_score"),
    }


def _body_evidence(projection: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "learning_quality_score": projection.get("learning_quality_score"),
        "learning_refs": list(projection.get("learning_refs") or []),
        "structure_mapping": {
            "source": projection.get("mapping_source"),
            "domains": list(projection.get("structure_domains") or []),
            "target_paths": list(projection.get("target_paths") or []),
        },
    }
