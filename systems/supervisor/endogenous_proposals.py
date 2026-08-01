"""LM proposal transport and pure proposal normalization for endogenous drive."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from systems.supervisor.endogenous_drive_prompts import (
    build_endogenous_core_mission_prompt,
    build_endogenous_task_generation_payload,
)


LM_TASK_TYPES = {"observation", "review", "learning", "maintenance", "improvement"}
LM_RISK_LEVELS = {"low", "medium", "high"}
LM_EVIDENCE_LEVELS = {"weak", "moderate", "strong"}
LM_EXECUTION_MODES = {"observe_only", "review_then_handoff", "guarded_execution"}


class JsonCompletionClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_payload: Dict[str, Any],
        task: str,
    ) -> Any: ...


ClientResolver = Callable[..., tuple[Optional[JsonCompletionClient], str]]


@dataclass(frozen=True, slots=True)
class LmProposalGenerationResult:
    status: str
    proposals: List[Dict[str, Any]]
    cognitive_assessment: Dict[str, Any]
    error: Optional[str] = None


@dataclass(frozen=True, slots=True)
class NormalizedLmProposal:
    candidate_kind: str
    title: str
    summary: str
    confidence: float
    evidence_summary: List[str]
    rationale: str
    task_type: str
    risk_level: str
    evidence_level: str
    observation_required: bool
    execution_mode: str
    blocking_factors: List[str]
    referenced_evidence_nodes: List[str]
    referenced_agenda_nodes: List[str]
    posture_alignment: List[str]
    priority_basis: List[str]
    reference_alignment: Dict[str, Any]
    supervisor_advisory: Dict[str, Any]


def generate_lm_task_proposals(
    *,
    evidence_packet: Dict[str, Any],
    cognition_charter: Dict[str, Any],
    role: str,
    max_candidates: int,
    client_resolver: Optional[ClientResolver] = None,
) -> LmProposalGenerationResult:
    core_mission = str(cognition_charter.get("core_mission") or "").strip()
    if not core_mission or max_candidates <= 0:
        return LmProposalGenerationResult(
            status="disabled",
            proposals=[],
            cognitive_assessment={},
            error=(
                "missing_core_mission"
                if not core_mission
                else "max_candidates_disabled"
            ),
        )

    resolver = client_resolver or _resolve_default_client
    try:
        llm_client, _ = resolver(role=role)
    except Exception as exc:
        return LmProposalGenerationResult(
            status="llm_unavailable",
            proposals=[],
            cognitive_assessment={},
            error=str(exc),
        )
    if llm_client is None:
        return LmProposalGenerationResult(
            status="llm_unavailable",
            proposals=[],
            cognitive_assessment={},
            error="llm_client_unavailable",
        )

    system_prompt = build_endogenous_core_mission_prompt(
        cognition_charter=cognition_charter,
        cognitive_posture=evidence_packet.get("cognitive_posture"),
    )
    payload = build_endogenous_task_generation_payload(
        evidence_packet=evidence_packet,
        cognition_charter=cognition_charter,
        max_candidates=max_candidates,
    )
    try:
        result = llm_client.complete_json(
            system_prompt=system_prompt,
            user_payload={"task_generation": payload},
            task="scholar.revision",
        )
    except Exception as exc:
        return LmProposalGenerationResult(
            status="generation_error",
            proposals=[],
            cognitive_assessment={},
            error=str(exc),
        )
    if not isinstance(result, dict):
        return LmProposalGenerationResult(
            status="invalid_response",
            proposals=[],
            cognitive_assessment={},
            error="non_dict_response",
        )

    cognitive_assessment = normalize_lm_cognitive_assessment(
        result.get("cognitive_assessment")
    )
    proposals = result.get("proposals")
    if not isinstance(proposals, list):
        return LmProposalGenerationResult(
            status="invalid_response",
            proposals=[],
            cognitive_assessment=cognitive_assessment,
            error="missing_proposals_list",
        )
    normalized_proposals = [
        dict(item) for item in proposals if isinstance(item, dict)
    ]
    return LmProposalGenerationResult(
        status="completed",
        proposals=normalized_proposals,
        cognitive_assessment=cognitive_assessment,
    )


def normalize_lm_cognitive_assessment(assessment: Any) -> Dict[str, Any]:
    if not isinstance(assessment, dict):
        return {}
    normalized = {
        "current_judgement": str(
            assessment.get("current_judgement") or ""
        ).strip(),
        "dominant_constraint": str(
            assessment.get("dominant_constraint") or ""
        ).strip(),
        "primary_grounding_gaps": normalize_lm_string_list(
            assessment.get("primary_grounding_gaps"),
            limit=6,
        ),
        "why_this_task_type_now": normalize_lm_string_list(
            assessment.get("why_this_task_type_now"),
            limit=6,
        ),
        "why_not_improvement_now": normalize_lm_string_list(
            assessment.get("why_not_improvement_now"),
            limit=6,
        ),
        "self_iteration_target": str(
            assessment.get("self_iteration_target") or ""
        ).strip(),
        "self_iteration_hypothesis": str(
            assessment.get("self_iteration_hypothesis") or ""
        ).strip(),
        "stay_or_switch": str(assessment.get("stay_or_switch") or "")
        .strip()
        .lower(),
        "switch_reason": str(assessment.get("switch_reason") or "").strip(),
    }
    if normalized["stay_or_switch"] not in {"stay", "switch"}:
        normalized["stay_or_switch"] = ""
    return {
        key: value
        for key, value in normalized.items()
        if value not in ("", []) and value is not None
    }


def normalize_lm_proposal(
    item: Dict[str, Any],
    *,
    evidence_graph: Dict[str, Any],
    agenda_graph: Dict[str, Any],
) -> Optional[NormalizedLmProposal]:
    candidate_kind = str(item.get("candidate_kind") or "").strip()
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if not candidate_kind or not title or not summary:
        return None
    confidence = clamp01(item.get("confidence") or 0.5)
    evidence_summary = normalize_lm_string_list(
        item.get("evidence_summary"),
        limit=6,
    )
    rationale = str(item.get("rationale") or "").strip()
    task_type = normalize_lm_task_type(item.get("task_type"), candidate_kind)
    risk_level = normalize_lm_risk_level(item.get("risk_level"), candidate_kind)
    evidence_level = normalize_lm_evidence_level(
        item.get("evidence_level"),
        confidence=confidence,
    )
    observation_required = normalize_lm_observation_required(
        item.get("observation_required"),
        candidate_kind=candidate_kind,
    )
    execution_mode = normalize_lm_execution_mode(
        item.get("execution_mode"),
        candidate_kind=candidate_kind,
    )
    blocking_factors = normalize_lm_string_list(
        item.get("blocking_factors"),
        limit=6,
    )
    referenced_evidence_nodes = normalize_lm_string_list(
        item.get("referenced_evidence_nodes"),
        limit=8,
    )
    referenced_agenda_nodes = normalize_lm_string_list(
        item.get("referenced_agenda_nodes"),
        limit=8,
    )
    posture_alignment = normalize_lm_string_list(
        item.get("posture_alignment"),
        limit=6,
    )
    priority_basis = normalize_lm_string_list(
        item.get("priority_basis"),
        limit=6,
    )
    reference_alignment = align_lm_references(
        referenced_evidence_nodes=referenced_evidence_nodes,
        referenced_agenda_nodes=referenced_agenda_nodes,
        evidence_graph=evidence_graph,
        agenda_graph=agenda_graph,
    )
    advisory = supervisor_advisory_for_lm_proposal(
        candidate_kind=candidate_kind,
        evidence_level=evidence_level,
        risk_level=risk_level,
        observation_required=observation_required,
        execution_mode=execution_mode,
        blocking_factors=blocking_factors,
        reference_alignment=reference_alignment,
    )
    return NormalizedLmProposal(
        candidate_kind=candidate_kind,
        title=title,
        summary=summary,
        confidence=confidence,
        evidence_summary=evidence_summary,
        rationale=rationale,
        task_type=task_type,
        risk_level=risk_level,
        evidence_level=evidence_level,
        observation_required=observation_required,
        execution_mode=execution_mode,
        blocking_factors=blocking_factors,
        referenced_evidence_nodes=referenced_evidence_nodes,
        referenced_agenda_nodes=referenced_agenda_nodes,
        posture_alignment=posture_alignment,
        priority_basis=priority_basis,
        reference_alignment=reference_alignment,
        supervisor_advisory=advisory,
    )


def constraints_for_lm_candidate_kind(candidate_kind: str) -> Dict[str, Any]:
    if candidate_kind in {
        "exploratory_learning",
        "truthfulness_review",
        "shell_baseline_learning",
    }:
        constraints: Dict[str, Any] = {
            "execution_policy": "learn_only",
            "must_not_modify_active_body": True,
        }
        if candidate_kind == "shell_baseline_learning":
            constraints["execution_policy"] = "learn_shell_baseline"
        return constraints
    if candidate_kind == "governance_hygiene_review":
        return {"must_not_execute_without_review": True}
    return {}


def normalize_lm_task_type(value: Any, candidate_kind: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LM_TASK_TYPES:
        return normalized
    return task_type_for_candidate_kind(candidate_kind)


def task_type_for_candidate_kind(candidate_kind: Any) -> str:
    normalized_kind = str(candidate_kind or "").strip()
    defaults = {
        "memory_maintenance": "maintenance",
        "truthfulness_review": "review",
        "exploratory_learning": "learning",
        "shell_baseline_learning": "learning",
        "governance_hygiene_review": "review",
        "body_improvement": "improvement",
    }
    return defaults.get(normalized_kind, "observation")


def normalize_lm_risk_level(value: Any, candidate_kind: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LM_RISK_LEVELS:
        return normalized
    defaults = {
        "memory_maintenance": "medium",
        "truthfulness_review": "low",
        "exploratory_learning": "low",
        "shell_baseline_learning": "low",
        "governance_hygiene_review": "medium",
        "body_improvement": "high",
    }
    return defaults.get(candidate_kind, "medium")


def normalize_lm_evidence_level(value: Any, *, confidence: float) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LM_EVIDENCE_LEVELS:
        return normalized
    if confidence >= 0.8:
        return "strong"
    if confidence >= 0.45:
        return "moderate"
    return "weak"


def normalize_lm_observation_required(
    value: Any,
    *,
    candidate_kind: str,
) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    return candidate_kind in {"truthfulness_review", "governance_hygiene_review"}


def normalize_lm_execution_mode(
    value: Any,
    *,
    candidate_kind: str,
) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LM_EXECUTION_MODES:
        return normalized
    defaults = {
        "memory_maintenance": "guarded_execution",
        "truthfulness_review": "observe_only",
        "exploratory_learning": "review_then_handoff",
        "shell_baseline_learning": "review_then_handoff",
        "governance_hygiene_review": "review_then_handoff",
        "body_improvement": "guarded_execution",
    }
    return defaults.get(candidate_kind, "review_then_handoff")


def normalize_lm_string_list(value: Any, *, limit: int = 6) -> List[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]
    normalized: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def align_lm_references(
    *,
    referenced_evidence_nodes: List[str],
    referenced_agenda_nodes: List[str],
    evidence_graph: Dict[str, Any],
    agenda_graph: Dict[str, Any],
) -> Dict[str, Any]:
    evidence_nodes = {
        str(node.get("topic") or "").strip(): dict(node)
        for node in list(evidence_graph.get("nodes") or [])
        if isinstance(node, dict) and str(node.get("topic") or "").strip()
    }
    valid_evidence_nodes = set(evidence_nodes.keys())
    valid_agenda_nodes: set[str] = set()
    agenda_priorities: Dict[str, float] = {}
    focus = str(agenda_graph.get("focus") or "").strip()
    if focus:
        valid_agenda_nodes.add(f"focus:{focus}")
        agenda_priorities[f"focus:{focus}"] = float(
            agenda_graph.get("focus_confidence") or 0.0
        )
    for item in list(agenda_graph.get("unresolved_gaps") or []):
        if isinstance(item, dict):
            gap = str(item.get("gap") or "").strip()
            if gap:
                valid_agenda_nodes.add(gap)
                agenda_priorities[gap] = float(item.get("priority") or 0.0)
    for item in list(agenda_graph.get("recommended_directions") or []):
        if isinstance(item, dict):
            direction = str(item.get("direction") or "").strip()
            if direction:
                valid_agenda_nodes.add(direction)
                agenda_priorities[direction] = float(item.get("priority") or 0.0)
    for item in list(agenda_graph.get("active_signals") or []):
        if isinstance(item, dict):
            signal = str(item.get("signal") or "").strip()
            if signal:
                valid_agenda_nodes.add(signal)
                agenda_priorities[signal] = float(item.get("priority") or 0.0)

    matched_evidence = [
        node for node in referenced_evidence_nodes if node in valid_evidence_nodes
    ]
    missing_evidence = [
        node for node in referenced_evidence_nodes if node not in valid_evidence_nodes
    ]
    matched_agenda = [
        node for node in referenced_agenda_nodes if node in valid_agenda_nodes
    ]
    missing_agenda = [
        node for node in referenced_agenda_nodes if node not in valid_agenda_nodes
    ]
    weak_evidence = [
        node
        for node in matched_evidence
        if float(evidence_nodes.get(node, {}).get("avg_confidence") or 0.0) < 0.45
    ]
    weak_agenda = [
        node
        for node in matched_agenda
        if float(agenda_priorities.get(node) or 0.0) < 0.45
    ]

    total_requested = len(referenced_evidence_nodes) + len(referenced_agenda_nodes)
    total_matched = len(matched_evidence) + len(matched_agenda)
    weak_penalty = (len(weak_evidence) + len(weak_agenda)) * 0.12
    alignment_score = (
        round(clamp01(total_matched / total_requested - weak_penalty), 4)
        if total_requested > 0
        else 1.0
    )
    alignment_quality = "strong"
    if total_requested > 0 and (missing_evidence or missing_agenda):
        alignment_quality = "partial"
    if weak_evidence or weak_agenda:
        alignment_quality = "weak"
    if total_requested > 0 and total_matched == 0:
        alignment_quality = "drifted"

    primary_evidence_nodes = [
        str(item.get("topic") or "").strip()
        for item in sorted(
            [
                dict(node)
                for node in list(evidence_graph.get("nodes") or [])
                if isinstance(node, dict) and str(node.get("topic") or "").strip()
            ],
            key=lambda row: (
                -float(row.get("priority") or row.get("avg_confidence") or 0.0),
                str(row.get("topic") or "").strip(),
            ),
        )[:3]
        if str(item.get("topic") or "").strip()
    ]
    primary_agenda_nodes: List[str] = []
    if focus:
        primary_agenda_nodes.append(f"focus:{focus}")
    primary_agenda_nodes.extend(
        str(item.get("gap") or "").strip()
        for item in sorted(
            [
                dict(row)
                for row in list(agenda_graph.get("unresolved_gaps") or [])
                if isinstance(row, dict) and str(row.get("gap") or "").strip()
            ],
            key=lambda row: (
                -float(row.get("priority") or 0.0),
                str(row.get("gap") or "").strip(),
            ),
        )[:2]
        if str(item.get("gap") or "").strip()
    )
    if not primary_agenda_nodes:
        primary_agenda_nodes.extend(
            str(item.get("direction") or "").strip()
            for item in sorted(
                [
                    dict(row)
                    for row in list(agenda_graph.get("recommended_directions") or [])
                    if isinstance(row, dict)
                    and str(row.get("direction") or "").strip()
                ],
                key=lambda row: (
                    -float(row.get("priority") or 0.0),
                    str(row.get("direction") or "").strip(),
                ),
            )[:2]
            if str(item.get("direction") or "").strip()
        )
    matched_primary_evidence_nodes = [
        node for node in matched_evidence if node in primary_evidence_nodes
    ]
    matched_primary_agenda_nodes = [
        node for node in matched_agenda if node in primary_agenda_nodes
    ]
    missing_primary_evidence_nodes = [
        node for node in primary_evidence_nodes if node not in matched_evidence
    ]
    missing_primary_agenda_nodes = [
        node for node in primary_agenda_nodes if node not in matched_agenda
    ]
    grounding_penalty = 0.0
    if primary_evidence_nodes and not matched_primary_evidence_nodes:
        grounding_penalty += 0.16
    if primary_agenda_nodes and not matched_primary_agenda_nodes:
        grounding_penalty += 0.16
    if not referenced_evidence_nodes:
        grounding_penalty += 0.08
    if not referenced_agenda_nodes:
        grounding_penalty += 0.08
    if grounding_penalty > 0.0:
        alignment_score = round(clamp01(alignment_score - grounding_penalty), 4)
        if alignment_quality == "strong":
            alignment_quality = "partial"
        if alignment_score < 0.45 or (
            primary_evidence_nodes
            and not matched_primary_evidence_nodes
            and primary_agenda_nodes
            and not matched_primary_agenda_nodes
        ):
            alignment_quality = "weak"
    return {
        "matched_evidence_nodes": matched_evidence,
        "weak_evidence_nodes": weak_evidence,
        "missing_evidence_nodes": missing_evidence,
        "matched_agenda_nodes": matched_agenda,
        "weak_agenda_nodes": weak_agenda,
        "missing_agenda_nodes": missing_agenda,
        "primary_evidence_nodes": primary_evidence_nodes,
        "primary_agenda_nodes": primary_agenda_nodes,
        "matched_primary_evidence_nodes": matched_primary_evidence_nodes,
        "matched_primary_agenda_nodes": matched_primary_agenda_nodes,
        "missing_primary_evidence_nodes": missing_primary_evidence_nodes,
        "missing_primary_agenda_nodes": missing_primary_agenda_nodes,
        "grounding_penalty": round(clamp01(grounding_penalty), 4),
        "alignment_score": alignment_score,
        "alignment_quality": alignment_quality,
    }


def supervisor_advisory_for_lm_proposal(
    *,
    candidate_kind: str,
    evidence_level: str,
    risk_level: str,
    observation_required: bool,
    execution_mode: str,
    blocking_factors: List[str],
    reference_alignment: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    advisory_reasons: List[str] = []
    recommended_observation_required = observation_required
    recommended_execution_mode = execution_mode
    if evidence_level == "weak":
        advisory_reasons.append("weak_evidence_requires_additional_validation")
        if recommended_execution_mode == "guarded_execution":
            recommended_execution_mode = "review_then_handoff"
    if risk_level == "high":
        advisory_reasons.append("high_risk_requires_governance_review")
        recommended_observation_required = True
        if recommended_execution_mode == "guarded_execution":
            recommended_execution_mode = "review_then_handoff"
    if candidate_kind in {"truthfulness_review", "governance_hygiene_review"}:
        advisory_reasons.append("review_family_prefers_observation_or_review_first")
        recommended_observation_required = True
    if blocking_factors:
        advisory_reasons.append("blocking_factors_present")
    alignment = dict(reference_alignment or {})
    alignment_quality = str(
        alignment.get("alignment_quality") or ""
    ).strip().lower()
    missing_primary_evidence_nodes = list(
        alignment.get("missing_primary_evidence_nodes") or []
    )
    missing_primary_agenda_nodes = list(
        alignment.get("missing_primary_agenda_nodes") or []
    )
    if alignment_quality in {"weak", "drifted"}:
        advisory_reasons.append("reference_binding_is_not_grounded_enough")
        recommended_observation_required = True
        if recommended_execution_mode == "guarded_execution":
            recommended_execution_mode = "review_then_handoff"
    if missing_primary_evidence_nodes or missing_primary_agenda_nodes:
        advisory_reasons.append("primary_evidence_or_agenda_binding_is_missing")
        recommended_observation_required = True
        if recommended_execution_mode == "guarded_execution":
            recommended_execution_mode = "review_then_handoff"
    if (
        recommended_observation_required
        and recommended_execution_mode == "guarded_execution"
    ):
        recommended_execution_mode = "review_then_handoff"
    return {
        "recommended_execution_mode": recommended_execution_mode,
        "recommended_observation_required": recommended_observation_required,
        "advisory_reasons": advisory_reasons,
    }


def clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _resolve_default_client(
    *,
    role: str,
) -> tuple[Optional[JsonCompletionClient], str]:
    from memai.model_config import resolve_mem_llm_client

    return resolve_mem_llm_client(role=role)
