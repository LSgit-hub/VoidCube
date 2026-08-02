"""Pure input-context assembly for endogenous cognitive posture selection."""

from __future__ import annotations

from typing import Any, Dict, List

from systems.supervisor.endogenous_agenda import build_agenda_graph
from systems.supervisor.endogenous_cognitive_posture import (
    resolve_cognitive_posture_from_policy,
)
from systems.supervisor.endogenous_evidence import build_evidence_channels
from systems.supervisor.endogenous_meta_cognition import (
    build_recent_cognitive_alignment_summary,
)
from systems.supervisor.endogenous_self_model import (
    build_evidence_credibility_summary,
    build_self_model_snapshot,
)


def build_cognitive_posture_context(
    *,
    policy: Dict[str, Any],
    deliberation_dict: Dict[str, Any],
    drive_history: Dict[str, Any],
    recent_learning_evidence: List[Dict[str, Any]],
    external_research_evidence: List[Dict[str, Any]],
    shell_body_profile: Dict[str, Any],
    recent_reference_alignment: Dict[str, Any],
    proposal_drift_memory: Dict[str, Any],
) -> Dict[str, Any]:
    evidence_channels = build_evidence_channels(
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        shell_body_profile=shell_body_profile,
        deliberation_dict=deliberation_dict,
    )
    evidence_graph = dict(evidence_channels.get("evidence_graph") or {})
    agenda_graph = build_agenda_graph(
        deliberation_dict=deliberation_dict,
        evidence_graph=evidence_graph,
    )
    self_model_snapshot = build_self_model_snapshot(
        perception=dict(deliberation_dict.get("perception") or {}),
        world_model=dict(deliberation_dict.get("world_model") or {}),
        reflection=dict(deliberation_dict.get("reflection") or {}),
        adaptive_policy=dict(deliberation_dict.get("adaptive_policy") or {}),
        shell_body_profile=shell_body_profile,
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        recent_reference_alignment=recent_reference_alignment,
        evidence_graph=evidence_graph,
        agenda_graph=agenda_graph,
    )
    evidence_credibility_summary = build_evidence_credibility_summary(
        recent_learning_evidence=recent_learning_evidence,
        external_research_evidence=external_research_evidence,
        shell_body_profile=shell_body_profile,
        evidence_channels=evidence_channels,
        recent_reference_alignment=recent_reference_alignment,
    )
    cognitive_posture = resolve_cognitive_posture_from_policy(
        policy=policy,
        deliberation_dict=deliberation_dict,
        self_model_snapshot=self_model_snapshot,
        evidence_credibility_summary=evidence_credibility_summary,
        recent_reference_alignment=recent_reference_alignment,
        proposal_drift_memory=proposal_drift_memory,
        recent_cognitive_alignment=build_recent_cognitive_alignment_summary(
            drive_history
        ),
    )
    return {
        "cognitive_posture": cognitive_posture,
        "evidence_channels": evidence_channels,
        "evidence_graph": evidence_graph,
        "agenda_graph": agenda_graph,
        "self_model_snapshot": self_model_snapshot,
        "evidence_credibility_summary": evidence_credibility_summary,
    }
