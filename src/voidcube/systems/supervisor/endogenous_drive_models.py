"""Data contracts for endogenous drive deliberation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .endogenous_needs import DriveNeed


@dataclass(frozen=True, slots=True)
class DrivePerceptionSnapshot:
    user_mode: str
    autonomous_chain_gate_active: bool
    system_posture: str
    active_sessions: int
    recent_errors: int
    uncertainty_count: int
    correction_signals: int
    learning_quality: float
    has_learning_history: bool
    shell_slot_present: bool
    shell_slot_id: str
    api_b_judgement_count: int
    learning_backlog_count: int
    body_improvement_backlog_count: int
    stale_backlog_count: int
    pending_review_count: int
    api_a_ready_count: int = 0
    api_a_handoff_count: int = 0
    api_a_running_count: int = 0
    checks: Dict[str, Any] = field(default_factory=dict)
    idle_seconds: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.api_a_handoff_count <= 0 and self.api_a_ready_count > 0:
            object.__setattr__(self, "api_a_handoff_count", self.api_a_ready_count)


@dataclass(frozen=True, slots=True)
class DriveWorldModel:
    user_mode: str
    system_posture: str
    truthfulness_pressure: float
    learning_momentum: float
    body_upgrade_readiness: float
    governance_load_state: str
    memory_pressure: float
    self_confidence: float


@dataclass(frozen=True, slots=True)
class DriveIntent:
    intent_type: str
    priority: float
    rationale: str
    target_horizon: str
    output_channel: str
    source_needs: List[str] = field(default_factory=list)
    candidate_family: Optional[str] = None
    candidate_kind: Optional[str] = None


@dataclass(frozen=True, slots=True)
class DriveSignal:
    signal_type: str
    priority: float
    message: str
    rationale: str
    source_needs: List[str] = field(default_factory=list)
    related_intent: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DriveReflection:
    recent_learning_count: int
    recent_learning_quality: float
    learning_yield_state: str
    api_b_judgement_blockage_pressure: float
    api_b_judgement_blockage_state: str
    body_growth_blocked: bool
    repeated_drive_pressure: float
    autonomy_readiness: float
    dominant_constraint: str
    rationale: str
    source_evidence: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DriveAdaptivePolicy:
    learning_expansion_bias: float
    truthfulness_bias: float
    memory_continuity_bias: float
    governance_hygiene_bias: float
    body_growth_bias: float
    observation_bias: float
    candidate_throttle: float
    candidate_budget: int
    exploratory_learning_quota: int
    body_growth_quota: int
    preferred_focus: str
    rationale: str
    source_evidence: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DriveDeliberationReport:
    perception: DrivePerceptionSnapshot
    world_model: DriveWorldModel
    reflection: DriveReflection
    adaptive_policy: DriveAdaptivePolicy
    needs: List[DriveNeed] = field(default_factory=list)
    intents: List[DriveIntent] = field(default_factory=list)
    signals: List[DriveSignal] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "perception": {
                "user_mode": self.perception.user_mode,
                "autonomous_chain_gate_active": self.perception.autonomous_chain_gate_active,
                "system_posture": self.perception.system_posture,
                "active_sessions": self.perception.active_sessions,
                "recent_errors": self.perception.recent_errors,
                "uncertainty_count": self.perception.uncertainty_count,
                "correction_signals": self.perception.correction_signals,
                "learning_quality": round(self.perception.learning_quality, 4),
                "has_learning_history": self.perception.has_learning_history,
                "shell_slot_present": self.perception.shell_slot_present,
                "shell_slot_id": self.perception.shell_slot_id,
                "api_b_judgement_count": self.perception.api_b_judgement_count,
                "learning_backlog_count": self.perception.learning_backlog_count,
                "body_improvement_backlog_count": self.perception.body_improvement_backlog_count,
                "stale_backlog_count": self.perception.stale_backlog_count,
                "pending_review_count": self.perception.pending_review_count,
                "api_a_handoff_count": self.perception.api_a_handoff_count,
                "api_a_ready_count": self.perception.api_a_handoff_count,
                "api_a_running_count": self.perception.api_a_running_count,
                "checks": dict(self.perception.checks),
                "idle_seconds": dict(self.perception.idle_seconds),
            },
            "world_model": {
                "user_mode": self.world_model.user_mode,
                "system_posture": self.world_model.system_posture,
                "truthfulness_pressure": round(self.world_model.truthfulness_pressure, 4),
                "learning_momentum": round(self.world_model.learning_momentum, 4),
                "body_upgrade_readiness": round(self.world_model.body_upgrade_readiness, 4),
                "governance_load_state": self.world_model.governance_load_state,
                "memory_pressure": round(self.world_model.memory_pressure, 4),
                "self_confidence": round(self.world_model.self_confidence, 4),
            },
            "reflection": {
                "recent_learning_count": self.reflection.recent_learning_count,
                "recent_learning_quality": round(self.reflection.recent_learning_quality, 4),
                "learning_yield_state": self.reflection.learning_yield_state,
                "api_b_judgement_blockage_pressure": round(
                    self.reflection.api_b_judgement_blockage_pressure, 4
                ),
                "api_b_judgement_blockage_state": self.reflection.api_b_judgement_blockage_state,
                "body_growth_blocked": self.reflection.body_growth_blocked,
                "repeated_drive_pressure": round(self.reflection.repeated_drive_pressure, 4),
                "autonomy_readiness": round(self.reflection.autonomy_readiness, 4),
                "dominant_constraint": self.reflection.dominant_constraint,
                "rationale": self.reflection.rationale,
                "source_evidence": list(self.reflection.source_evidence),
            },
            "adaptive_policy": {
                "learning_expansion_bias": round(self.adaptive_policy.learning_expansion_bias, 4),
                "truthfulness_bias": round(self.adaptive_policy.truthfulness_bias, 4),
                "memory_continuity_bias": round(self.adaptive_policy.memory_continuity_bias, 4),
                "governance_hygiene_bias": round(self.adaptive_policy.governance_hygiene_bias, 4),
                "body_growth_bias": round(self.adaptive_policy.body_growth_bias, 4),
                "observation_bias": round(self.adaptive_policy.observation_bias, 4),
                "candidate_throttle": round(self.adaptive_policy.candidate_throttle, 4),
                "candidate_budget": self.adaptive_policy.candidate_budget,
                "exploratory_learning_quota": self.adaptive_policy.exploratory_learning_quota,
                "body_growth_quota": self.adaptive_policy.body_growth_quota,
                "preferred_focus": self.adaptive_policy.preferred_focus,
                "rationale": self.adaptive_policy.rationale,
                "source_evidence": list(self.adaptive_policy.source_evidence),
            },
            "needs": [
                {
                    "need_type": need.need_type,
                    "severity": round(need.severity, 4),
                    "urgency": round(need.urgency, 4),
                    "confidence": round(need.confidence, 4),
                    "rationale": need.rationale,
                    "source_evidence": list(need.source_evidence),
                }
                for need in self.needs
            ],
            "intents": [
                {
                    "intent_type": intent.intent_type,
                    "priority": round(intent.priority, 4),
                    "rationale": intent.rationale,
                    "target_horizon": intent.target_horizon,
                    "output_channel": intent.output_channel,
                    "source_needs": list(intent.source_needs),
                    "candidate_family": intent.candidate_family,
                    "candidate_kind": intent.candidate_kind,
                }
                for intent in self.intents
            ],
            "signals": [
                {
                    "signal_type": signal.signal_type,
                    "priority": round(signal.priority, 4),
                    "message": signal.message,
                    "rationale": signal.rationale,
                    "source_needs": list(signal.source_needs),
                    "related_intent": signal.related_intent,
                    "payload": dict(signal.payload),
                }
                for signal in self.signals
            ],
        }
