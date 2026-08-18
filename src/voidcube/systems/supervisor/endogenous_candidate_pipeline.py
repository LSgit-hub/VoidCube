"""Pure candidate scoring and deterministic selection rules for endogenous drive."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, TypeVar
import re


CORE_VALUES: Dict[str, str] = {
    "continuity": "Preserve VoidCube's long-term memory, lineage, and service continuity.",
    "truthfulness": "Surface uncertainty, correction signals, and evidence gaps before they harden.",
    "creativity": "Turn idle capacity into bounded learning and improvement proposals.",
}

SCORE_WEIGHTS: Dict[str, float] = {
    "core_value_strength": 0.38,
    "urgency": 0.24,
    "novelty": 0.14,
    "specificity": 0.10,
    "execution_readiness": 0.14,
    "backlog_pressure_penalty": 0.12,
    "repetition_penalty": 0.10,
}
TERMINAL_QUEUE_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True, slots=True)
class EndogenousTaskCandidate:
    """Task projection emitted by the cognition core for API-B review."""

    stable_key: str
    title: str
    summary: str
    priority: str
    governance_task_type: str
    task_family: str
    execution_kind: Optional[str]
    value_tags: List[str]
    utility: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)

    def rationale(self) -> str:
        metadata = dict(self.metadata or {})
        for key in ("rationale", "llm_task_rationale", "llm_rationale"):
            text = str(metadata.get(key) or "").strip()
            if text:
                return text
        judgement = dict(metadata.get("drive_judgement") or {})
        for source_key in ("intent", "adaptive_policy", "reflection"):
            source = dict(judgement.get(source_key) or {})
            text = str(source.get("rationale") or "").strip()
            if text:
                return text
        for need in list(judgement.get("needs") or []):
            if not isinstance(need, dict):
                continue
            text = str(need.get("rationale") or "").strip()
            if text:
                return text
        return self.summary

    def to_api_b_judgement_item(self) -> Dict[str, Any]:
        rationale = self.rationale()
        metadata: Dict[str, Any] = {
            "source": "endogenous_drive",
            "endogenous_drive_key": self.stable_key,
            "core_values": list(self.value_tags),
            "utility": self.utility,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
            "rationale": rationale,
        }
        metadata.update(dict(self.metadata))
        if not str(metadata.get("rationale") or "").strip():
            metadata["rationale"] = rationale
        if self.execution_kind is not None:
            metadata["execution_kind"] = self.execution_kind
        return {
            "title": self.title,
            "summary": self.summary,
            "rationale": rationale,
            "source": "endogenous_drive",
            "priority": self.priority,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
            "execution_kind": self.execution_kind,
            "metadata": metadata,
            "evidence": {
                "endogenous_drive": {
                    "stable_key": self.stable_key,
                    "core_values": list(self.value_tags),
                    "core_value_definitions": {
                        key: CORE_VALUES[key]
                        for key in self.value_tags
                        if key in CORE_VALUES
                    },
                    "utility": self.utility,
                    "score_breakdown": dict(
                        self.metadata.get("score_breakdown") or {}
                    ),
                },
                **dict(self.evidence),
            },
            "constraints": dict(self.constraints),
        }


class AdaptivePolicyLike(Protocol):
    candidate_budget: int
    exploratory_learning_quota: int
    body_growth_quota: int
    preferred_focus: str
    observation_bias: float
    memory_continuity_bias: float
    truthfulness_bias: float
    learning_expansion_bias: float
    governance_hygiene_bias: float
    body_growth_bias: float
    candidate_throttle: float


class CandidateLike(Protocol):
    title: str
    task_family: str
    governance_task_type: str
    utility: float
    metadata: Dict[str, Any]


CandidateT = TypeVar("CandidateT", bound=CandidateLike)


def clamp01(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def score_candidate(
    *,
    candidate_kind: str,
    core_value_strength: float,
    urgency: float,
    novelty: float,
    specificity: float,
    execution_readiness: float,
    backlog_pressure_penalty: float = 0.0,
    repetition_penalty: float = 0.0,
    adaptive_factor: float = 1.0,
) -> tuple[float, Dict[str, Any]]:
    dimensions = {
        "core_value_strength": round(clamp01(core_value_strength), 4),
        "urgency": round(clamp01(urgency), 4),
        "novelty": round(clamp01(novelty), 4),
        "specificity": round(clamp01(specificity), 4),
        "execution_readiness": round(clamp01(execution_readiness), 4),
    }
    penalties = {
        "backlog_pressure_penalty": round(clamp01(backlog_pressure_penalty), 4),
        "repetition_penalty": round(clamp01(repetition_penalty), 4),
    }
    raw_score = (
        dimensions["core_value_strength"] * SCORE_WEIGHTS["core_value_strength"]
        + dimensions["urgency"] * SCORE_WEIGHTS["urgency"]
        + dimensions["novelty"] * SCORE_WEIGHTS["novelty"]
        + dimensions["specificity"] * SCORE_WEIGHTS["specificity"]
        + dimensions["execution_readiness"] * SCORE_WEIGHTS["execution_readiness"]
        - penalties["backlog_pressure_penalty"] * SCORE_WEIGHTS["backlog_pressure_penalty"]
        - penalties["repetition_penalty"] * SCORE_WEIGHTS["repetition_penalty"]
    )
    normalized_adaptive_factor = round(max(0.7, min(1.25, float(adaptive_factor))), 4)
    utility = round(clamp01(raw_score * normalized_adaptive_factor), 4)
    return utility, {
        "score_model": "endogenous_drive_v2",
        "candidate_kind": candidate_kind,
        "dimensions": dimensions,
        "penalties": penalties,
        "weights": dict(SCORE_WEIGHTS),
        "adaptive_factor": normalized_adaptive_factor,
        "utility": utility,
    }


def build_scored_candidate(
    *,
    stable_key: str,
    title: str,
    summary: str,
    priority: str,
    governance_task_type: str,
    task_family: str,
    execution_kind: Optional[str],
    value_tags: List[str],
    candidate_kind: str,
    score_inputs: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    constraints: Optional[Dict[str, Any]] = None,
) -> EndogenousTaskCandidate:
    utility, score_breakdown = score_candidate(
        candidate_kind=candidate_kind,
        **score_inputs,
    )
    merged_metadata = dict(metadata or {})
    merged_metadata["score_breakdown"] = score_breakdown
    merged_evidence = dict(evidence or {})
    merged_evidence["score_breakdown"] = score_breakdown
    return EndogenousTaskCandidate(
        stable_key=stable_key,
        title=title,
        summary=summary,
        priority=priority,
        governance_task_type=governance_task_type,
        task_family=task_family,
        execution_kind=execution_kind,
        value_tags=list(value_tags),
        utility=utility,
        metadata=merged_metadata,
        evidence=merged_evidence,
        constraints=dict(constraints or {}),
    )


def adaptive_factor_for_candidate(
    *,
    candidate_kind: str,
    adaptive_policy: AdaptivePolicyLike,
) -> float:
    if candidate_kind == "memory_maintenance":
        factor = 0.9 + adaptive_policy.memory_continuity_bias * 0.35
        if adaptive_policy.preferred_focus == "memory_continuity":
            factor += 0.08
        return factor
    if candidate_kind == "truthfulness_review":
        factor = 0.9 + adaptive_policy.truthfulness_bias * 0.35
        if adaptive_policy.preferred_focus == "truthfulness":
            factor += 0.08
        return factor
    if candidate_kind in {"exploratory_learning", "shell_baseline_learning"}:
        factor = (
            0.82
            + adaptive_policy.learning_expansion_bias * 0.3
            - adaptive_policy.candidate_throttle * 0.2
        )
        if adaptive_policy.preferred_focus == "learning_expansion":
            factor += 0.06
        return factor
    if candidate_kind == "governance_hygiene_review":
        factor = 0.84 + adaptive_policy.governance_hygiene_bias * 0.32
        if adaptive_policy.preferred_focus == "governance_hygiene":
            factor += 0.08
        return factor
    if candidate_kind == "body_improvement":
        factor = (
            0.8
            + adaptive_policy.body_growth_bias * 0.3
            - adaptive_policy.candidate_throttle * 0.16
        )
        if adaptive_policy.preferred_focus == "body_growth":
            factor += 0.08
        return factor
    return 1.0


def candidate_kind_of(candidate: CandidateLike) -> str:
    metadata = dict(candidate.metadata or {})
    score_breakdown = dict(metadata.get("score_breakdown") or {})
    return str(score_breakdown.get("candidate_kind") or "").strip()


def candidate_selection_priority(candidate: CandidateLike) -> float:
    metadata = dict(candidate.metadata or {})
    drive_judgement = dict(metadata.get("drive_judgement") or {})
    intent = dict(drive_judgement.get("intent") or {})
    intent_priority = intent.get("priority")
    if isinstance(intent_priority, (int, float)):
        return clamp01(intent_priority)

    linked_needs = drive_judgement.get("needs")
    if isinstance(linked_needs, list):
        samples: List[float] = []
        for need in linked_needs:
            if not isinstance(need, dict):
                continue
            for field_name in ("severity", "urgency"):
                value = need.get(field_name)
                if isinstance(value, (int, float)):
                    samples.append(float(value))
        if samples:
            return clamp01(max(samples))

    return float(candidate.utility)


def adaptive_group_for_candidate(candidate: CandidateLike) -> str | None:
    candidate_kind = candidate_kind_of(candidate)
    if candidate_kind == "exploratory_learning":
        return "exploratory_learning"
    if candidate_kind == "body_improvement":
        return "body_growth"
    return None


def budget_priority_for_candidate(
    candidate: CandidateLike,
    *,
    adaptive_policy: AdaptivePolicyLike,
) -> tuple[int, float, float, str]:
    candidate_kind = candidate_kind_of(candidate)
    preferred_focus = str(adaptive_policy.preferred_focus or "").strip().lower()
    aligned_kinds = {
        "truthfulness": {"truthfulness_review"},
        "governance_hygiene": {"governance_hygiene_review"},
        "memory_continuity": {"memory_maintenance"},
        "observation": {"truthfulness_review", "governance_hygiene_review"},
    }
    observation_tie_break = {
        "truthfulness_review": 0,
        "governance_hygiene_review": 1,
    }
    rank = 0 if candidate_kind in aligned_kinds.get(preferred_focus, set()) else 1
    kind_tie_break = candidate_kind
    if preferred_focus == "observation":
        kind_tie_break = f"{observation_tie_break.get(candidate_kind, 9)}:{candidate_kind}"
    return (
        rank,
        -candidate_selection_priority(candidate),
        -float(candidate.utility),
        kind_tie_break,
    )


def apply_adaptive_candidate_budget(
    candidates: List[CandidateT],
    *,
    adaptive_policy: AdaptivePolicyLike,
) -> List[CandidateT]:
    if not candidates:
        return []

    ordered = sorted(
        candidates,
        key=lambda candidate: budget_priority_for_candidate(
            candidate,
            adaptive_policy=adaptive_policy,
        ),
    )
    selected: List[CandidateT] = []
    group_counts = {"exploratory_learning": 0, "body_growth": 0}
    group_limits = {
        "exploratory_learning": max(0, int(adaptive_policy.exploratory_learning_quota)),
        "body_growth": max(0, int(adaptive_policy.body_growth_quota)),
    }
    budget = max(1, int(adaptive_policy.candidate_budget))
    observation_mode = (
        adaptive_policy.preferred_focus == "observation"
        or adaptive_policy.observation_bias >= 0.72
    )

    for candidate in ordered:
        candidate_kind = candidate_kind_of(candidate)
        if observation_mode and candidate_kind not in {
            "truthfulness_review",
            "governance_hygiene_review",
            "shell_baseline_learning",
        }:
            continue
        group = adaptive_group_for_candidate(candidate)
        if group is not None and group_counts[group] >= group_limits[group]:
            continue
        selected.append(candidate)
        if group is not None:
            group_counts[group] += 1
        if len(selected) >= budget:
            break

    if not selected:
        if observation_mode:
            return []
        return ordered[:1]
    return selected


def merge_lm_led_candidate_stream(
    *,
    lm_candidates: List[CandidateT],
    heuristic_candidates: List[CandidateT],
    adaptive_policy: AdaptivePolicyLike,
) -> List[CandidateT]:
    canonical_shell_baselines = [
        candidate
        for candidate in heuristic_candidates
        if candidate_kind_of(candidate) == "shell_baseline_learning"
    ]
    if canonical_shell_baselines:
        lm_candidates = [
            candidate
            for candidate in lm_candidates
            if candidate_kind_of(candidate) != "shell_baseline_learning"
        ]
    if not lm_candidates:
        return list(heuristic_candidates or [])
    if not heuristic_candidates:
        return list(lm_candidates or [])

    merged: List[CandidateT] = [*canonical_shell_baselines, *lm_candidates]
    seen_signatures = {candidate_semantic_signature(candidate) for candidate in merged}
    lm_kinds = {
        candidate_kind_of(candidate)
        for candidate in lm_candidates
        if candidate_kind_of(candidate)
    }
    complement_budget = (
        2
        if adaptive_policy.preferred_focus
        in {"memory_continuity", "governance_hygiene", "truthfulness"}
        else 1
    )

    for candidate in sorted(
        heuristic_candidates,
        key=lambda item: item.utility,
        reverse=True,
    ):
        if complement_budget <= 0:
            break
        signature = candidate_semantic_signature(candidate)
        if signature in seen_signatures:
            continue
        candidate_kind = candidate_kind_of(candidate)
        if candidate_kind == "shell_baseline_learning":
            continue
        if candidate_kind and candidate_kind in lm_kinds:
            continue
        merged.append(candidate)
        seen_signatures.add(signature)
        complement_budget -= 1
    return merged


def active_api_b_judgement_candidate_kinds(
    tasks: List[Any],
) -> set[str]:
    kinds: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        status = str(task.get("status") or "").strip().lower()
        if status in TERMINAL_QUEUE_STATUSES:
            continue
        metadata = dict(task.get("metadata") or {})
        evidence = dict(task.get("evidence") or {})
        score_breakdown = dict(
            metadata.get("score_breakdown")
            or evidence.get("score_breakdown")
            or {}
        )
        candidate_kind = str(
            metadata.get("candidate_kind")
            or evidence.get("candidate_kind")
            or score_breakdown.get("candidate_kind")
            or ""
        ).strip()
        if candidate_kind:
            kinds.add(candidate_kind)
    return kinds


def candidate_semantic_signature(candidate: CandidateLike) -> str:
    return "|".join(
        [
            candidate_kind_of(candidate),
            str(candidate.task_family or "").strip().lower(),
            str(candidate.governance_task_type or "").strip().lower(),
            normalize_topic_signature(candidate.title),
        ]
    )


def normalize_topic_signature(text: str) -> str:
    normalized_words = [
        word.lower()
        for word in re.findall(r"[a-zA-Z0-9_]+", str(text or ""))
        if len(word) >= 3
    ]
    if not normalized_words:
        return ""
    return " ".join(normalized_words[:8])
