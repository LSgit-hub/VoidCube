"""Pure assembly helpers for endogenous LM evidence packets."""

from __future__ import annotations

from typing import Any, Dict, List

from systems.supervisor.endogenous_context import (
    build_lm_context_layers,
    reference_alignment_gap_labels,
)


def build_grounding_focus(
    *,
    evidence_graph: Dict[str, Any],
    agenda_graph: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
) -> Dict[str, Any]:
    evidence_nodes = [
        dict(row)
        for row in list(evidence_graph.get("nodes") or [])
        if isinstance(row, dict) and str(row.get("topic") or "").strip()
    ]
    agenda_gaps = [
        dict(row)
        for row in list(agenda_graph.get("unresolved_gaps") or [])
        if isinstance(row, dict) and str(row.get("gap") or "").strip()
    ]
    primary_agenda_nodes = (
        ([f"focus:{str(agenda_graph.get('focus') or '').strip()}"]
         if str(agenda_graph.get("focus") or "").strip()
         else [])
        + [
            str(item.get("gap") or "").strip()
            for item in sorted(
                agenda_gaps,
                key=lambda row: (
                    -float(row.get("priority") or 0.0),
                    str(row.get("gap") or "").strip(),
                ),
            )[:2]
            if str(item.get("gap") or "").strip()
        ]
    )[:3]
    return {
        "primary_evidence_nodes": [
            str(item.get("topic") or "").strip()
            for item in sorted(
                evidence_nodes,
                key=lambda row: (
                    -float(row.get("priority") or row.get("avg_confidence") or 0.0),
                    str(row.get("topic") or "").strip(),
                ),
            )[:3]
            if str(item.get("topic") or "").strip()
        ],
        "primary_agenda_nodes": primary_agenda_nodes,
        "recommended_directions": [
            str(item.get("direction") or "").strip()
            for item in list(agenda_graph.get("recommended_directions") or [])[:3]
            if isinstance(item, dict) and str(item.get("direction") or "").strip()
        ],
        "contradictory_topics": [
            (
                f"{str(item.get('from') or '').strip()}->"
                f"{str(item.get('to') or '').strip()}:"
                f"{str(item.get('relation') or 'contradicts').strip()}"
            )
            for item in list(evidence_graph.get("contradiction_edges") or [])[:3]
            if isinstance(item, dict)
            and (
                str(item.get("from") or "").strip()
                or str(item.get("to") or "").strip()
            )
        ],
        "grounding_gaps": reference_alignment_gap_labels(
            recent_reference_alignment
        )[:6],
        "weak_or_missing_channels": [
            str(item).strip()
            for item in list(
                evidence_credibility_summary.get("weak_or_missing_channels") or []
            )[:4]
            if str(item).strip()
        ],
    }


def assemble_lm_evidence_packet(
    *,
    cognition_charter: Dict[str, Any],
    memory_plan: Dict[str, Any],
    self_learning_plan: Dict[str, Any],
    autonomous_improvement_plan: Dict[str, Any],
    deliberation_dict: Dict[str, Any],
    perception: Dict[str, Any],
    world_model: Dict[str, Any],
    reflection: Dict[str, Any],
    adaptive_policy: Dict[str, Any],
    cognitive_posture: Dict[str, Any],
    grounding_focus: Dict[str, Any],
    self_iteration_hypotheses: Dict[str, Any],
    meta_cognition_profile: Dict[str, Any],
    api_b_judgement_snapshot: Dict[str, Any],
    self_model_snapshot: Dict[str, Any],
    evidence_credibility_summary: Dict[str, Any],
    task_type_priors: Dict[str, Any],
    evidence_channels: Dict[str, Any],
    evidence_graph: Dict[str, Any],
    agenda_graph: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
    cognitive_assessment_memory: Dict[str, Any],
    self_iteration_trend_memory: Dict[str, Any],
    switch_self_regulation_memory: Dict[str, Any],
    post_task_effect_memory: Dict[str, Any],
    recent_learning_titles: List[str],
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    learning_backlog_titles: List[str],
    body_improvement_backlog_titles: List[str],
    api_b_judgement_tasks: List[Dict[str, Any]],
    checks: Dict[str, Any],
    idle_seconds: Dict[str, Any],
    shell_slot: Dict[str, Any],
    shell_body_profile: Dict[str, Any],
) -> Dict[str, Any]:
    context_layers = build_lm_context_layers(
        cognition_charter=cognition_charter,
        cognitive_posture=cognitive_posture,
        grounding_focus=grounding_focus,
        self_iteration_hypotheses=self_iteration_hypotheses,
        meta_cognition_profile=meta_cognition_profile,
        self_model_snapshot=self_model_snapshot,
        evidence_credibility_summary=evidence_credibility_summary,
        task_type_priors=task_type_priors,
        cognitive_assessment_memory=cognitive_assessment_memory,
        self_iteration_trend_memory=self_iteration_trend_memory,
        switch_self_regulation_memory=switch_self_regulation_memory,
        post_task_effect_memory=post_task_effect_memory,
        recent_reference_alignment=recent_reference_alignment,
        api_b_judgement_snapshot=api_b_judgement_snapshot,
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        evidence_channels=evidence_channels,
        recent_learning_titles=recent_learning_titles[:8],
    )
    return {
        "identity": {
            "role": "endogenous_supervisory_core",
            "goal": "evidence-driven self-iteration under governance constraints",
        },
        "plans": {
            "memory_maintenance": dict(memory_plan),
            "self_learning": dict(self_learning_plan),
            "self_evolution": dict(autonomous_improvement_plan),
        },
        "perception": perception,
        "world_model": world_model,
        "reflection": reflection,
        "adaptive_policy": adaptive_policy,
        "decision_core": dict(context_layers.get("decision_core") or {}),
        "supporting_detail": dict(context_layers.get("supporting_detail") or {}),
        "long_tail_context": dict(context_layers.get("long_tail_context") or {}),
        "cognitive_posture": cognitive_posture,
        "grounding_focus": grounding_focus,
        "self_iteration_hypotheses": self_iteration_hypotheses,
        "meta_cognition_profile": meta_cognition_profile,
        "api_b_judgement_snapshot": api_b_judgement_snapshot,
        "self_model_snapshot": self_model_snapshot,
        "evidence_credibility_summary": evidence_credibility_summary,
        "task_type_priors": task_type_priors,
        "needs": deliberation_dict.get("needs", []),
        "intents": deliberation_dict.get("intents", []),
        "signals": deliberation_dict.get("signals", []),
        "evidence_channels": evidence_channels,
        "research_digest": evidence_channels.get("research_digest", {}),
        "evidence_graph": evidence_graph,
        "agenda_graph": agenda_graph,
        "recent_reference_alignment": recent_reference_alignment,
        "proposal_drift_memory": proposal_drift_memory,
        "cognitive_assessment_memory": cognitive_assessment_memory,
        "self_iteration_trend_memory": self_iteration_trend_memory,
        "switch_self_regulation_memory": switch_self_regulation_memory,
        "post_task_effect_memory": post_task_effect_memory,
        "recent_learning_titles": recent_learning_titles[:8],
        "recent_learning_evidence": recent_learning_evidence,
        "external_research_evidence": external_research_evidence,
        "learning_backlog_titles": learning_backlog_titles[:8],
        "body_improvement_backlog_titles": body_improvement_backlog_titles[:8],
        "api_b_judgement_tasks": api_b_judgement_tasks[:12],
        "checks": checks,
        "idle_seconds": idle_seconds,
        "shell_slot": shell_slot,
        "shell_body_profile": shell_body_profile,
    }
