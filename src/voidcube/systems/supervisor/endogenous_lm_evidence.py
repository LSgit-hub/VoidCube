"""Pure assembly helpers for endogenous LM evidence packets."""

from __future__ import annotations

from typing import Any, Dict, List

from .endogenous_api_b_snapshot import (
    build_api_b_judgement_snapshot,
)
from .endogenous_cognitive_memory import (
    build_cognitive_assessment_memory,
    build_post_task_effect_memory,
    build_self_iteration_trend_memory,
    build_switch_self_regulation_memory,
)
from .endogenous_cognitive_posture_context import (
    build_cognitive_posture_context,
)
from .endogenous_context import (
    build_lm_context_layers,
    reference_alignment_gap_labels,
)
from .endogenous_meta_cognition import (
    build_meta_cognition_profile,
    build_proposal_drift_memory,
)
from .endogenous_self_iteration import (
    build_self_iteration_hypotheses,
)
from .endogenous_task_priors import build_task_type_priors
from .endogenous_evidence import normalize_recent_learning_evidence
from .endogenous_cognition_charter import resolve_cognition_charter
from .endogenous_drive_context import get_shell_slot_meta
from .endogenous_research import build_external_research_evidence
from .endogenous_self_model import build_recent_reference_alignment
from .endogenous_shell_profile import build_shell_body_profile


def build_lm_evidence_packet_from_runtime_config(
    *,
    runtime_config: Any,
    execution_config: Any,
    drive_input: Dict[str, Any],
    deliberation: Any,
    drive_context: Dict[str, Any],
    memory_plan: Dict[str, Any],
    self_learning_plan: Dict[str, Any],
    autonomous_improvement_plan: Dict[str, Any],
) -> Dict[str, Any]:
    cognition_charter = resolve_cognition_charter(
        charter_model=getattr(
            runtime_config, "endogenous_drive_cognition_charter", None
        ),
        core_mission=getattr(
            runtime_config, "endogenous_drive_core_mission_prompt", ""
        ),
        task_generation_principles=getattr(
            runtime_config,
            "endogenous_drive_task_generation_principles",
            [],
        ),
    )
    charter_model = getattr(
        runtime_config, "endogenous_drive_cognition_charter", None
    )
    policy_model = getattr(charter_model, "cognitive_control_policy", None)
    if hasattr(policy_model, "model_dump"):
        policy = policy_model.model_dump(mode="json")
    else:
        policy = dict(policy_model or {})
    return build_lm_evidence_packet(
        cognition_charter=cognition_charter,
        policy=policy,
        deliberation_dict=deliberation.to_dict(),
        drive_input=drive_input,
        drive_context=drive_context,
        memory_plan=memory_plan,
        self_learning_plan=self_learning_plan,
        autonomous_improvement_plan=autonomous_improvement_plan,
        shell_slot=get_shell_slot_meta(drive_input),
        external_research_enabled=bool(
            getattr(
                runtime_config,
                "endogenous_drive_external_research_enabled",
                False,
            )
        ),
        external_research_entries=list(
            getattr(
                runtime_config,
                "endogenous_drive_external_research_entries",
                [],
            )
            or []
        ),
        external_research_files=list(
            getattr(
                runtime_config,
                "endogenous_drive_external_research_files",
                [],
            )
            or []
        ),
        repo_root=getattr(execution_config, "git_repo_path", "./") or "./",
    )


def build_lm_evidence_packet(
    *,
    cognition_charter: Dict[str, Any],
    policy: Dict[str, Any],
    deliberation_dict: Dict[str, Any],
    drive_input: Dict[str, Any],
    drive_context: Dict[str, Any],
    memory_plan: Dict[str, Any],
    self_learning_plan: Dict[str, Any],
    autonomous_improvement_plan: Dict[str, Any],
    shell_slot: Dict[str, Any],
    external_research_enabled: bool,
    external_research_entries: List[Any],
    external_research_files: List[Any],
    repo_root: str,
) -> Dict[str, Any]:
    perception = deliberation_dict.get("perception", {})
    world_model = deliberation_dict.get("world_model", {})
    reflection = deliberation_dict.get("reflection", {})
    adaptive_policy = deliberation_dict.get("adaptive_policy", {})
    recent_learning_evidence = normalize_recent_learning_evidence(
        list(drive_context.get("completed_learning_tasks") or [])
    )
    external_research_evidence = build_external_research_evidence(
        enabled=external_research_enabled,
        entries=external_research_entries,
        file_entries=external_research_files,
        repo_root=repo_root,
    )
    shell_body_profile = build_shell_body_profile(shell_slot)
    recent_reference_alignment = build_recent_reference_alignment(drive_context)
    context = build_lm_evidence_context(
        policy=policy,
        deliberation_dict=deliberation_dict,
        drive_history=dict(drive_context.get("drive_history") or {}),
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        shell_body_profile=shell_body_profile,
        recent_reference_alignment=recent_reference_alignment,
    )
    return assemble_lm_evidence_packet(
        cognition_charter=cognition_charter,
        memory_plan=memory_plan,
        self_learning_plan=self_learning_plan,
        autonomous_improvement_plan=autonomous_improvement_plan,
        deliberation_dict=deliberation_dict,
        perception=perception,
        world_model=world_model,
        reflection=reflection,
        adaptive_policy=adaptive_policy,
        cognitive_posture=dict(context.get("cognitive_posture") or {}),
        grounding_focus=dict(context.get("grounding_focus") or {}),
        self_iteration_hypotheses=dict(
            context.get("self_iteration_hypotheses") or {}
        ),
        meta_cognition_profile=dict(context.get("meta_cognition_profile") or {}),
        api_b_judgement_snapshot=dict(
            context.get("api_b_judgement_snapshot") or {}
        ),
        self_model_snapshot=dict(context.get("self_model_snapshot") or {}),
        evidence_credibility_summary=dict(
            context.get("evidence_credibility_summary") or {}
        ),
        task_type_priors=dict(context.get("task_type_priors") or {}),
        evidence_channels=dict(context.get("evidence_channels") or {}),
        evidence_graph=dict(context.get("evidence_graph") or {}),
        agenda_graph=dict(context.get("agenda_graph") or {}),
        recent_reference_alignment=recent_reference_alignment,
        proposal_drift_memory=dict(context.get("proposal_drift_memory") or {}),
        cognitive_assessment_memory=dict(
            context.get("cognitive_assessment_memory") or {}
        ),
        self_iteration_trend_memory=dict(
            context.get("self_iteration_trend_memory") or {}
        ),
        switch_self_regulation_memory=dict(
            context.get("switch_self_regulation_memory") or {}
        ),
        post_task_effect_memory=dict(
            context.get("post_task_effect_memory") or {}
        ),
        recent_learning_titles=list(drive_context.get("recent_learning_titles") or []),
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        learning_backlog_titles=list(
            drive_context.get("learning_backlog_titles") or []
        ),
        body_improvement_backlog_titles=list(
            drive_context.get("body_improvement_backlog_titles") or []
        ),
        api_b_judgement_tasks=list(
            drive_context.get("api_b_judgement_tasks") or []
        ),
        checks=dict(drive_input.get("checks") or {}),
        idle_seconds=dict(drive_input.get("idle_seconds") or {}),
        shell_slot=shell_slot,
        shell_body_profile=shell_body_profile,
    )


def build_lm_evidence_context(
    *,
    policy: Dict[str, Any],
    deliberation_dict: Dict[str, Any],
    drive_history: Dict[str, Any],
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    shell_body_profile: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
) -> Dict[str, Any]:
    proposal_drift_memory = build_proposal_drift_memory(
        {"drive_history": drive_history}
    )
    posture_context = build_cognitive_posture_context(
        policy=policy,
        deliberation_dict=deliberation_dict,
        drive_history=drive_history,
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        shell_body_profile=shell_body_profile,
        recent_reference_alignment=recent_reference_alignment,
        proposal_drift_memory=proposal_drift_memory,
    )
    perception = dict(deliberation_dict.get("perception") or {})
    world_model = dict(deliberation_dict.get("world_model") or {})
    reflection = dict(deliberation_dict.get("reflection") or {})
    adaptive_policy = dict(deliberation_dict.get("adaptive_policy") or {})
    evidence_graph = dict(posture_context.get("evidence_graph") or {})
    agenda_graph = dict(posture_context.get("agenda_graph") or {})
    cognitive_assessment_memory = build_cognitive_assessment_memory(
        {"drive_history": drive_history}
    )
    self_iteration_trend_memory = build_self_iteration_trend_memory(
        {"drive_history": drive_history}
    )
    switch_self_regulation_memory = build_switch_self_regulation_memory(
        {"drive_history": drive_history}
    )
    post_task_effect_memory = build_post_task_effect_memory(
        {"drive_history": drive_history}
    )
    evidence_credibility_summary = dict(
        posture_context.get("evidence_credibility_summary") or {}
    )
    self_model_snapshot = dict(posture_context.get("self_model_snapshot") or {})
    task_type_priors = build_task_type_priors(
        reflection=reflection,
        adaptive_policy=adaptive_policy,
        self_model_snapshot=self_model_snapshot,
        evidence_credibility_summary=evidence_credibility_summary,
        agenda_graph=agenda_graph,
        recent_reference_alignment=recent_reference_alignment,
        proposal_drift_memory=proposal_drift_memory,
    )
    grounding_focus = build_grounding_focus(
        evidence_graph=evidence_graph,
        agenda_graph=agenda_graph,
        recent_reference_alignment=recent_reference_alignment,
        evidence_credibility_summary=evidence_credibility_summary,
    )
    self_iteration_hypotheses = build_self_iteration_hypotheses(
        self_model_snapshot=self_model_snapshot,
        evidence_credibility_summary=evidence_credibility_summary,
        task_type_priors=task_type_priors,
        recent_reference_alignment=recent_reference_alignment,
        proposal_drift_memory=proposal_drift_memory,
        cognitive_assessment_memory=cognitive_assessment_memory,
        self_iteration_trend_memory=self_iteration_trend_memory,
        switch_self_regulation_memory=switch_self_regulation_memory,
        post_task_effect_memory=post_task_effect_memory,
        grounding_focus=grounding_focus,
    )
    meta_cognition_profile = build_meta_cognition_profile(
        grounding_focus=grounding_focus,
        self_iteration_hypotheses=self_iteration_hypotheses,
        cognitive_assessment_memory=cognitive_assessment_memory,
        self_iteration_trend_memory=self_iteration_trend_memory,
        switch_self_regulation_memory=switch_self_regulation_memory,
        post_task_effect_memory=post_task_effect_memory,
        proposal_drift_memory=proposal_drift_memory,
        task_type_priors=task_type_priors,
    )
    return {
        **posture_context,
        "proposal_drift_memory": proposal_drift_memory,
        "cognitive_assessment_memory": cognitive_assessment_memory,
        "self_iteration_trend_memory": self_iteration_trend_memory,
        "switch_self_regulation_memory": switch_self_regulation_memory,
        "post_task_effect_memory": post_task_effect_memory,
        "task_type_priors": task_type_priors,
        "grounding_focus": grounding_focus,
        "self_iteration_hypotheses": self_iteration_hypotheses,
        "meta_cognition_profile": meta_cognition_profile,
        "api_b_judgement_snapshot": build_api_b_judgement_snapshot(
            {"drive_history": drive_history}
        ),
    }


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
