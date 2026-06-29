from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from systems.supervisor.endogenous_drive_prompts import (
    build_endogenous_core_mission_prompt,
    build_endogenous_task_generation_payload,
)
from systems.supervisor.config_models import (
    EndogenousDriveCognitionCharterConfig,
    EndogenousDriveCognitiveControlPolicyConfig,
)


CORE_VALUES: Dict[str, str] = {
    "continuity": "Preserve VoidCube's long-term memory, lineage, and service continuity.",
    "truthfulness": "Surface uncertainty, correction signals, and evidence gaps before they harden.",
    "creativity": "Turn idle capacity into bounded learning and improvement proposals.",
}

_TOPIC_WORD_RE = re.compile(r"[a-zA-Z0-9_]{3,}")
_TOPIC_STOPWORDS = {
    "voidcube", "agent", "system", "task", "tasks", "work", "review", "recent",
    "learning", "learn", "research", "improve", "improvement", "current", "shell",
    "body", "code", "codebase", "baseline", "follow", "followup", "thread",
    "general", "quality", "issue", "issues", "notes", "evidence", "future",
}
_SCORE_WEIGHTS: Dict[str, float] = {
    "core_value_strength": 0.38,
    "urgency": 0.24,
    "novelty": 0.14,
    "specificity": 0.10,
    "execution_readiness": 0.14,
    "queue_pressure_penalty": 0.12,
    "repetition_penalty": 0.10,
}
_TERMINAL_QUEUE_STATUSES = {"completed", "failed", "cancelled"}
_REVIEW_BACKLOG_STATUSES = {"deferred", "paused", "awaiting_review", "retry"}
_LM_TASK_TYPES = {"observation", "review", "learning", "maintenance", "improvement"}
_LM_RISK_LEVELS = {"low", "medium", "high"}
_LM_EVIDENCE_LEVELS = {"weak", "moderate", "strong"}
_LM_EXECUTION_MODES = {"observe_only", "review_then_queue", "guarded_execution"}


@dataclass(frozen=True, slots=True)
class EndogenousTaskCandidate:
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

    def to_queue_item(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "source": "endogenous_drive",
            "endogenous_drive_key": self.stable_key,
            "core_values": list(self.value_tags),
            "utility": self.utility,
            "governance_task_type": self.governance_task_type,
            "task_family": self.task_family,
        }
        metadata.update(dict(self.metadata))
        if self.execution_kind is not None:
            metadata["execution_kind"] = self.execution_kind
        return {
            "title": self.title,
            "summary": self.summary,
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
                        key: CORE_VALUES[key] for key in self.value_tags if key in CORE_VALUES
                    },
                    "utility": self.utility,
                    "score_breakdown": dict(self.metadata.get("score_breakdown") or {}),
                },
                **dict(self.evidence),
            },
            "constraints": dict(self.constraints),
        }


@dataclass(frozen=True, slots=True)
class DrivePerceptionSnapshot:
    user_mode: str
    governor_mode_active: bool
    in_execution_window: bool
    system_posture: str
    active_sessions: int
    recent_errors: int
    uncertainty_count: int
    correction_signals: int
    learning_quality: float
    has_learning_history: bool
    shell_slot_present: bool
    shell_slot_id: str
    active_queue_count: int
    queued_learning_count: int
    queued_body_improvement_count: int
    stale_queue_count: int
    pending_review_count: int
    checks: Dict[str, Any] = field(default_factory=dict)
    idle_seconds: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DriveWorldModel:
    user_mode: str
    system_posture: str
    truthfulness_pressure: float
    learning_momentum: float
    body_upgrade_readiness: float
    queue_health: str
    memory_pressure: float
    self_confidence: float


@dataclass(frozen=True, slots=True)
class DriveNeed:
    need_type: str
    severity: float
    urgency: float
    confidence: float
    rationale: str
    source_evidence: List[str] = field(default_factory=list)


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
    queue_blockage_pressure: float
    queue_blockage_state: str
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
    queue_hygiene_bias: float
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
                "governor_mode_active": self.perception.governor_mode_active,
                "in_execution_window": self.perception.in_execution_window,
                "system_posture": self.perception.system_posture,
                "active_sessions": self.perception.active_sessions,
                "recent_errors": self.perception.recent_errors,
                "uncertainty_count": self.perception.uncertainty_count,
                "correction_signals": self.perception.correction_signals,
                "learning_quality": round(self.perception.learning_quality, 4),
                "has_learning_history": self.perception.has_learning_history,
                "shell_slot_present": self.perception.shell_slot_present,
                "shell_slot_id": self.perception.shell_slot_id,
                "active_queue_count": self.perception.active_queue_count,
                "queued_learning_count": self.perception.queued_learning_count,
                "queued_body_improvement_count": self.perception.queued_body_improvement_count,
                "stale_queue_count": self.perception.stale_queue_count,
                "pending_review_count": self.perception.pending_review_count,
                "checks": dict(self.perception.checks),
                "idle_seconds": dict(self.perception.idle_seconds),
            },
            "world_model": {
                "user_mode": self.world_model.user_mode,
                "system_posture": self.world_model.system_posture,
                "truthfulness_pressure": round(self.world_model.truthfulness_pressure, 4),
                "learning_momentum": round(self.world_model.learning_momentum, 4),
                "body_upgrade_readiness": round(self.world_model.body_upgrade_readiness, 4),
                "queue_health": self.world_model.queue_health,
                "memory_pressure": round(self.world_model.memory_pressure, 4),
                "self_confidence": round(self.world_model.self_confidence, 4),
            },
            "reflection": {
                "recent_learning_count": self.reflection.recent_learning_count,
                "recent_learning_quality": round(self.reflection.recent_learning_quality, 4),
                "learning_yield_state": self.reflection.learning_yield_state,
                "queue_blockage_pressure": round(self.reflection.queue_blockage_pressure, 4),
                "queue_blockage_state": self.reflection.queue_blockage_state,
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
                "queue_hygiene_bias": round(self.adaptive_policy.queue_hygiene_bias, 4),
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


class EndogenousDriveEngine:
    """Supervisor drive loop — deterministic core + optional LLM intelligence.

    The drive engine does not execute work. It turns system facts, core values,
    and (when available) LLM-analyzed memory context into auditable queue
    candidates that still pass through supervisor review.

    Without LLM: uses deterministic text extraction (first 80 chars).
    With LLM: reads compressed memory context to generate intelligent,
    context-aware learning topics.
    """

    def __init__(self, config: Any | None = None) -> None:
        self.config = config
        self._latest_lm_task_generation_context: Dict[str, Any] = {}

    def get_latest_lm_task_generation_context(self) -> Dict[str, Any]:
        return dict(self._latest_lm_task_generation_context or {})

    def resolve_cognitive_posture_state(
        self,
        *,
        idle_window: Dict[str, Any],
        deliberation_dict: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_context = self._build_drive_context(idle_window)
        runtime_config = getattr(self.config, "service_runtime", None)
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        policy_model = getattr(charter_model, "cognitive_control_policy", None)
        if hasattr(policy_model, "model_dump"):
            policy = policy_model.model_dump(mode="json")
        else:
            policy = dict(policy_model or {})

        recent_reference_alignment = self._build_recent_reference_alignment(drive_context)
        proposal_drift_memory = self._build_proposal_drift_memory(drive_context)
        recent_learning_evidence = self._build_recent_learning_evidence(drive_context)
        external_research_evidence = self._build_external_research_evidence()
        shell_slot = dict(self._get_shell_slot_meta(idle_window) or {})
        shell_body_profile = self._build_shell_body_profile(shell_slot)
        evidence_channels = self._build_evidence_channels(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            deliberation_dict=deliberation_dict,
        )
        evidence_graph = dict(evidence_channels.get("evidence_graph") or {})
        agenda_graph = self._build_agenda_graph(
            deliberation_dict=deliberation_dict,
            evidence_graph=evidence_graph,
        )
        self_model_snapshot = self._build_self_model_snapshot(
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
        evidence_credibility_summary = self._build_evidence_credibility_summary(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            evidence_channels=evidence_channels,
            recent_reference_alignment=recent_reference_alignment,
        )
        return self._resolve_cognitive_posture_from_policy(
            policy=policy,
            deliberation_dict=deliberation_dict,
            self_model_snapshot=self_model_snapshot,
            evidence_credibility_summary=evidence_credibility_summary,
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
            drive_context=drive_context,
        )

    def generate_candidates(
        self,
        *,
        idle_window: Dict[str, Any],
        existing_drive_keys: Iterable[str],
        max_candidates: int = 3,
    ) -> List[EndogenousTaskCandidate]:
        existing_keys = set(existing_drive_keys)
        candidates = self._candidate_stream(idle_window, existing_keys=existing_keys)
        candidates.sort(key=lambda candidate: candidate.utility, reverse=True)
        return candidates[:max(max_candidates, 0)]

    def build_deliberation_report(
        self,
        *,
        idle_window: Dict[str, Any],
    ) -> DriveDeliberationReport:
        activity = dict(idle_window.get("activity") or {})
        drive_context = self._build_drive_context(idle_window)
        nested_counts = dict(activity.get("counts") or {})
        counts: Dict[str, Any] = dict(nested_counts)
        for _key in (
            "error_count",
            "recent_errors",
            "uncertainty_high_count",
            "high_uncertainty",
        ):
            value = activity.get(_key)
            if value is not None and _key not in counts:
                counts[_key] = value
        decisions_by_family = dict(idle_window.get("task_family_decisions") or {})
        decisions_by_governance = dict(idle_window.get("governance_task_type_decisions") or {})
        memory_plan = self._decision_for(
            "memory_maintenance",
            decisions_by_family,
            decisions_by_governance,
        )
        self_learning_plan = self._decision_for(
            "self_learning",
            decisions_by_family,
            decisions_by_governance,
        )
        self_evolution_plan = self._decision_for(
            "general_self_evolution",
            decisions_by_family,
            decisions_by_governance,
        )
        recent_errors = int(counts.get("error_count") or counts.get("recent_errors") or 0)
        uncertainty_count = int(
            counts.get("uncertainty_high_count")
            or counts.get("high_uncertainty")
            or 0
        )
        pre_decayed = idle_window.get("correction_signals")
        if pre_decayed is not None:
            try:
                correction_signals = max(0, int(pre_decayed))
            except (TypeError, ValueError):
                correction_signals = recent_errors + uncertainty_count
        else:
            correction_signals = recent_errors + uncertainty_count
        shell_slot_meta = self._get_shell_slot_meta(idle_window) or {}
        perception = self._perceive_drive_state(
            idle_window=idle_window,
            activity=activity,
            drive_context=drive_context,
            counts=counts,
            correction_signals=correction_signals,
            shell_slot_meta=shell_slot_meta,
        )
        world_model = self._build_world_model(perception)
        reflection = self._build_reflection(
            perception=perception,
            world_model=world_model,
            drive_context=drive_context,
            shell_slot_meta=shell_slot_meta,
        )
        adaptive_policy = self._build_adaptive_policy(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            drive_context=drive_context,
        )
        needs = self._detect_needs(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            self_evolution_plan=self_evolution_plan,
        )
        intents = self._synthesize_intents(
            needs=needs,
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
        )
        signals = self._emit_drive_signals(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            needs=needs,
            intents=intents,
        )
        return DriveDeliberationReport(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            needs=needs,
            intents=intents,
            signals=signals,
        )

    def _perceive_drive_state(
        self,
        *,
        idle_window: Dict[str, Any],
        activity: Dict[str, Any],
        drive_context: Dict[str, Any],
        counts: Dict[str, Any],
        correction_signals: int,
        shell_slot_meta: Optional[Dict[str, Any]] = None,
    ) -> DrivePerceptionSnapshot:
        checks = dict(idle_window.get("checks") or {})
        idle_seconds = dict(idle_window.get("idle_seconds") or {})
        governor_mode_active = bool(idle_window.get("governor_mode_active", False))
        active_sessions = int(activity.get("active_sessions") or 0)
        queued_learning_count = len(list(drive_context.get("queued_learning_titles") or []))
        queued_body_improvement_count = len(
            list(drive_context.get("queued_body_improvement_titles") or [])
        )
        stale_queue_count = int(drive_context.get("stale_queue_count") or 0)
        pending_review_count = int(drive_context.get("pending_review_count") or 0)
        active_queue_count = int(drive_context.get("active_queue_count") or 0)
        learning_quality = self._calculate_learning_quality_score(idle_window)
        recent_errors = int(counts.get("error_count") or counts.get("recent_errors") or 0)
        uncertainty_count = int(
            counts.get("uncertainty_high_count")
            or counts.get("high_uncertainty")
            or 0
        )
        shell_slot_id = str((shell_slot_meta or {}).get("slot_id") or "").strip()
        shell_slot_present = bool(shell_slot_id or (shell_slot_meta or {}).get("worktree_path"))

        user_mode = "serving_user"
        if governor_mode_active:
            user_mode = "governor_autonomous"
        elif checks.get("has_user_idle"):
            user_mode = "idle_window"

        system_posture = "stable"
        if active_sessions > 0 and not checks.get("has_user_idle", False):
            system_posture = "serving_user"
        elif correction_signals >= 4:
            system_posture = "strained"
        elif pending_review_count > 0 or stale_queue_count > 1:
            system_posture = "degrading"
        elif learning_quality >= 60.0 and shell_slot_present:
            system_posture = "growth_window"

        return DrivePerceptionSnapshot(
            user_mode=user_mode,
            governor_mode_active=governor_mode_active,
            in_execution_window=bool(checks.get("in_execution_window", False)),
            system_posture=system_posture,
            active_sessions=active_sessions,
            recent_errors=recent_errors,
            uncertainty_count=uncertainty_count,
            correction_signals=max(0, correction_signals),
            learning_quality=learning_quality,
            has_learning_history=bool(idle_window.get("completed_learning_tasks") or []),
            shell_slot_present=shell_slot_present,
            shell_slot_id=shell_slot_id,
            active_queue_count=active_queue_count,
            queued_learning_count=queued_learning_count,
            queued_body_improvement_count=queued_body_improvement_count,
            stale_queue_count=stale_queue_count,
            pending_review_count=pending_review_count,
            checks=checks,
            idle_seconds=idle_seconds,
        )

    def _build_world_model(
        self,
        perception: DrivePerceptionSnapshot,
    ) -> DriveWorldModel:
        truthfulness_pressure = self._clamp01(
            0.15
            + min(perception.correction_signals, 6) / 6.0 * 0.75
        )
        learning_momentum = self._clamp01(
            (perception.learning_quality / 100.0) * 0.8
            + (0.1 if perception.has_learning_history else 0.0)
            - min(perception.queued_learning_count, 3) * 0.08
        )
        body_upgrade_readiness = self._clamp01(
            (perception.learning_quality / 100.0) * 0.7
            + (0.15 if perception.shell_slot_present else 0.0)
            - min(perception.queued_body_improvement_count, 2) * 0.2
        )
        queue_strain = min(
            perception.active_queue_count * 0.08
            + perception.stale_queue_count * 0.12
            + perception.pending_review_count * 0.1,
            1.0,
        )
        memory_pressure = self._clamp01(
            0.25
            + (0.15 if perception.in_execution_window else 0.0)
            + min(perception.stale_queue_count, 3) * 0.08
        )
        self_confidence = self._clamp01(
            0.55
            + (0.1 if perception.in_execution_window else 0.0)
            + (0.08 if perception.governor_mode_active else 0.0)
            - min(perception.active_sessions, 3) * 0.08
            - min(perception.pending_review_count, 3) * 0.04
        )
        queue_health = "clear"
        if queue_strain >= 0.55:
            queue_health = "strained"
        elif queue_strain >= 0.3:
            queue_health = "busy"

        return DriveWorldModel(
            user_mode=perception.user_mode,
            system_posture=perception.system_posture,
            truthfulness_pressure=truthfulness_pressure,
            learning_momentum=learning_momentum,
            body_upgrade_readiness=body_upgrade_readiness,
            queue_health=queue_health,
            memory_pressure=memory_pressure,
            self_confidence=self_confidence,
        )

    def _build_reflection(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        drive_context: Dict[str, Any],
        shell_slot_meta: Optional[Dict[str, Any]] = None,
    ) -> DriveReflection:
        completed_learning_tasks = list(drive_context.get("completed_learning_tasks") or [])
        queued_tasks = list(drive_context.get("queued_tasks") or [])
        drive_history = dict(drive_context.get("drive_history") or {})
        historical_outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        historical_outcomes = self._normalize_historical_outcomes(historical_outcomes)
        recent_learning_count = len(completed_learning_tasks[:3])

        quality_values: List[float] = []
        for task in completed_learning_tasks[:3]:
            try:
                quality_values.append(self._clamp01(float(task.get("quality_score") or 0.0)))
            except (TypeError, ValueError):
                continue
        recent_learning_quality = (
            sum(quality_values) / len(quality_values)
            if quality_values
            else self._clamp01(perception.learning_quality / 100.0)
        )

        learning_yield_state = "cold"
        if recent_learning_quality >= 0.75:
            learning_yield_state = "strong"
        elif recent_learning_quality >= 0.45 or recent_learning_count > 0:
            learning_yield_state = "mixed"

        blocked_status_count = 0
        repeated_drive_count = 0
        recent_endogenous_keys: set[str] = set()
        for task in queued_tasks:
            status = str(task.get("status") or "").strip().lower()
            if status in _REVIEW_BACKLOG_STATUSES:
                blocked_status_count += 1
            metadata = dict(task.get("metadata") or {})
            evidence = dict(task.get("evidence") or {})
            endogenous_key = str(
                metadata.get("endogenous_drive_key")
                or evidence.get("endogenous_drive_key")
                or ""
            ).strip()
            if endogenous_key:
                recent_endogenous_keys.add(endogenous_key)
                repeated_drive_count += 1

        queue_blockage_pressure = self._clamp01(
            blocked_status_count * 0.18
            + perception.stale_queue_count * 0.16
            + max(0, perception.active_queue_count - 2) * 0.05
        )
        if world_model.queue_health == "strained":
            queue_blockage_pressure = self._clamp01(queue_blockage_pressure + 0.2)
        elif world_model.queue_health == "busy":
            queue_blockage_pressure = self._clamp01(queue_blockage_pressure + 0.08)

        queue_blockage_state = "clear"
        if queue_blockage_pressure >= 0.6:
            queue_blockage_state = "blocked"
        elif queue_blockage_pressure >= 0.32:
            queue_blockage_state = "dragging"

        body_growth_blocked = False
        if shell_slot_meta:
            policy = dict(drive_context.get("policy") or {})
            body_growth_blocked = self._has_recent_body_improvement(
                drive_context,
                shell_slot_meta=dict(shell_slot_meta or {}),
                cooldown_hours=int(policy.get("body_improvement_cooldown_hours", 12) or 12),
            )

        repeated_drive_pressure = self._clamp01(
            repeated_drive_count * 0.08
            + max(0, len(recent_endogenous_keys) - 1) * 0.04
            + (0.14 if queue_blockage_state != "clear" else 0.0)
        )
        recent_historical_outcomes = historical_outcomes[:12]

        def _historical_family(item: Dict[str, Any]) -> str:
            return str(
                item.get("task_family")
                or item.get("governance_task_type")
                or ""
            ).strip().lower()

        recent_self_learning_outcomes = [
            item
            for item in historical_outcomes
            if _historical_family(item) == "self_learning"
        ][:12]
        historical_pressure = self._summarize_historical_pressure(
            recent_historical_outcomes=recent_historical_outcomes,
            recent_self_learning_outcomes=recent_self_learning_outcomes,
        )
        historical_scope = str(historical_pressure["scope"] or "global")
        historical_total = int(historical_pressure["total"] or 0)
        historical_success_ratio = float(historical_pressure["success_ratio"] or 0.5)
        historical_drag_ratio = float(historical_pressure["drag_ratio"] or 0.0)
        recent_relapse_drag_count = int(historical_pressure["recent_relapse_drag_count"] or 0)
        recent_relapse_drag_ratio = float(historical_pressure["recent_relapse_drag_ratio"] or 0.0)
        autonomy_readiness = self._clamp01(
            world_model.self_confidence * 0.34
            + world_model.learning_momentum * 0.24
            + world_model.body_upgrade_readiness * 0.12
            + recent_learning_quality * 0.18
            + historical_success_ratio * 0.1
            - queue_blockage_pressure * 0.24
            - repeated_drive_pressure * 0.12
            - historical_drag_ratio * 0.16
            - recent_relapse_drag_ratio * 0.06
            - (0.08 if body_growth_blocked else 0.0)
        )
        historical_underdelivery_active = bool(
            historical_pressure.get("underdelivery_active")
        )

        dominant_constraint = "none"
        if queue_blockage_pressure >= 0.55:
            dominant_constraint = "queue_blockage"
        elif body_growth_blocked:
            dominant_constraint = "body_growth_cooldown"
        elif historical_underdelivery_active:
            dominant_constraint = "historical_underdelivery"
        elif recent_learning_quality < 0.4 and recent_learning_count > 0:
            dominant_constraint = "weak_learning_yield"
        elif perception.active_sessions > 0 and perception.user_mode == "serving_user":
            dominant_constraint = "user_service_priority"

        rationale_parts = [
            f"recent learning yield is {learning_yield_state}",
            f"queue blockage is {queue_blockage_state}",
        ]
        if historical_total > 0:
            rationale_parts.append(
                f"historical {historical_scope} success ratio is {historical_success_ratio:.2f}"
            )
        if body_growth_blocked:
            rationale_parts.append("body growth is temporarily blocked by recent shell-improvement activity")
        if dominant_constraint != "none":
            rationale_parts.append(f"dominant constraint is {dominant_constraint}")

        return DriveReflection(
            recent_learning_count=recent_learning_count,
            recent_learning_quality=recent_learning_quality,
            learning_yield_state=learning_yield_state,
            queue_blockage_pressure=queue_blockage_pressure,
            queue_blockage_state=queue_blockage_state,
            body_growth_blocked=body_growth_blocked,
            repeated_drive_pressure=repeated_drive_pressure,
            autonomy_readiness=autonomy_readiness,
            dominant_constraint=dominant_constraint,
            rationale="; ".join(rationale_parts) + ".",
            source_evidence=[
                f"recent_learning_count={recent_learning_count}",
                f"recent_learning_quality={recent_learning_quality:.2f}",
                f"blocked_status_count={blocked_status_count}",
                f"stale_queue_count={perception.stale_queue_count}",
                f"body_growth_blocked={body_growth_blocked}",
                f"repeated_drive_count={repeated_drive_count}",
                f"historical_scope={historical_scope}",
                f"historical_outcomes={historical_total}",
                f"historical_success_ratio={historical_success_ratio:.2f}",
                f"historical_drag_ratio={historical_drag_ratio:.2f}",
                f"recent_relapse_drag_count={recent_relapse_drag_count}",
                f"recent_relapse_drag_ratio={recent_relapse_drag_ratio:.2f}",
            ],
        )

    def _build_adaptive_policy(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        drive_context: Dict[str, Any],
    ) -> DriveAdaptivePolicy:
        drive_history = dict(drive_context.get("drive_history") or {})
        policy = dict(drive_context.get("policy") or {})
        strategy_memory = self._normalize_strategy_memory(
            drive_history.get("strategy_memory")
        )
        historical_outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        historical_outcomes = self._normalize_historical_outcomes(historical_outcomes)
        recent_historical_outcomes = historical_outcomes[:12]

        def _historical_family(item: Dict[str, Any]) -> str:
            return str(
                item.get("task_family")
                or item.get("governance_task_type")
                or ""
            ).strip().lower()

        recent_self_learning_outcomes = [
            item
            for item in historical_outcomes
            if _historical_family(item) == "self_learning"
        ][:12]
        historical_pressure = self._summarize_historical_pressure(
            recent_historical_outcomes=recent_historical_outcomes,
            recent_self_learning_outcomes=recent_self_learning_outcomes,
        )

        stats: Dict[str, Dict[str, int]] = {}
        for item in historical_outcomes[:18]:
            family = str(
                item.get("task_family")
                or item.get("governance_task_type")
                or item.get("execution_kind")
                or "unknown"
            ).strip().lower()
            if not family:
                continue
            bucket = stats.setdefault(
                family,
                {"completed": 0, "failed": 0, "dragging": 0},
            )
            status = str(item.get("status") or "").strip().lower()
            if status == "completed":
                bucket["completed"] += 1
            elif status in {"failed", "cancelled"}:
                bucket["failed"] += 1
            elif status in {"approved", "deferred", "paused", "awaiting_review", "retry"}:
                bucket["dragging"] += 1

        def _family_success(families: List[str], default: float = 0.5) -> float:
            completed = failed = dragging = 0
            for family in families:
                bucket = stats.get(family, {})
                completed += int(bucket.get("completed") or 0)
                failed += int(bucket.get("failed") or 0)
                dragging += int(bucket.get("dragging") or 0)
            total = completed + failed + dragging
            if total <= 0:
                return default
            return completed / total

        historical_completed = 0
        historical_failed = 0
        historical_dragging = 0
        for bucket in stats.values():
            historical_completed += int(bucket.get("completed") or 0)
            historical_failed += int(bucket.get("failed") or 0)
            historical_dragging += int(bucket.get("dragging") or 0)
        historical_total = historical_completed + historical_failed + historical_dragging
        historical_drag_ratio = (
            (historical_failed + historical_dragging) / historical_total
            if historical_total > 0
            else 0.0
        )

        scoped_historical_scope = str(historical_pressure["scope"] or "global")
        scoped_historical_drag_ratio = float(historical_pressure["drag_ratio"] or 0.0)
        recent_relapse_drag_count = int(historical_pressure["recent_relapse_drag_count"] or 0)
        recent_relapse_drag_ratio = float(historical_pressure["recent_relapse_drag_ratio"] or 0.0)

        learning_success = _family_success(["self_learning"], default=0.55)
        queue_success = _family_success(["general_self_evolution", "self_evolution"], default=0.45)
        body_success = _family_success(["body_upgrade", "body_improvement"], default=0.4)
        memory_success = _family_success(["memory_maintenance"], default=0.65)

        focus_stats = dict(strategy_memory.get("focus_stats") or {})
        context_key = self._strategy_context_key(
            perception=perception,
            reflection=reflection,
        )
        contextual_focus_stats = dict(
            dict(strategy_memory.get("contextual_focus_stats") or {}).get(context_key) or {}
        )
        agenda_topic_stats = dict(strategy_memory.get("agenda_topic_stats") or {})
        observation_target_stats = dict(strategy_memory.get("observation_target_stats") or {})

        def _effectiveness_from_bucket(bucket: Dict[str, Any], default: float) -> float:
            completed = int(bucket.get("completed") or 0)
            failed = int(bucket.get("failed") or 0)
            dragging = int(bucket.get("dragging") or 0)
            judged = int(bucket.get("judged") or 0)
            resolved = completed + failed + dragging
            if judged <= 0 and resolved <= 0:
                return default
            if resolved <= 0:
                return default
            success = completed / resolved
            drag_penalty = dragging / resolved
            failure_penalty = failed / resolved
            return self._clamp01(success - drag_penalty * 0.18 - failure_penalty * 0.24)

        def _focus_effectiveness(focus: str, default: float) -> float:
            global_bucket = dict(focus_stats.get(focus) or {})
            contextual_bucket = dict(contextual_focus_stats.get(focus) or {})
            global_effect = _effectiveness_from_bucket(global_bucket, default)
            if not contextual_bucket:
                return global_effect
            contextual_effect = _effectiveness_from_bucket(contextual_bucket, global_effect)
            contextual_judged = int(contextual_bucket.get("judged") or 0)
            global_judged = int(global_bucket.get("judged") or 0)
            if contextual_judged <= 0:
                return global_effect
            confidence = min(0.75, 0.35 + contextual_judged * 0.08 + global_judged * 0.02)
            return self._clamp01(global_effect * (1.0 - confidence) + contextual_effect * confidence)

        focus_effectiveness = {
            "truthfulness": _focus_effectiveness("truthfulness", default=0.56),
            "memory_continuity": _focus_effectiveness("memory_continuity", default=0.58),
            "learning_expansion": _focus_effectiveness("learning_expansion", default=0.54),
            "queue_hygiene": _focus_effectiveness("queue_hygiene", default=0.48),
            "body_growth": _focus_effectiveness("body_growth", default=0.44),
            "observation": _focus_effectiveness("observation", default=0.52),
        }
        observation_recovery_advantage = max(
            0.0,
            focus_effectiveness["observation"] - focus_effectiveness["learning_expansion"],
        )
        contextual_observation_available = bool(contextual_focus_stats.get("observation"))
        unresolved_observation_pressure = 0.0
        observation_recovery_signal = 0.0
        observation_pressure_samples: list[float] = []
        observation_recovery_samples: list[float] = []
        for stats in observation_target_stats.values():
            if not isinstance(stats, dict):
                continue
            recommended = max(0, int(stats.get("recommended") or 0))
            resolved = max(0, int(stats.get("resolved") or 0))
            stalled = max(0, int(stats.get("stalled") or 0))
            last_risk = self._clamp01(stats.get("last_risk") or 0.0)
            if recommended <= 0:
                continue
            unresolved_ratio = max(0.0, (recommended - resolved) / max(recommended, 1))
            recovery_ratio = resolved / max(recommended, 1)
            pressure_sample = last_risk * 0.04
            if recommended >= 2 or stalled > 0:
                pressure_sample += (
                    unresolved_ratio * 0.12
                    + min(stalled, 3) * 0.05
                    + last_risk * 0.04
                )
            observation_pressure_samples.append(pressure_sample)
            observation_recovery_samples.append(recovery_ratio * 0.08)
        if observation_pressure_samples:
            unresolved_observation_pressure = self._clamp01(
                sum(observation_pressure_samples) / len(observation_pressure_samples)
                + min(0.06, max(0, len(observation_pressure_samples) - 1) * 0.02)
            )
        if observation_recovery_samples:
            observation_recovery_signal = self._clamp01(
                sum(observation_recovery_samples) / len(observation_recovery_samples)
            )

        agenda_drag_pressure = 0.0
        agenda_resolution_signal = 0.0
        for topic, stats in agenda_topic_stats.items():
            if not isinstance(stats, dict):
                continue
            dragging = max(0, int(stats.get("dragging") or 0))
            active_cycles = max(0, int(stats.get("active_cycles") or 0))
            resolved = max(0, int(stats.get("resolved") or 0))
            seen = max(0, int(stats.get("seen") or 0))
            if seen <= 0:
                continue
            agenda_drag_pressure += max(0.0, (dragging + max(active_cycles - resolved, 0)) / max(seen, 1)) * 0.06
            agenda_resolution_signal += (resolved / max(seen, 1)) * 0.05

        learning_expansion_bias = self._clamp01(
            0.52
            + (learning_success - 0.5) * 0.4
            + (0.08 if reflection.learning_yield_state == "strong" else 0.0)
            - reflection.queue_blockage_pressure * 0.18
            + (focus_effectiveness["learning_expansion"] - 0.5) * 0.16
            - unresolved_observation_pressure * 0.22
            + observation_recovery_signal * 0.18
            + agenda_resolution_signal * 0.12
            - float(policy.get("dynamic_learning_expansion_suppression") or 0.0)
        )
        truthfulness_bias = self._clamp01(
            0.56
            + world_model.truthfulness_pressure * 0.32
            + max(0.0, 0.55 - learning_success) * 0.08
            + (focus_effectiveness["truthfulness"] - 0.5) * 0.18
            + min(
                0.18,
                sum(
                    (
                        self._clamp01(stats.get("last_risk") or 0.0) * 0.1
                        + max(0, int(stats.get("stalled") or 0)) * 0.03
                    )
                    for target, stats in observation_target_stats.items()
                    if target in {"truthfulness", "latent_truthfulness"}
                    and isinstance(stats, dict)
                ),
            )
            + float(policy.get("dynamic_truthfulness_bias_boost") or 0.0)
        )
        memory_continuity_bias = self._clamp01(
            0.58
            + (memory_success - 0.5) * 0.18
            + world_model.memory_pressure * 0.22
            + (focus_effectiveness["memory_continuity"] - 0.5) * 0.14
        )
        queue_hygiene_bias = self._clamp01(
            0.44
            + reflection.queue_blockage_pressure * 0.34
            + max(0.0, 0.5 - queue_success) * 0.22
            + reflection.repeated_drive_pressure * 0.1
            + (focus_effectiveness["queue_hygiene"] - 0.45) * 0.16
            + min(
                0.16,
                sum(
                    (
                        self._clamp01(stats.get("last_risk") or 0.0) * 0.08
                        + max(0, int(stats.get("stalled") or 0)) * 0.03
                    )
                    for target, stats in observation_target_stats.items()
                    if target == "queue_blockage" and isinstance(stats, dict)
                ),
            )
            + agenda_drag_pressure * 0.08
        )
        body_growth_bias = self._clamp01(
            0.42
            + (body_success - 0.45) * 0.28
            + world_model.body_upgrade_readiness * 0.16
            - (0.18 if reflection.body_growth_blocked else 0.0)
            - reflection.queue_blockage_pressure * 0.12
            + (focus_effectiveness["body_growth"] - 0.42) * 0.14
            - unresolved_observation_pressure * 0.08
        )
        observation_bias = self._clamp01(
            0.3
            + reflection.queue_blockage_pressure * 0.28
            + max(0.0, 0.52 - reflection.autonomy_readiness) * 0.45
            + max(0.0, 0.55 - learning_success) * 0.14
            + (focus_effectiveness["observation"] - 0.5) * 0.34
            + (0.22 if reflection.dominant_constraint == "weak_learning_yield" else 0.0)
            + (0.18 if reflection.dominant_constraint == "historical_underdelivery" else 0.0)
            + (
                observation_recovery_advantage * 0.28
                if reflection.dominant_constraint in {"weak_learning_yield", "historical_underdelivery"}
                else 0.0
            )
            + (
                0.08
                if contextual_observation_available
                and reflection.dominant_constraint in {"weak_learning_yield", "historical_underdelivery"}
                else 0.0
            )
            + unresolved_observation_pressure * 0.36
            - observation_recovery_signal * 0.18
            + agenda_drag_pressure * 0.12
            + recent_relapse_drag_ratio * 0.08
        )
        candidate_throttle = self._clamp01(
            0.18
            + reflection.queue_blockage_pressure * 0.32
            + reflection.repeated_drive_pressure * 0.24
            + max(0.0, 0.5 - reflection.autonomy_readiness) * 0.3
            + max(0.0, 0.5 - focus_effectiveness["learning_expansion"]) * 0.06
            + max(0.0, 0.5 - focus_effectiveness["body_growth"]) * 0.04
            + (0.08 if reflection.dominant_constraint == "weak_learning_yield" else 0.0)
            + unresolved_observation_pressure * 0.34
            + agenda_drag_pressure * 0.1
            - observation_recovery_signal * 0.1
            + recent_relapse_drag_ratio * 0.12
            + float(policy.get("dynamic_candidate_throttle_boost") or 0.0)
        )
        observation_bias = self._clamp01(
            observation_bias + float(policy.get("dynamic_observation_bias_boost") or 0.0)
        )

        focus_candidates = {
            "truthfulness": truthfulness_bias,
            "memory_continuity": memory_continuity_bias,
            "learning_expansion": learning_expansion_bias,
            "queue_hygiene": queue_hygiene_bias,
            "body_growth": body_growth_bias,
            "observation": observation_bias,
        }
        preferred_focus = max(focus_candidates.items(), key=lambda item: item[1])[0]
        if (
            reflection.dominant_constraint == "historical_underdelivery"
            and observation_bias >= 0.72
            and preferred_focus == "memory_continuity"
        ):
            preferred_focus = "observation"
        if (
            scoped_historical_drag_ratio >= 0.66
            and (
                preferred_focus == "observation"
                or reflection.autonomy_readiness <= 0.18
                or observation_bias >= 0.58
            )
        ):
            candidate_budget = 1
        elif (
            reflection.dominant_constraint == "historical_underdelivery"
            and recent_relapse_drag_ratio >= 0.66
            and recent_relapse_drag_count >= 2
        ):
            candidate_budget = 1
        elif candidate_throttle >= 0.72:
            candidate_budget = 1
        elif candidate_throttle >= 0.45:
            candidate_budget = 2
        else:
            candidate_budget = 4
        if preferred_focus == "observation" or observation_bias >= 0.7:
            exploratory_learning_quota = 0
        elif candidate_throttle >= 0.65:
            exploratory_learning_quota = 0
        elif candidate_throttle >= 0.4 or preferred_focus == "queue_hygiene":
            exploratory_learning_quota = 1
        else:
            exploratory_learning_quota = 2
        body_growth_quota = (
            1
            if (
                body_growth_bias >= 0.58
                and candidate_throttle < 0.62
                and preferred_focus in {"body_growth", "learning_expansion"}
            )
            else 0
        )

        rationale_parts = [
            f"preferred focus is {preferred_focus}",
            f"candidate throttle is {candidate_throttle:.2f}",
            f"candidate budget is {candidate_budget}",
            f"learning bias is {learning_expansion_bias:.2f}",
            f"queue bias is {queue_hygiene_bias:.2f}",
        ]
        if focus_stats:
            rationale_parts.append(
                f"strategy memory favors {preferred_focus} at {focus_effectiveness.get(preferred_focus, 0.5):.2f} effectiveness"
            )
        if contextual_focus_stats:
            rationale_parts.append(
                f"context posture memory is active for {context_key}"
            )
        if observation_bias >= 0.6:
            rationale_parts.append("observation bias is elevated because autonomous output should slow down")

        return DriveAdaptivePolicy(
            learning_expansion_bias=learning_expansion_bias,
            truthfulness_bias=truthfulness_bias,
            memory_continuity_bias=memory_continuity_bias,
            queue_hygiene_bias=queue_hygiene_bias,
            body_growth_bias=body_growth_bias,
            observation_bias=observation_bias,
            candidate_throttle=candidate_throttle,
            candidate_budget=candidate_budget,
            exploratory_learning_quota=exploratory_learning_quota,
            body_growth_quota=body_growth_quota,
            preferred_focus=preferred_focus,
            rationale="; ".join(rationale_parts) + ".",
            source_evidence=[
                f"learning_success={learning_success:.2f}",
                f"queue_success={queue_success:.2f}",
                f"body_success={body_success:.2f}",
                f"memory_success={memory_success:.2f}",
                f"historical_drag_scope={scoped_historical_scope}",
                f"historical_drag_ratio={historical_drag_ratio:.2f}",
                f"scoped_historical_drag_ratio={scoped_historical_drag_ratio:.2f}",
                f"recent_relapse_drag_count={recent_relapse_drag_count}",
                f"recent_relapse_drag_ratio={recent_relapse_drag_ratio:.2f}",
                f"queue_blockage_pressure={reflection.queue_blockage_pressure:.2f}",
                f"autonomy_readiness={reflection.autonomy_readiness:.2f}",
                f"context_key={context_key}",
                f"observation_recovery_advantage={observation_recovery_advantage:.2f}",
                f"unresolved_observation_pressure={unresolved_observation_pressure:.2f}",
                f"observation_recovery_signal={observation_recovery_signal:.2f}",
                f"agenda_drag_pressure={agenda_drag_pressure:.2f}",
                f"agenda_resolution_signal={agenda_resolution_signal:.2f}",
                f"dynamic_candidate_throttle_boost={float(policy.get('dynamic_candidate_throttle_boost') or 0.0):.2f}",
                f"dynamic_observation_bias_boost={float(policy.get('dynamic_observation_bias_boost') or 0.0):.2f}",
                f"dynamic_truthfulness_bias_boost={float(policy.get('dynamic_truthfulness_bias_boost') or 0.0):.2f}",
                f"dynamic_learning_expansion_suppression={float(policy.get('dynamic_learning_expansion_suppression') or 0.0):.2f}",
                f"focus_effectiveness[{preferred_focus}]={focus_effectiveness.get(preferred_focus, 0.5):.2f}",
                f"candidate_budget={candidate_budget}",
                f"exploratory_learning_quota={exploratory_learning_quota}",
                f"body_growth_quota={body_growth_quota}",
            ],
        )

    def _strategy_context_key(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        reflection: DriveReflection,
    ) -> str:
        user_mode = str(perception.user_mode or "unknown").strip().lower() or "unknown"
        system_posture = str(perception.system_posture or "unknown").strip().lower() or "unknown"
        dominant_constraint = (
            str(reflection.dominant_constraint or "none").strip().lower() or "none"
        )
        return f"{user_mode}|{system_posture}|{dominant_constraint}"

    def _detect_needs(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        self_evolution_plan: Dict[str, Any],
    ) -> List[DriveNeed]:
        needs: List[DriveNeed] = []
        truthfulness_review_active = (
            self_learning_plan.get("eligible_for_planning")
            and perception.correction_signals >= 3
        )
        if memory_plan.get("eligible_for_planning"):
            memory_constraint_penalty = 0.0
            if reflection.dominant_constraint == "historical_underdelivery":
                memory_constraint_penalty += 0.08
            if adaptive_policy.preferred_focus == "observation":
                memory_constraint_penalty += 0.06
            needs.append(
                DriveNeed(
                    need_type="stabilize_memory_continuity",
                    severity=self._clamp01(
                        world_model.memory_pressure
                        + 0.08
                        + adaptive_policy.memory_continuity_bias * 0.22
                        - memory_constraint_penalty
                    ),
                    urgency=self._clamp01(
                        world_model.memory_pressure
                        + 0.1
                        + adaptive_policy.memory_continuity_bias * 0.18
                        - memory_constraint_penalty * 0.82
                    ),
                    confidence=self._clamp01(
                        0.68
                        + adaptive_policy.memory_continuity_bias * 0.22
                        - memory_constraint_penalty * 0.32
                    ),
                    rationale="Memory continuity work remains a standing supervisory obligation during viable execution windows.",
                    source_evidence=[
                        f"in_execution_window={perception.in_execution_window}",
                        f"memory_idle={perception.checks.get('has_memory_idle', False)}",
                        f"memory_continuity_bias={adaptive_policy.memory_continuity_bias:.2f}",
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
                    severity=self._clamp01(
                        world_model.truthfulness_pressure
                        + adaptive_policy.truthfulness_bias * 0.16
                        + truthfulness_priority_bonus
                    ),
                    urgency=self._clamp01(
                        world_model.truthfulness_pressure
                        + adaptive_policy.truthfulness_bias * 0.12
                        + truthfulness_priority_bonus * 0.9
                    ),
                    confidence=self._clamp01(
                        0.72
                        + adaptive_policy.truthfulness_bias * 0.24
                        + truthfulness_priority_bonus * 0.45
                    ),
                    rationale="Recent errors and uncertainty signals indicate truthfulness debt that should be surfaced and reviewed.",
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
            if reflection.dominant_constraint == "historical_underdelivery":
                learning_constraint_penalty += 0.14
            if adaptive_policy.preferred_focus == "observation":
                learning_constraint_penalty += 0.08
            if truthfulness_review_active and adaptive_policy.preferred_focus == "truthfulness":
                learning_constraint_penalty += 0.06
            needs.append(
                DriveNeed(
                    need_type="expand_learning_frontier",
                    severity=self._clamp01(
                        world_model.learning_momentum
                        - 0.02
                        + reflection.autonomy_readiness * 0.16
                        + adaptive_policy.learning_expansion_bias * 0.2
                        - reflection.queue_blockage_pressure * 0.12
                        - learning_constraint_penalty
                    ),
                    urgency=self._clamp01(
                        world_model.learning_momentum
                        + reflection.recent_learning_quality * 0.15
                        + adaptive_policy.learning_expansion_bias * 0.1
                        - reflection.queue_blockage_pressure * 0.08
                        - adaptive_policy.candidate_throttle * 0.12
                        - learning_constraint_penalty * 0.72
                    ),
                    confidence=self._clamp01(
                        world_model.self_confidence * 0.52
                        + reflection.autonomy_readiness * 0.22
                        + adaptive_policy.learning_expansion_bias * 0.26
                        - learning_constraint_penalty * 0.46
                    ),
                    rationale=(
                        "Learning should expand when recent evidence still yields value, "
                        "but it should cool down when queue blockage suggests more output would only add pressure."
                    ),
                    source_evidence=[
                        f"learning_quality={perception.learning_quality:.2f}",
                        f"queued_learning_count={perception.queued_learning_count}",
                        f"has_learning_history={perception.has_learning_history}",
                        f"learning_yield_state={reflection.learning_yield_state}",
                        f"queue_blockage_state={reflection.queue_blockage_state}",
                        f"learning_expansion_bias={adaptive_policy.learning_expansion_bias:.2f}",
                        f"candidate_throttle={adaptive_policy.candidate_throttle:.2f}",
                    ],
                )
            )
        if (
            self_evolution_plan.get("eligible_for_planning")
            and perception.shell_slot_present
            and perception.learning_quality >= 60.0
            and not reflection.body_growth_blocked
        ):
            needs.append(
                DriveNeed(
                    need_type="prepare_body_growth",
                    severity=self._clamp01(
                        world_model.body_upgrade_readiness
                        - 0.02
                        + reflection.autonomy_readiness * 0.12
                        + adaptive_policy.body_growth_bias * 0.18
                    ),
                    urgency=self._clamp01(
                        world_model.body_upgrade_readiness
                        + reflection.recent_learning_quality * 0.08
                        + adaptive_policy.body_growth_bias * 0.1
                        - adaptive_policy.candidate_throttle * 0.08
                    ),
                    confidence=self._clamp01(
                        0.5
                        + world_model.self_confidence * 0.12
                        + reflection.autonomy_readiness * 0.1
                        + adaptive_policy.body_growth_bias * 0.28
                    ),
                    rationale="Body growth should only be prepared when recent learning has real yield and shell improvement is not already blocked by recent output pressure.",
                    source_evidence=[
                        f"learning_quality={perception.learning_quality:.2f}",
                        f"shell_slot_present={perception.shell_slot_present}",
                        f"queued_body_improvement_count={perception.queued_body_improvement_count}",
                        f"body_growth_blocked={reflection.body_growth_blocked}",
                        f"body_growth_bias={adaptive_policy.body_growth_bias:.2f}",
                    ],
                )
            )
        if self_evolution_plan.get("eligible_for_planning"):
            queue_need_score = self._clamp01(
                0.2
                + min(perception.active_queue_count, 5) * 0.08
                + min(perception.stale_queue_count + perception.pending_review_count, 4) * 0.08
                + reflection.queue_blockage_pressure * 0.18
                + adaptive_policy.queue_hygiene_bias * 0.16
            )
            needs.append(
                DriveNeed(
                    need_type="clear_governance_backlog",
                    severity=queue_need_score,
                    urgency=self._clamp01(
                        queue_need_score
                        - 0.02
                        + reflection.repeated_drive_pressure * 0.08
                        + adaptive_policy.queue_hygiene_bias * 0.12
                    ),
                    confidence=self._clamp01(
                        0.56
                        + reflection.queue_blockage_pressure * 0.16
                        + adaptive_policy.queue_hygiene_bias * 0.22
                    ),
                    rationale="Queue hygiene becomes more important when repeated endogenous output is not closing loops and backlog pressure keeps accumulating.",
                    source_evidence=[
                        f"active_queue_count={perception.active_queue_count}",
                        f"stale_queue_count={perception.stale_queue_count}",
                        f"pending_review_count={perception.pending_review_count}",
                        f"repeated_drive_pressure={reflection.repeated_drive_pressure:.2f}",
                        f"queue_hygiene_bias={adaptive_policy.queue_hygiene_bias:.2f}",
                    ],
                )
            )
        if (
            reflection.queue_blockage_pressure >= 0.45
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
            needs.append(
                DriveNeed(
                    need_type="observe_before_acting",
                    severity=self._clamp01(
                        0.34
                        + reflection.queue_blockage_pressure * 0.32
                        + max(0.0, 0.5 - reflection.autonomy_readiness) * 0.45
                        + adaptive_policy.observation_bias * 0.18
                        + observation_constraint_bonus
                    ),
                    urgency=self._clamp01(
                        0.28
                        + reflection.queue_blockage_pressure * 0.28
                        + max(0.0, 0.45 - reflection.autonomy_readiness) * 0.4
                        + adaptive_policy.observation_bias * 0.14
                        + observation_constraint_bonus * 0.85
                    ),
                    confidence=self._clamp01(0.62 + adaptive_policy.observation_bias * 0.28),
                    rationale="The drive should slow itself down and observe when repeated output is meeting blockage or autonomy readiness is not yet strong enough.",
                    source_evidence=[
                        f"queue_blockage_pressure={reflection.queue_blockage_pressure:.2f}",
                        f"autonomy_readiness={reflection.autonomy_readiness:.2f}",
                        f"dominant_constraint={reflection.dominant_constraint}",
                        f"observation_bias={adaptive_policy.observation_bias:.2f}",
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

    def _synthesize_intents(
        self,
        *,
        needs: List[DriveNeed],
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
    ) -> List[DriveIntent]:
        intents: List[DriveIntent] = []
        for need in needs:
            priority = self._clamp01(
                need.severity * 0.45
                + need.urgency * 0.35
                + need.confidence * 0.20
            )
            if need.need_type == "expand_learning_frontier":
                priority = self._clamp01(
                    priority
                    + adaptive_policy.learning_expansion_bias * 0.08
                    - adaptive_policy.candidate_throttle * 0.1
                )
            elif need.need_type == "prepare_body_growth":
                priority = self._clamp01(
                    priority
                    + adaptive_policy.body_growth_bias * 0.08
                    - adaptive_policy.candidate_throttle * 0.06
                )
            elif need.need_type == "clear_governance_backlog":
                priority = self._clamp01(priority + adaptive_policy.queue_hygiene_bias * 0.08)
            elif need.need_type == "observe_before_acting":
                priority = self._clamp01(priority + adaptive_policy.observation_bias * 0.12)
            if need.need_type == "stabilize_memory_continuity":
                intents.append(
                    DriveIntent(
                        intent_type="maintain_memory_continuity",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon="immediate",
                        output_channel="task_candidate",
                        source_needs=[need.need_type],
                        candidate_family="memory_maintenance",
                        candidate_kind="memory_maintenance",
                    )
                )
            elif need.need_type == "repair_truthfulness":
                intents.append(
                    DriveIntent(
                        intent_type="review_truthfulness_signals",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon="immediate",
                        output_channel="task_candidate",
                        source_needs=[need.need_type],
                        candidate_family="self_learning",
                        candidate_kind="truthfulness_review",
                    )
                )
            elif need.need_type == "expand_learning_frontier":
                intents.append(
                    DriveIntent(
                        intent_type="expand_learning_frontier",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon="near_term",
                        output_channel="task_candidate",
                        source_needs=[need.need_type],
                        candidate_family="self_learning",
                        candidate_kind=(
                            "shell_baseline_learning"
                            if perception.shell_slot_present and not perception.has_learning_history
                            else "exploratory_learning"
                        ),
                    )
                )
            elif need.need_type == "prepare_body_growth":
                intents.append(
                    DriveIntent(
                        intent_type="prepare_body_growth",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon="near_term",
                        output_channel="task_candidate",
                        source_needs=[need.need_type],
                        candidate_family="body_upgrade",
                        candidate_kind="body_improvement",
                    )
                )
            elif need.need_type == "clear_governance_backlog":
                intents.append(
                    DriveIntent(
                        intent_type="review_queue_hygiene",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon="near_term",
                        output_channel="task_candidate",
                        source_needs=[need.need_type],
                        candidate_family="general_self_evolution",
                        candidate_kind="queue_hygiene_review",
                    )
                )
            elif need.need_type == "observe_before_acting":
                intents.append(
                    DriveIntent(
                        intent_type="observe_before_acting",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon=(
                            "immediate"
                            if reflection.queue_blockage_pressure >= 0.55
                            else "near_term"
                        ),
                        output_channel="drive_signal",
                        source_needs=[need.need_type],
                    )
                )
        intents.sort(key=lambda item: item.priority, reverse=True)
        return intents

    def _emit_drive_signals(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
        needs: List[DriveNeed],
        intents: List[DriveIntent],
    ) -> List[DriveSignal]:
        signals: List[DriveSignal] = []
        need_lookup = {need.need_type: need for need in needs}
        intent_lookup = {intent.intent_type: intent for intent in intents}

        if world_model.queue_health in {"busy", "strained"}:
            queue_need = need_lookup.get("clear_governance_backlog")
            queue_intent = intent_lookup.get("review_queue_hygiene")
            signals.append(
                DriveSignal(
                    signal_type="governance_review_suggestion",
                    priority=self._clamp01(
                        (queue_need.severity if queue_need else 0.45)
                        + (0.08 if world_model.queue_health == "strained" else 0.0)
                    ),
                    message="Queue state suggests a governance review pass before more autonomous work accumulates.",
                    rationale=(
                        queue_need.rationale
                        if queue_need is not None
                        else "Backlog pressure and review debt indicate the queue should be examined."
                    ),
                    source_needs=(
                        [queue_need.need_type]
                        if queue_need is not None
                        else ["clear_governance_backlog"]
                    ),
                    related_intent=queue_intent.intent_type if queue_intent is not None else None,
                    payload={
                        "queue_health": world_model.queue_health,
                        "active_queue_count": perception.active_queue_count,
                        "stale_queue_count": perception.stale_queue_count,
                        "pending_review_count": perception.pending_review_count,
                    },
                )
            )
        else:
            queue_need = need_lookup.get("clear_governance_backlog")
            queue_intent = intent_lookup.get("review_queue_hygiene")
            if (
                queue_need is not None
                and (
                    perception.pending_review_count > 0
                    or perception.stale_queue_count > 0
                    or perception.active_queue_count > 0
                )
            ):
                signals.append(
                    DriveSignal(
                        signal_type="governance_review_suggestion",
                        priority=self._clamp01(queue_need.severity + 0.06),
                        message="Queue review is suggested because review debt or stale work is already present even before full backlog strain emerges.",
                        rationale=queue_need.rationale,
                        source_needs=[queue_need.need_type],
                        related_intent=queue_intent.intent_type if queue_intent is not None else None,
                        payload={
                            "queue_health": world_model.queue_health,
                            "active_queue_count": perception.active_queue_count,
                            "stale_queue_count": perception.stale_queue_count,
                            "pending_review_count": perception.pending_review_count,
                            "trigger": "early_review_debt",
                        },
                    )
                )

        truthfulness_need = need_lookup.get("repair_truthfulness")
        truthfulness_intent = intent_lookup.get("review_truthfulness_signals")
        if truthfulness_need is not None and perception.correction_signals >= 3:
            signals.append(
                DriveSignal(
                    signal_type="observation_signal",
                    priority=self._clamp01(
                        truthfulness_need.severity
                        + 0.08
                        + adaptive_policy.truthfulness_bias * 0.1
                    ),
                    message=(
                        "Truthfulness-focused observation is recommended because correction pressure is rising "
                        "even if the overall drive is also slowing down."
                    ),
                    rationale=truthfulness_need.rationale,
                    source_needs=[truthfulness_need.need_type],
                    related_intent=(
                        truthfulness_intent.intent_type
                        if truthfulness_intent is not None
                        else None
                    ),
                    payload={
                        "observation_target": "truthfulness",
                        "correction_signals": perception.correction_signals,
                        "recent_errors": perception.recent_errors,
                        "uncertainty_count": perception.uncertainty_count,
                        "system_posture": perception.system_posture,
                    },
                )
            )

        observe_need = need_lookup.get("observe_before_acting")
        if observe_need is not None:
            observe_intent = intent_lookup.get("observe_before_acting")
            signals.append(
                DriveSignal(
                    signal_type="observation_signal",
                    priority=self._clamp01(
                        observe_need.severity + 0.06 + adaptive_policy.observation_bias * 0.12
                    ),
                    message="Observation is recommended before more autonomous output because the drive is encountering blockage or weak readiness.",
                    rationale=observe_need.rationale,
                    source_needs=[observe_need.need_type],
                    related_intent=observe_intent.intent_type if observe_intent is not None else None,
                    payload={
                        "observation_target": reflection.dominant_constraint,
                        "queue_blockage_state": reflection.queue_blockage_state,
                        "autonomy_readiness": round(reflection.autonomy_readiness, 4),
                        "repeated_drive_pressure": round(reflection.repeated_drive_pressure, 4),
                    },
                )
            )
            signals.append(
                DriveSignal(
                    signal_type="autonomy_alignment_signal",
                    priority=self._clamp01(
                        observe_need.urgency + 0.04 + adaptive_policy.observation_bias * 0.16
                    ),
                    message="Autonomous output should be aligned and throttled before pushing more candidate work.",
                    rationale=(
                        f"{reflection.rationale} {adaptive_policy.rationale}"
                    ),
                    source_needs=[observe_need.need_type],
                    related_intent=observe_intent.intent_type if observe_intent is not None else None,
                    payload={
                        "dominant_constraint": reflection.dominant_constraint,
                        "learning_yield_state": reflection.learning_yield_state,
                        "queue_blockage_state": reflection.queue_blockage_state,
                    },
                )
            )
        elif perception.learning_quality >= 75.0:
            observation_target = "body_growth"
            related_intent = "prepare_body_growth"
            source_need = "prepare_body_growth"
            signals.append(
                DriveSignal(
                    signal_type="observation_signal",
                    priority=self._clamp01(
                        0.52
                        + (0.18 if observation_target == "truthfulness" else 0.1)
                        + min(perception.correction_signals, 4) * 0.04
                    ),
                    message=(
                        "Observation is recommended before further autonomous actions because correction pressure is rising."
                        if observation_target == "truthfulness"
                        else "Observation is recommended because learning quality suggests a possible growth window is forming."
                    ),
                    rationale=(
                        need_lookup[source_need].rationale
                        if source_need in need_lookup
                        else "The current state warrants supervisory observation."
                    ),
                    source_needs=[source_need],
                    related_intent=related_intent,
                    payload={
                        "observation_target": observation_target,
                        "correction_signals": perception.correction_signals,
                        "learning_quality": round(perception.learning_quality, 4),
                        "system_posture": perception.system_posture,
                    },
                )
            )

        signals.append(
            DriveSignal(
                signal_type="drive_posture_signal",
                priority=self._clamp01(0.4 + adaptive_policy.candidate_throttle * 0.3),
                message="The endogenous drive has selected a current governance posture and compatibility budget for this cycle.",
                rationale=adaptive_policy.rationale,
                source_needs=(
                    [observe_need.need_type]
                    if observe_need is not None
                    else []
                ),
                related_intent=(
                    observe_intent.intent_type
                    if observe_need is not None and observe_intent is not None
                    else None
                ),
                payload={
                    "preferred_focus": adaptive_policy.preferred_focus,
                    "candidate_budget": adaptive_policy.candidate_budget,
                    "exploratory_learning_quota": adaptive_policy.exploratory_learning_quota,
                    "body_growth_quota": adaptive_policy.body_growth_quota,
                    "candidate_throttle": round(adaptive_policy.candidate_throttle, 4),
                    "source_evidence": list(adaptive_policy.source_evidence),
                },
            )
        )

        signals.sort(key=lambda item: item.priority, reverse=True)
        return signals

    def _intent_metadata(
        self,
        *,
        intent: DriveIntent,
        needs: List[DriveNeed],
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
    ) -> Dict[str, Any]:
        report = DriveDeliberationReport(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            needs=needs,
            intents=[intent],
        ).to_dict()
        intent_dict = report["intents"][0] if report["intents"] else {}
        linked_needs = [
            need
            for need in report["needs"]
            if need["need_type"] in set(intent.source_needs)
        ]
        return {
            "perception": report["perception"],
            "world_model": report["world_model"],
            "reflection": report["reflection"],
            "adaptive_policy": report["adaptive_policy"],
            "intent": intent_dict,
            "needs": linked_needs,
        }

    def _build_scored_candidate(
        self,
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
        utility, score_breakdown = self._score_candidate(
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

    def _score_candidate(
        self,
        *,
        candidate_kind: str,
        core_value_strength: float,
        urgency: float,
        novelty: float,
        specificity: float,
        execution_readiness: float,
        queue_pressure_penalty: float = 0.0,
        repetition_penalty: float = 0.0,
        adaptive_factor: float = 1.0,
    ) -> tuple[float, Dict[str, Any]]:
        dimensions = {
            "core_value_strength": round(self._clamp01(core_value_strength), 4),
            "urgency": round(self._clamp01(urgency), 4),
            "novelty": round(self._clamp01(novelty), 4),
            "specificity": round(self._clamp01(specificity), 4),
            "execution_readiness": round(self._clamp01(execution_readiness), 4),
        }
        penalties = {
            "queue_pressure_penalty": round(self._clamp01(queue_pressure_penalty), 4),
            "repetition_penalty": round(self._clamp01(repetition_penalty), 4),
        }
        raw_score = (
            dimensions["core_value_strength"] * _SCORE_WEIGHTS["core_value_strength"]
            + dimensions["urgency"] * _SCORE_WEIGHTS["urgency"]
            + dimensions["novelty"] * _SCORE_WEIGHTS["novelty"]
            + dimensions["specificity"] * _SCORE_WEIGHTS["specificity"]
            + dimensions["execution_readiness"] * _SCORE_WEIGHTS["execution_readiness"]
            - penalties["queue_pressure_penalty"] * _SCORE_WEIGHTS["queue_pressure_penalty"]
            - penalties["repetition_penalty"] * _SCORE_WEIGHTS["repetition_penalty"]
        )
        normalized_adaptive_factor = round(max(0.7, min(1.25, float(adaptive_factor))), 4)
        utility = round(self._clamp01(raw_score * normalized_adaptive_factor), 4)
        return utility, {
            "score_model": "endogenous_drive_v2",
            "candidate_kind": candidate_kind,
            "dimensions": dimensions,
            "penalties": penalties,
            "weights": dict(_SCORE_WEIGHTS),
            "adaptive_factor": normalized_adaptive_factor,
            "utility": utility,
        }

    def _adaptive_factor_for_candidate(
        self,
        *,
        candidate_kind: str,
        adaptive_policy: DriveAdaptivePolicy,
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
        if candidate_kind in {"exploratory_learning", "shell_baseline_learning", "generic_learning_fallback"}:
            factor = (
                0.82
                + adaptive_policy.learning_expansion_bias * 0.3
                - adaptive_policy.candidate_throttle * 0.2
            )
            if adaptive_policy.preferred_focus == "learning_expansion":
                factor += 0.06
            return factor
        if candidate_kind == "queue_hygiene_review":
            factor = 0.84 + adaptive_policy.queue_hygiene_bias * 0.32
            if adaptive_policy.preferred_focus == "queue_hygiene":
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

    def _neutral_adaptive_policy(self) -> DriveAdaptivePolicy:
        return DriveAdaptivePolicy(
            learning_expansion_bias=0.5,
            truthfulness_bias=0.5,
            memory_continuity_bias=0.5,
            queue_hygiene_bias=0.5,
            body_growth_bias=0.5,
            observation_bias=0.5,
            candidate_throttle=0.0,
            candidate_budget=4,
            exploratory_learning_quota=2,
            body_growth_quota=1,
            preferred_focus="learning_expansion",
            rationale="fallback adaptive policy.",
        )

    def _candidate_kind_of(self, candidate: EndogenousTaskCandidate) -> str:
        metadata = dict(candidate.metadata or {})
        score_breakdown = dict(metadata.get("score_breakdown") or {})
        return str(score_breakdown.get("candidate_kind") or "").strip()

    def _adaptive_group_for_candidate(self, candidate: EndogenousTaskCandidate) -> Optional[str]:
        candidate_kind = self._candidate_kind_of(candidate)
        if candidate_kind in {
            "exploratory_learning",
            "shell_baseline_learning",
            "generic_learning_fallback",
        }:
            return "exploratory_learning"
        if candidate_kind == "body_improvement":
            return "body_growth"
        return None

    def _candidate_selection_priority(self, candidate: EndogenousTaskCandidate) -> float:
        metadata = dict(candidate.metadata or {})
        drive_judgement = dict(metadata.get("drive_judgement") or {})
        intent = dict(drive_judgement.get("intent") or {})
        intent_priority = intent.get("priority")
        if isinstance(intent_priority, (int, float)):
            return self._clamp01(float(intent_priority))

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
                return self._clamp01(max(samples))

        return float(candidate.utility)

    def _budget_priority_for_candidate(
        self,
        candidate: EndogenousTaskCandidate,
        *,
        adaptive_policy: DriveAdaptivePolicy,
    ) -> tuple[int, float, float, str]:
        candidate_kind = self._candidate_kind_of(candidate)
        preferred_focus = str(adaptive_policy.preferred_focus or "").strip().lower()
        aligned_kinds: Dict[str, set[str]] = {
            "truthfulness": {"truthfulness_review"},
            "queue_hygiene": {"queue_hygiene_review"},
            "memory_continuity": {"memory_maintenance"},
            "observation": {"truthfulness_review", "queue_hygiene_review"},
        }
        observation_tie_break = {
            "truthfulness_review": 0,
            "queue_hygiene_review": 1,
        }
        rank = 1
        if candidate_kind in aligned_kinds.get(preferred_focus, set()):
            rank = 0
        kind_tie_break = candidate_kind
        if preferred_focus == "observation":
            kind_tie_break = f"{observation_tie_break.get(candidate_kind, 9)}:{candidate_kind}"
        return (
            rank,
            -self._candidate_selection_priority(candidate),
            -float(candidate.utility),
            kind_tie_break,
        )

    def _apply_adaptive_candidate_budget(
        self,
        candidates: List[EndogenousTaskCandidate],
        *,
        adaptive_policy: DriveAdaptivePolicy,
    ) -> List[EndogenousTaskCandidate]:
        if not candidates:
            return []

        ordered = sorted(
            candidates,
            key=lambda candidate: self._budget_priority_for_candidate(
                candidate,
                adaptive_policy=adaptive_policy,
            ),
        )
        selected: List[EndogenousTaskCandidate] = []
        group_counts: Dict[str, int] = {
            "exploratory_learning": 0,
            "body_growth": 0,
        }
        group_limits: Dict[str, int] = {
            "exploratory_learning": max(0, int(adaptive_policy.exploratory_learning_quota)),
            "body_growth": max(0, int(adaptive_policy.body_growth_quota)),
        }
        budget = max(1, int(adaptive_policy.candidate_budget))
        observation_mode = (
            adaptive_policy.preferred_focus == "observation"
            or adaptive_policy.observation_bias >= 0.72
        )

        for candidate in ordered:
            candidate_kind = self._candidate_kind_of(candidate)
            if observation_mode and candidate_kind not in {
                "truthfulness_review",
                "queue_hygiene_review",
            }:
                continue
            group = self._adaptive_group_for_candidate(candidate)
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

    def _candidate_stream(
        self, idle_window: Dict[str, Any], *, existing_keys: set[str] = None
    ) -> List[EndogenousTaskCandidate]:
        if existing_keys is None:
            existing_keys = set()
        activity = dict(idle_window.get("activity") or {})
        drive_context = self._build_drive_context(idle_window)
        policy = drive_context["policy"]
        shell_slot_meta = self._get_shell_slot_meta(idle_window) or {}
        decisions_by_family = dict(idle_window.get("task_family_decisions") or {})
        decisions_by_governance = dict(idle_window.get("governance_task_type_decisions") or {})

        memory_plan = self._decision_for(
            "memory_maintenance",
            decisions_by_family,
            decisions_by_governance,
        )
        self_learning_plan = self._decision_for(
            "self_learning",
            decisions_by_family,
            decisions_by_governance,
        )
        self_evolution_plan = self._decision_for(
            "general_self_evolution",
            decisions_by_family,
            decisions_by_governance,
        )
        deliberation = self.build_deliberation_report(idle_window=idle_window)
        perception = deliberation.perception
        world_model = deliberation.world_model
        reflection = deliberation.reflection
        adaptive_policy = deliberation.adaptive_policy
        needs = list(deliberation.needs)
        intents = list(deliberation.intents)
        intents_by_kind = {
            str(intent.candidate_kind or ""): intent
            for intent in intents
            if intent.candidate_kind
        }

        lm_candidates = self._llm_task_proposals(
            idle_window=idle_window,
            existing_keys=existing_keys,
            deliberation=deliberation,
            drive_context=drive_context,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            self_evolution_plan=self_evolution_plan,
        )
        candidates: List[EndogenousTaskCandidate] = []
        if memory_plan.get("eligible_for_planning") and "continuity:memory_maintenance_sweep" not in existing_keys:
            memory_intent = intents_by_kind.get("memory_maintenance")
            candidates.append(
                self._build_scored_candidate(
                    stable_key="continuity:memory_maintenance_sweep",
                    title="Maintain long-term memory continuity",
                    summary=(
                        "Inspect memory-maintenance needs during an idle window so long-term "
                        "identity, summaries, and governance traces stay usable."
                    ),
                    priority="high",
                    governance_task_type="memory_maintenance",
                    task_family="memory_maintenance",
                    execution_kind="memory_maintenance",
                    value_tags=["continuity"],
                    candidate_kind="memory_maintenance",
                    score_inputs={
                        "core_value_strength": 1.0,
                        "urgency": self._memory_maintenance_urgency(idle_window),
                        "novelty": 0.58,
                        "specificity": 0.78,
                        "execution_readiness": 1.0,
                        "queue_pressure_penalty": self._queue_pressure_penalty(
                            drive_context,
                            governance_task_type="memory_maintenance",
                            task_family="memory_maintenance",
                            execution_kind="memory_maintenance",
                        ),
                        "adaptive_factor": self._adaptive_factor_for_candidate(
                            candidate_kind="memory_maintenance",
                            adaptive_policy=adaptive_policy,
                        ),
                    },
                    metadata=(
                        {
                            "drive_judgement": self._intent_metadata(
                                intent=memory_intent,
                                needs=needs,
                                perception=perception,
                                world_model=world_model,
                                reflection=reflection,
                                adaptive_policy=adaptive_policy,
                            )
                        }
                        if memory_intent is not None
                        else None
                    ),
                    evidence={
                        "idle_window_checks": dict(idle_window.get("checks") or {}),
                        "idle_seconds": dict(idle_window.get("idle_seconds") or {}),
                    },
                )
            )

        recent_errors = perception.recent_errors
        uncertainty_count = perception.uncertainty_count
        if (
            perception.correction_signals >= 3
            and self_learning_plan.get("eligible_for_planning")
            and "truthfulness:review_correction_signals" not in existing_keys
        ):
            truth_intent = intents_by_kind.get("truthfulness_review")
            candidates.append(
                self._build_scored_candidate(
                    stable_key="truthfulness:review_correction_signals",
                    title="Review recent uncertainty and correction signals",
                    summary=(
                        "Turn recent errors or high-uncertainty answers into a bounded "
                        "self-learning follow-up instead of letting them remain invisible."
                    ),
                    priority="high" if perception.correction_signals >= 3 else "normal",
                    governance_task_type="self_learning",
                    task_family="self_learning",
                    execution_kind=None,
                    value_tags=["truthfulness"],
                    candidate_kind="truthfulness_review",
                    score_inputs={
                        "core_value_strength": 0.98,
                        "urgency": self._clamp01(
                            0.35 + (min(perception.correction_signals, 6) / 6.0) * 0.65
                        ),
                        "novelty": 0.72 if idle_window.get("correction_signals") is not None else 0.68,
                        "specificity": self._clamp01(
                            0.55 + min(perception.correction_signals, 5) * 0.08
                        ),
                        "execution_readiness": 0.92,
                        "queue_pressure_penalty": self._queue_pressure_penalty(
                            drive_context,
                            governance_task_type="self_learning",
                            task_family="self_learning",
                        ),
                        "adaptive_factor": self._adaptive_factor_for_candidate(
                            candidate_kind="truthfulness_review",
                            adaptive_policy=adaptive_policy,
                        ),
                    },
                    metadata=(
                        {
                            "drive_judgement": self._intent_metadata(
                                intent=truth_intent,
                                needs=needs,
                                perception=perception,
                                world_model=world_model,
                                reflection=reflection,
                                adaptive_policy=adaptive_policy,
                            )
                        }
                        if truth_intent is not None
                        else None
                    ),
                    evidence={
                        "recent_errors": recent_errors,
                        "uncertainty_high_count": uncertainty_count,
                        "correction_signals": perception.correction_signals,
                        "signal_source": "evaluate_idle_window" if idle_window.get("correction_signals") is not None else "raw_counts",
                    },
                )
            )

        active_sessions = perception.active_sessions
        if self_learning_plan.get("eligible_for_planning"):
            shell_slot_id = str(shell_slot_meta.get("slot_id") or "shell").strip()
            shell_worktree = str(shell_slot_meta.get("worktree_path") or "").strip()
            baseline_key = f"creativity:self_learning:shell_baseline:{shell_slot_id or 'shell'}"
            baseline_added = False
            has_learning_history = perception.has_learning_history
            learning_intent = intents_by_kind.get("exploratory_learning")
            shell_baseline_intent = intents_by_kind.get("shell_baseline_learning")

            # Three-tier fallback chain for learning topics, matching the
            # architectural baseline §3.4 "LLM 优先 + 启发式降级" pattern:
            #   Tier 1: LLM-generated topics from compressed memory context
            #   Tier 2: Recent compressed memories from Mem (local, no LLM)
            #   Tier 3: Mechanical extraction from activity metadata
            topics: list[dict] = []
            topic_source = "none"

            governor_active = idle_window.get("governor_mode_active", False)
            llm_topics = self._llm_generate_learning_topics(
                activity,
                max_topics=4,
                governor_mode=governor_active,
                drive_context=drive_context,
            )
            if llm_topics:
                topics = llm_topics
                topic_source = "llm"

            if not topics:
                mem_topics = self._mem_extract_learning_topics(activity, max_topics=4)
                if mem_topics:
                    topics = mem_topics
                    topic_source = "mem_compressed"

            if not topics:
                mechanical_topic = self._extract_learning_topic(activity)
                if mechanical_topic:
                    topics = [{"title": mechanical_topic, "summary": (
                        f"Use idle capacity to research '{mechanical_topic}' — the most recent "
                        f"user-discussed topic that may benefit from deeper investigation."
                    )}]
                    topic_source = "activity_metadata"

            had_raw_topics = bool(topics)
            topics = self._filter_learning_topics(
                topics,
                drive_context=drive_context,
                existing_keys=existing_keys,
                cooldown_hours=int(policy.get("learning_topic_cooldown_hours", 24) or 24),
                overlap_threshold=float(policy.get("topic_overlap_threshold", 0.6) or 0.6),
                max_topics=3,
            )

            if (
                shell_worktree
                and not has_learning_history
                and baseline_key not in existing_keys
            ):
                candidates.append(
                    self._build_shell_baseline_learning_candidate(
                        stable_key=baseline_key,
                        active_sessions=active_sessions,
                        shell_slot_id=shell_slot_id,
                        shell_worktree=shell_worktree,
                        trigger="bootstrap_shell_baseline",
                        drive_context=drive_context,
                        bootstrap=True,
                        drive_judgement=(
                            self._intent_metadata(
                                intent=shell_baseline_intent,
                                needs=needs,
                                perception=perception,
                                world_model=world_model,
                                reflection=reflection,
                                adaptive_policy=adaptive_policy,
                            )
                            if shell_baseline_intent is not None
                            else None
                        ),
                        adaptive_policy=adaptive_policy,
                    )
                )
                existing_keys.add(baseline_key)
                baseline_added = True

            generated_count = 0
            for topic in topics:
                topic_key = _stable_key_for_topic(topic["title"])
                if topic_key in existing_keys:
                    continue  # Skip duplicate topic
                title = f"Research: {topic['title']}"
                summary = topic.get("summary") or topic["title"]
                candidates.append(
                    self._build_scored_candidate(
                        stable_key=topic_key,  # Dynamic key: "creativity:idle_learning:{hash}"
                        title=title,
                        summary=summary,
                        priority="normal",
                        governance_task_type="self_learning",
                        task_family="self_learning",
                        execution_kind=None,
                        value_tags=["creativity"],
                        candidate_kind="exploratory_learning",
                        score_inputs={
                            "core_value_strength": {
                                "llm": 0.76,
                                "mem_compressed": 0.69,
                                "activity_metadata": 0.64,
                            }.get(topic_source, 0.62),
                            "urgency": self._idle_learning_urgency(
                                active_sessions=active_sessions,
                                topic_source=topic_source,
                                governor_mode=governor_active,
                            ),
                            "novelty": float(topic.get("novelty_score") or 0.6),
                            "specificity": float(topic.get("specificity_score") or 0.55),
                            "execution_readiness": {
                                "llm": 0.84,
                                "mem_compressed": 0.72,
                                "activity_metadata": 0.66,
                            }.get(topic_source, 0.62),
                            "queue_pressure_penalty": self._queue_pressure_penalty(
                                drive_context,
                                governance_task_type="self_learning",
                                task_family="self_learning",
                            ),
                            "repetition_penalty": round(
                                max(0.0, 0.55 - float(topic.get("novelty_score") or 0.6)),
                                4,
                            ),
                            "adaptive_factor": self._adaptive_factor_for_candidate(
                                candidate_kind="exploratory_learning",
                                adaptive_policy=adaptive_policy,
                            ),
                        },
                        metadata={
                            "learning_branch": "exploratory",
                            "self_learning_mode": "no_dependency_exploration",
                            **(
                                {
                                    "drive_judgement": self._intent_metadata(
                                        intent=learning_intent,
                                        needs=needs,
                                        perception=perception,
                                        world_model=world_model,
                                        reflection=reflection,
                                        adaptive_policy=adaptive_policy,
                                    )
                                }
                                if learning_intent is not None
                                else {}
                            ),
                        },
                        evidence={
                            "active_sessions": active_sessions,
                            "trigger": "idle_capacity",
                            "learning_topic": topic["title"],
                            "topic_source": topic_source,
                            "learning_branch": "exploratory",
                            "llm_generated": topic_source == "llm",
                            "novelty_score": topic.get("novelty_score"),
                            "specificity_score": topic.get("specificity_score"),
                        },
                        constraints={
                            "execution_policy": "learn_only",
                            "must_not_modify_active_body": True,
                        },
                    )
                )
                existing_keys.add(topic_key)
                generated_count += 1
                if generated_count >= 2:
                    break

            # Final fallback: completely static topic when even Tier 3 found
            # nothing.  This is the only path that yields a generic task and
            # exists so the creativity candidate is never silently dropped.
            if generated_count == 0 and not had_raw_topics:
                topic_key = baseline_key if shell_worktree else "creativity:idle_learning:fallback"
                if topic_key not in existing_keys:
                    if shell_worktree:
                        candidates.append(
                            self._build_shell_baseline_learning_candidate(
                                stable_key=topic_key,
                                active_sessions=active_sessions,
                                shell_slot_id=shell_slot_id,
                                shell_worktree=shell_worktree,
                                trigger="idle_capacity",
                                drive_context=drive_context,
                                bootstrap=False,
                                adaptive_policy=adaptive_policy,
                            )
                        )
                    else:
                        candidates.append(
                            self._build_scored_candidate(
                                stable_key=topic_key,
                                title="Explore one unresolved learning thread",
                                summary=(
                                    "Use idle capacity to identify one evidence-backed learning direction, "
                                    "gather external references, and record why that thread matters for "
                                    "future self-improvement."
                                ),
                                priority="normal",
                                governance_task_type="self_learning",
                                task_family="self_learning",
                                execution_kind=None,
                                value_tags=["creativity"],
                                candidate_kind="generic_learning_fallback",
                                score_inputs={
                                    "core_value_strength": 0.56,
                                    "urgency": self._idle_learning_urgency(
                                        active_sessions=active_sessions,
                                        topic_source="generic_fallback",
                                        governor_mode=governor_active,
                                    ),
                                    "novelty": 0.2,
                                    "specificity": 0.22,
                                    "execution_readiness": 0.48,
                                    "queue_pressure_penalty": self._queue_pressure_penalty(
                                        drive_context,
                                        governance_task_type="self_learning",
                                        task_family="self_learning",
                                    ),
                                    "repetition_penalty": 0.18,
                                    "adaptive_factor": self._adaptive_factor_for_candidate(
                                        candidate_kind="generic_learning_fallback",
                                        adaptive_policy=adaptive_policy,
                                    ),
                                },
                                metadata={
                                    "learning_branch": "exploratory",
                                    "self_learning_mode": "no_dependency_exploration",
                                    **(
                                        {
                                            "drive_judgement": self._intent_metadata(
                                                intent=learning_intent,
                                                needs=needs,
                                                perception=perception,
                                                world_model=world_model,
                                                reflection=reflection,
                                                adaptive_policy=adaptive_policy,
                                            )
                                        }
                                        if learning_intent is not None
                                        else {}
                                    ),
                                },
                                evidence={
                                    "active_sessions": active_sessions,
                                    "trigger": "idle_capacity",
                                    "learning_topic": "",
                                    "topic_source": "generic_exploration_fallback",
                                    "learning_branch": "exploratory",
                                    "llm_generated": False,
                                },
                                constraints={
                                    "execution_policy": "learn_only",
                                    "must_not_modify_active_body": True,
                                },
                            )
                        )

        if (
            self_evolution_plan.get("eligible_for_planning")
            and "continuity:queue_hygiene_review" not in existing_keys
            and (
                perception.pending_review_count > 0
                or perception.stale_queue_count > 0
                or perception.active_queue_count > 0
            )
        ):
            queue_intent = intents_by_kind.get("queue_hygiene_review")
            candidates.append(
                self._build_scored_candidate(
                    stable_key="continuity:queue_hygiene_review",
                    title="Review self-evolution queue hygiene",
                    summary=(
                        "Check whether planned, deferred, or paused self-evolution work still "
                        "has enough evidence and clear rollback constraints."
                    ),
                    priority="normal",
                    governance_task_type="self_evolution",
                    task_family="general_self_evolution",
                    execution_kind="general_self_evolution",
                    value_tags=["continuity", "truthfulness"],
                    candidate_kind="queue_hygiene_review",
                    score_inputs={
                        "core_value_strength": 0.62,
                        "urgency": self._queue_hygiene_urgency(drive_context),
                        "novelty": 0.38,
                        "specificity": self._clamp01(
                            0.46 + min(int(drive_context.get("active_queue_count") or 0), 4) * 0.05
                        ),
                        "execution_readiness": 0.85,
                        "adaptive_factor": self._adaptive_factor_for_candidate(
                            candidate_kind="queue_hygiene_review",
                            adaptive_policy=adaptive_policy,
                        ),
                    },
                    metadata=(
                        {
                            "drive_judgement": self._intent_metadata(
                                intent=queue_intent,
                                needs=needs,
                                perception=perception,
                                world_model=world_model,
                                reflection=reflection,
                                adaptive_policy=adaptive_policy,
                            )
                        }
                        if queue_intent is not None
                        else None
                    ),
                    evidence={
                        "trigger": "supervisor_queue_governance",
                    },
                    constraints={
                        "must_not_execute_without_review": True,
                    },
                )
            )

        if self_evolution_plan.get("eligible_for_planning"):
            learning_quality = perception.learning_quality
            if (learning_quality >= float(policy.get("body_improvement_min_quality", 60.0) or 60.0)
                and shell_slot_meta
                and not self._has_recent_body_improvement(
                    drive_context,
                    shell_slot_meta=shell_slot_meta,
                    cooldown_hours=int(policy.get("body_improvement_cooldown_hours", 12) or 12),
                )):

                improvement = self._generate_body_improvement_direction(
                    idle_window,
                    learning_quality,
                    shell_slot_meta,
                )
                if improvement:
                    body_intent = intents_by_kind.get("body_improvement")
                    task_key = f"body_improvement:{_stable_key_for_topic(improvement['title'])}"
                    if task_key not in existing_keys:
                        candidates.append(
                            self._build_scored_candidate(
                                stable_key=task_key,
                                title=f"Improve shell body: {improvement['title']}",
                                summary=improvement.get("summary", improvement["title"]),
                                priority="high" if learning_quality >= 80 else "normal",
                                governance_task_type="self_evolution",
                                task_family="body_upgrade",
                                execution_kind="body_improvement",
                                value_tags=["creativity", "continuity"],
                                candidate_kind="body_improvement",
                                score_inputs={
                                    "core_value_strength": 0.86,
                                    "urgency": self._clamp01(
                                        0.42
                                        + max(0.0, learning_quality - 60.0) / 40.0 * 0.4
                                        + (0.12 if improvement.get("source") != "fallback" else 0.0)
                                    ),
                                    "novelty": {
                                        "llm": 0.78,
                                        "history": 0.64,
                                        "git_diff": 0.7,
                                        "fallback": 0.42,
                                    }.get(str(improvement.get("source") or "fallback"), 0.5),
                                    "specificity": (
                                        0.84
                                        if str(improvement.get("diff_summary") or "").strip()
                                        else {
                                            "llm": 0.78,
                                            "history": 0.72,
                                            "git_diff": 0.75,
                                            "fallback": 0.55,
                                        }.get(str(improvement.get("source") or "fallback"), 0.6)
                                    ),
                                    "execution_readiness": self._clamp01(
                                        0.92
                                        - min(len(drive_context.get("queued_learning_titles") or []), 3) * 0.08
                                    ),
                                    "queue_pressure_penalty": self._clamp01(
                                        self._queue_pressure_penalty(
                                            drive_context,
                                            governance_task_type="self_evolution",
                                            task_family="body_upgrade",
                                            execution_kind="body_improvement",
                                        )
                                        + min(len(drive_context.get("queued_learning_titles") or []), 3) * 0.04
                                    ),
                                    "adaptive_factor": self._adaptive_factor_for_candidate(
                                        candidate_kind="body_improvement",
                                        adaptive_policy=adaptive_policy,
                                    ),
                                },
                                metadata=(
                                    {
                                        "drive_judgement": self._intent_metadata(
                                            intent=body_intent,
                                            needs=needs,
                                            perception=perception,
                                            world_model=world_model,
                                            reflection=reflection,
                                            adaptive_policy=adaptive_policy,
                                        )
                                    }
                    if body_intent is not None
                                    else None
                                ),
                                constraints={
                                    "execution_policy": "improve_shell_body",
                                    "target_slot": "shell",
                                    "target_slot_id": shell_slot_meta.get("slot_id"),
                                    "worktree_path": shell_slot_meta.get("worktree_path"),
                                    "must_commit": True,
                                    "evolution_boundary_check": True,
                                    "max_files_changed": int(policy.get("body_improvement_max_files", 5) or 5),
                                    "editable_dirs": list(
                                        policy.get("body_improvement_editable_dirs")
                                        or ["skills/", "tools/", "agent/", "prompts/"]
                                    ),
                                    "forbidden_patterns": list(
                                        policy.get("body_improvement_forbidden_patterns")
                                        or ["**/credential*", "**/.env*", "systems/**"]
                                    ),
                                },
                                evidence={
                                    "learning_quality_score": learning_quality,
                                    "shell_slot_id": shell_slot_meta.get("slot_id"),
                                    "worktree_path": shell_slot_meta.get("worktree_path"),
                                    "git_diff_summary": improvement.get("diff_summary", ""),
                                    "source": improvement.get("source", "fallback"),
                                    "recent_learning_topics": drive_context["recent_learning_titles"][:3],
                                },
                            )
                        )

        if lm_candidates:
            candidates = self._merge_lm_led_candidate_stream(
                lm_candidates=lm_candidates,
                heuristic_candidates=candidates,
                adaptive_policy=adaptive_policy,
            )
        return self._apply_adaptive_candidate_budget(
            candidates,
            adaptive_policy=adaptive_policy,
        )

    def _merge_lm_led_candidate_stream(
        self,
        *,
        lm_candidates: List[EndogenousTaskCandidate],
        heuristic_candidates: List[EndogenousTaskCandidate],
        adaptive_policy: DriveAdaptivePolicy,
    ) -> List[EndogenousTaskCandidate]:
        if not lm_candidates:
            return list(heuristic_candidates or [])
        if not heuristic_candidates:
            return list(lm_candidates or [])

        merged: List[EndogenousTaskCandidate] = list(lm_candidates)
        seen_signatures = {
            self._candidate_semantic_signature(candidate)
            for candidate in lm_candidates
        }
        lm_kinds = {
            self._candidate_kind_of(candidate)
            for candidate in lm_candidates
            if self._candidate_kind_of(candidate)
        }

        complement_budget = 1
        if adaptive_policy.preferred_focus in {"memory_continuity", "queue_hygiene", "truthfulness"}:
            complement_budget = 2

        for candidate in sorted(
            heuristic_candidates,
            key=lambda item: item.utility,
            reverse=True,
        ):
            if complement_budget <= 0:
                break
            signature = self._candidate_semantic_signature(candidate)
            if signature in seen_signatures:
                continue
            candidate_kind = self._candidate_kind_of(candidate)
            if candidate_kind and candidate_kind in lm_kinds:
                continue
            merged.append(candidate)
            seen_signatures.add(signature)
            complement_budget -= 1
        return merged

    def _candidate_semantic_signature(
        self,
        candidate: EndogenousTaskCandidate,
    ) -> str:
        candidate_kind = self._candidate_kind_of(candidate)
        normalized_title = self._normalize_topic_signature(candidate.title)
        task_family = str(candidate.task_family or "").strip().lower()
        governance_task_type = str(candidate.governance_task_type or "").strip().lower()
        return "|".join(
            [
                candidate_kind or "",
                task_family,
                governance_task_type,
                normalized_title,
            ]
        )

    def _normalize_topic_signature(self, text: str) -> str:
        normalized_words = [
            word.lower()
            for word in re.findall(r"[a-zA-Z0-9_]+", str(text or ""))
            if len(word) >= 3
        ]
        if not normalized_words:
            return ""
        return " ".join(normalized_words[:8])

    def _llm_task_proposals(
        self,
        *,
        idle_window: Dict[str, Any],
        existing_keys: set[str],
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        self_evolution_plan: Dict[str, Any],
    ) -> List[EndogenousTaskCandidate]:
        service_runtime = getattr(self.config, "service_runtime", None)
        if service_runtime is None:
            return []
        if not bool(getattr(service_runtime, "endogenous_drive_lm_task_generation_enabled", False)):
            return []

        evidence_packet = self._build_lm_evidence_packet(
            idle_window=idle_window,
            deliberation=deliberation,
            drive_context=drive_context,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            self_evolution_plan=self_evolution_plan,
        )
        proposals = self._generate_lm_task_proposals(evidence_packet=evidence_packet)
        if not proposals:
            return []
        return self._materialize_lm_task_proposals(
            proposals=proposals,
            existing_keys=existing_keys,
            deliberation=deliberation,
            drive_context=drive_context,
            evidence_packet=evidence_packet,
        )

    def _build_lm_evidence_packet(
        self,
        *,
        idle_window: Dict[str, Any],
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        self_evolution_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        cognition_charter = self._resolve_endogenous_cognition_charter(
            getattr(self.config, "service_runtime", None)
        )
        deliberation_dict = deliberation.to_dict()
        perception = deliberation_dict.get("perception", {})
        world_model = deliberation_dict.get("world_model", {})
        reflection = deliberation_dict.get("reflection", {})
        adaptive_policy = deliberation_dict.get("adaptive_policy", {})
        shell_slot = dict(self._get_shell_slot_meta(idle_window) or {})
        recent_learning_evidence = self._build_recent_learning_evidence(drive_context)
        external_research_evidence = self._build_external_research_evidence()
        shell_body_profile = self._build_shell_body_profile(shell_slot)
        evidence_channels = self._build_evidence_channels(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            deliberation_dict=deliberation_dict,
        )
        evidence_graph = dict(evidence_channels.get("evidence_graph") or {})
        agenda_graph = self._build_agenda_graph(
            deliberation_dict=deliberation_dict,
            evidence_graph=evidence_graph,
        )
        recent_reference_alignment = self._build_recent_reference_alignment(drive_context)
        proposal_drift_memory = self._build_proposal_drift_memory(drive_context)
        cognitive_assessment_memory = self._build_cognitive_assessment_memory(drive_context)
        self_iteration_trend_memory = self._build_self_iteration_trend_memory(
            drive_context
        )
        switch_self_regulation_memory = self._build_switch_self_regulation_memory(
            drive_context
        )
        post_task_effect_memory = self._build_post_task_effect_memory(drive_context)
        self_model_snapshot = self._build_self_model_snapshot(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            shell_body_profile=shell_body_profile,
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            recent_reference_alignment=recent_reference_alignment,
            evidence_graph=evidence_graph,
            agenda_graph=agenda_graph,
        )
        evidence_credibility_summary = self._build_evidence_credibility_summary(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            evidence_channels=evidence_channels,
            recent_reference_alignment=recent_reference_alignment,
        )
        task_type_priors = self._build_task_type_priors(
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            self_model_snapshot=self_model_snapshot,
            evidence_credibility_summary=evidence_credibility_summary,
            agenda_graph=agenda_graph,
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
        )
        cognitive_posture = self.resolve_cognitive_posture_state(
            idle_window=idle_window,
            deliberation_dict=deliberation_dict,
        )
        grounding_focus = {
            "primary_evidence_nodes": [
                str(item.get("topic") or "").strip()
                for item in sorted(
                    [
                        dict(row)
                        for row in list(evidence_graph.get("nodes") or [])
                        if isinstance(row, dict) and str(row.get("topic") or "").strip()
                    ],
                    key=lambda row: (
                        -float(row.get("priority") or row.get("avg_confidence") or 0.0),
                        str(row.get("topic") or "").strip(),
                    ),
                )[:3]
                if str(item.get("topic") or "").strip()
            ],
            "primary_agenda_nodes": (
                ([f"focus:{str(agenda_graph.get('focus') or '').strip()}"] if str(agenda_graph.get("focus") or "").strip() else [])
                + [
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
                ]
            )[:3],
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
            "grounding_gaps": [
                *[
                    f"missing_evidence:{str(node).strip()}"
                    for entry in list(recent_reference_alignment.get("recent_entries") or [])[:3]
                    if isinstance(entry, dict)
                    for node in list(entry.get("missing_evidence_nodes") or [])[:2]
                    if str(node).strip()
                ],
                *[
                    f"missing_agenda:{str(node).strip()}"
                    for entry in list(recent_reference_alignment.get("recent_entries") or [])[:3]
                    if isinstance(entry, dict)
                    for node in list(entry.get("missing_agenda_nodes") or [])[:2]
                    if str(node).strip()
                ],
            ][:6],
            "weak_or_missing_channels": [
                str(item).strip()
                for item in list(evidence_credibility_summary.get("weak_or_missing_channels") or [])[:4]
                if str(item).strip()
            ],
        }
        self_iteration_hypotheses = self._build_self_iteration_hypotheses(
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
        meta_cognition_profile = self._build_meta_cognition_profile(
            grounding_focus=grounding_focus,
            self_iteration_hypotheses=self_iteration_hypotheses,
            cognitive_assessment_memory=cognitive_assessment_memory,
            self_iteration_trend_memory=self_iteration_trend_memory,
            switch_self_regulation_memory=switch_self_regulation_memory,
            post_task_effect_memory=post_task_effect_memory,
            proposal_drift_memory=proposal_drift_memory,
            task_type_priors=task_type_priors,
        )
        memory_context = self._fetch_memory_context(deep=True)
        queue_state_snapshot = self._build_queue_state_snapshot(drive_context)
        cognitive_feedback_memory = self._build_cognitive_feedback_memory(drive_context)
        evidence_attention = self._build_evidence_attention_summary(
            cognition_charter=cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory,
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            evidence_graph=evidence_graph,
            agenda_graph=agenda_graph,
            recent_reference_alignment=recent_reference_alignment,
        )
        cognitive_strategy_delta = self._build_cognitive_strategy_delta(
            cognition_charter=cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory,
        )
        cognitive_evolution_draft = self._build_cognitive_evolution_draft(
            cognition_charter=cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory,
            cognitive_strategy_delta=cognitive_strategy_delta,
            meta_cognition_profile=meta_cognition_profile,
            recent_reference_alignment=recent_reference_alignment,
            self_iteration_trend_memory=self_iteration_trend_memory,
            post_task_effect_memory=post_task_effect_memory,
        )
        context_layers = self._build_lm_context_layers(
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
            queue_state_snapshot=queue_state_snapshot,
            evidence_attention=evidence_attention,
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            evidence_channels=evidence_channels,
            memory_context=memory_context,
            recent_learning_titles=list(drive_context.get("recent_learning_titles") or [])[:8],
        )
        return {
            "identity": {
                "role": "endogenous_supervisory_core",
                "goal": "evidence-driven self-iteration under governance constraints",
            },
            "plans": {
                "memory_maintenance": dict(memory_plan),
                "self_learning": dict(self_learning_plan),
                "self_evolution": dict(self_evolution_plan),
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
            "queue_state_snapshot": queue_state_snapshot,
            "cognitive_feedback_memory": cognitive_feedback_memory,
            "evidence_attention": evidence_attention,
            "cognitive_strategy_delta": cognitive_strategy_delta,
            "cognitive_evolution_draft": cognitive_evolution_draft,
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
            "memory_context": memory_context,
            "recent_learning_titles": list(drive_context.get("recent_learning_titles") or [])[:8],
            "recent_learning_evidence": recent_learning_evidence,
            "external_research_evidence": external_research_evidence,
            "queued_learning_titles": list(drive_context.get("queued_learning_titles") or [])[:8],
            "queued_body_improvement_titles": list(drive_context.get("queued_body_improvement_titles") or [])[:8],
            "queued_tasks": list(drive_context.get("queued_tasks") or [])[:12],
            "checks": dict(idle_window.get("checks") or {}),
            "idle_seconds": dict(idle_window.get("idle_seconds") or {}),
            "shell_slot": shell_slot,
            "shell_body_profile": shell_body_profile,
        }

    def _build_queue_state_snapshot(
        self,
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        queued_tasks = [
            dict(item)
            for item in list(drive_context.get("queued_tasks") or [])[:8]
            if isinstance(item, dict)
        ]
        queued_learning_titles = [
            str(item).strip()
            for item in list(drive_context.get("queued_learning_titles") or [])[:5]
            if str(item).strip()
        ]
        queued_body_titles = [
            str(item).strip()
            for item in list(drive_context.get("queued_body_improvement_titles") or [])[:4]
            if str(item).strip()
        ]
        if not queued_tasks and not queued_learning_titles and not queued_body_titles:
            return {}

        recent_titles = [
            str(item.get("title") or "").strip()
            for item in queued_tasks[:4]
            if str(item.get("title") or "").strip()
        ]
        recent_statuses = [
            str(item.get("status") or "").strip()
            for item in queued_tasks[:4]
            if str(item.get("status") or "").strip()
        ]
        return {
            "queued_task_count": len(queued_tasks),
            "queued_learning_count": len(queued_learning_titles),
            "queued_body_improvement_count": len(queued_body_titles),
            "recent_titles": recent_titles,
            "recent_statuses": recent_statuses,
            "summary": (
                f"queued_tasks={len(queued_tasks)}; "
                f"queued_learning={len(queued_learning_titles)}; "
                f"queued_body_improvement={len(queued_body_titles)}; "
                f"recent_titles={', '.join(recent_titles[:3]) or 'none'}."
            ),
            "guidance": (
                "Avoid proposing queued-equivalent work unless stronger evidence clearly justifies replacing it."
            ),
        }

    def _build_lm_context_layers(
        self,
        *,
        cognition_charter: Dict[str, Any],
        cognitive_posture: Dict[str, Any],
        grounding_focus: Dict[str, Any],
        self_iteration_hypotheses: Dict[str, Any],
        meta_cognition_profile: Dict[str, Any],
        self_model_snapshot: Dict[str, Any],
        evidence_credibility_summary: Dict[str, Any],
        task_type_priors: Dict[str, Any],
        cognitive_assessment_memory: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        switch_self_regulation_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        queue_state_snapshot: Dict[str, Any],
        evidence_attention: Dict[str, Any],
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        evidence_channels: Dict[str, Any],
        memory_context: str,
        recent_learning_titles: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        layering_policy = self._resolve_cognitive_context_layering_policy(
            cognition_charter
        )
        decision_core = {
            "current_judgement": str(
                meta_cognition_profile.get("current_judgement")
                or (
                    list(cognitive_assessment_memory.get("common_current_judgements") or [None])[0]
                    if cognitive_assessment_memory.get("common_current_judgements")
                    else ""
                )
                or ""
            ).strip(),
            "dominant_constraint": str(
                meta_cognition_profile.get("dominant_constraint")
                or cognitive_assessment_memory.get("dominant_constraint")
                or ""
            ).strip(),
            "grounding_pressure": str(
                meta_cognition_profile.get("grounding_pressure") or ""
            ).strip(),
            "governance_posture": str(
                meta_cognition_profile.get("governance_posture")
                or meta_cognition_profile.get("recommended_task_posture")
                or task_type_priors.get("top_priority_task_type")
                or ""
            ).strip(),
            "compatible_projection_bias": str(
                meta_cognition_profile.get("compatible_projection_bias")
                or task_type_priors.get("top_priority_task_type")
                or ""
            ).strip(),
            "compatible_projection_score": round(
                self._clamp01(task_type_priors.get("top_priority_score") or 0.0),
                4,
            ),
            "top_self_iteration_domain": str(
                meta_cognition_profile.get("top_self_iteration_domain")
                or self_iteration_hypotheses.get("top_target_domain")
                or ""
            ).strip(),
            "top_self_iteration_hypothesis": str(
                meta_cognition_profile.get("top_self_iteration_hypothesis")
                or self_iteration_hypotheses.get("dominant_hypothesis")
                or ""
            ).strip(),
            "primary_evidence_nodes": [
                str(item).strip()
                for item in list(
                    evidence_attention.get("decision_core_topics")
                    or grounding_focus.get("primary_evidence_nodes")
                    or []
                )[:3]
                if str(item).strip()
            ],
            "primary_agenda_nodes": [
                str(item).strip()
                for item in list(
                    evidence_attention.get("decision_core_agenda_nodes")
                    or grounding_focus.get("primary_agenda_nodes")
                    or []
                )[:3]
                if str(item).strip()
            ],
            "queue_state_summary": str(queue_state_snapshot.get("summary") or "").strip(),
            "cognitive_posture": {
                "name": str(cognitive_posture.get("name") or "").strip(),
                "selection_reason": str(
                    cognitive_posture.get("selection_reason") or ""
                ).strip(),
            },
            "summary": (
                "Decision core: "
                f"judgement={str(meta_cognition_profile.get('current_judgement') or 'unknown').strip() or 'unknown'}; "
                f"constraint={str(meta_cognition_profile.get('dominant_constraint') or 'unknown').strip() or 'unknown'}; "
                f"governance_posture={str(meta_cognition_profile.get('governance_posture') or meta_cognition_profile.get('recommended_task_posture') or 'unknown').strip() or 'unknown'}; "
                f"projection_bias={str(task_type_priors.get('top_priority_task_type') or 'unknown').strip() or 'unknown'}; "
                f"self_iteration_domain={str(meta_cognition_profile.get('top_self_iteration_domain') or 'unknown').strip() or 'unknown'}."
            ),
        }

        readiness = dict(self_model_snapshot.get("readiness") or {})
        supporting_detail = {
            "grounding_gaps": [
                str(item).strip()
                for item in list(
                    evidence_attention.get("supporting_topics")
                    or grounding_focus.get("grounding_gaps")
                    or []
                )[:4]
                if str(item).strip()
            ],
            "contradictory_topics": [
                str(item).strip()
                for item in list(grounding_focus.get("contradictory_topics") or [])[:3]
                if str(item).strip()
            ],
            "weak_or_missing_channels": [
                str(item).strip()
                for item in list(
                    evidence_credibility_summary.get("weak_or_missing_channels") or []
                )[:4]
                if str(item).strip()
            ],
            "self_understanding_gaps": [
                str(item).strip()
                for item in list(self_model_snapshot.get("self_understanding_gaps") or [])[:4]
                if str(item).strip()
            ],
            "why_not_improvement_now": [
                str(item).strip()
                for item in list(
                    cognitive_assessment_memory.get("common_why_not_improvement_now") or []
                )[:4]
                if str(item).strip()
            ],
            "trend_state": str(self_iteration_trend_memory.get("trend_state") or "").strip(),
            "stay_or_switch_bias": str(
                meta_cognition_profile.get("stay_or_switch_bias")
                or switch_self_regulation_memory.get("preferred_switch_bias")
                or ""
            ).strip(),
            "recent_effect_direction": str(
                meta_cognition_profile.get("recent_effect_direction")
                or post_task_effect_memory.get("effect_direction")
                or ""
            ).strip(),
            "reference_alignment_score": round(
                self._clamp01(
                    recent_reference_alignment.get("average_alignment_score") or 0.0
                ),
                4,
            ),
            "self_iteration_readiness_score": round(
                self._clamp01(readiness.get("self_iteration_readiness_score") or 0.0),
                4,
            ),
            "summary": (
                "Supporting detail: "
                f"grounding_gaps={len(list(grounding_focus.get('grounding_gaps') or []))}; "
                f"weak_channels={len(list(evidence_credibility_summary.get('weak_or_missing_channels') or []))}; "
                f"trend_state={str(self_iteration_trend_memory.get('trend_state') or 'unknown').strip() or 'unknown'}."
            ),
        }

        channel_rows = [
            {
                "channel": str(item.get("channel") or "").strip(),
                "evidence_strength": str(item.get("evidence_strength") or "").strip(),
                "item_count": max(0, int(item.get("item_count") or 0)),
            }
            for item in list(evidence_channels.get("channels") or [])[:4]
            if isinstance(item, dict) and str(item.get("channel") or "").strip()
        ]
        long_tail_context = {
            "recent_learning_titles": [
                str(item).strip()
                for item in list(recent_learning_titles or [])[:5]
                if str(item).strip()
            ],
            "recent_learning_evidence": [
                {
                    "title": str(item.get("title") or "").strip(),
                    "quality_score": item.get("quality_score"),
                }
                for item in list(
                    evidence_attention.get("long_tail_learning_items")
                    or recent_learning_evidence
                    or []
                )[:2]
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ],
            "external_research_titles": [
                str(item.get("title") or "").strip()
                for item in list(
                    evidence_attention.get("long_tail_research_items")
                    or external_research_evidence
                    or []
                )[:3]
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ],
            "evidence_channels": channel_rows,
            "memory_context_preview": str(memory_context or "").strip()[:220],
            "summary": (
                "Long-tail context: "
                f"learning_titles={len(list(recent_learning_titles or []))}; "
                f"research_entries={len(list(external_research_evidence or []))}; "
                f"channels={len(channel_rows)}."
            ),
        }
        layer_sources = {
            "current_judgement": decision_core.get("current_judgement"),
            "dominant_constraint": decision_core.get("dominant_constraint"),
            "grounding_pressure": decision_core.get("grounding_pressure"),
            "governance_posture": decision_core.get("governance_posture"),
            "compatible_projection_bias": decision_core.get("compatible_projection_bias"),
            "compatible_projection_score": decision_core.get("compatible_projection_score"),
            "top_self_iteration_domain": decision_core.get("top_self_iteration_domain"),
            "top_self_iteration_hypothesis": decision_core.get("top_self_iteration_hypothesis"),
            "primary_evidence_nodes": decision_core.get("primary_evidence_nodes"),
            "primary_agenda_nodes": decision_core.get("primary_agenda_nodes"),
            "queue_state_summary": decision_core.get("queue_state_summary"),
            "cognitive_posture": decision_core.get("cognitive_posture"),
            "decision_summary": decision_core.get("summary"),
            "grounding_gaps": supporting_detail.get("grounding_gaps"),
            "contradictory_topics": supporting_detail.get("contradictory_topics"),
            "weak_or_missing_channels": supporting_detail.get("weak_or_missing_channels"),
            "self_understanding_gaps": supporting_detail.get("self_understanding_gaps"),
            "why_not_improvement_now": supporting_detail.get("why_not_improvement_now"),
            "trend_state": supporting_detail.get("trend_state"),
            "stay_or_switch_bias": supporting_detail.get("stay_or_switch_bias"),
            "recent_effect_direction": supporting_detail.get("recent_effect_direction"),
            "reference_alignment_score": supporting_detail.get("reference_alignment_score"),
            "self_iteration_readiness_score": supporting_detail.get("self_iteration_readiness_score"),
            "supporting_summary": supporting_detail.get("summary"),
            "recent_learning_titles": long_tail_context.get("recent_learning_titles"),
            "recent_learning_evidence": long_tail_context.get("recent_learning_evidence"),
            "external_research_titles": long_tail_context.get("external_research_titles"),
            "evidence_channels": long_tail_context.get("evidence_channels"),
            "memory_context_preview": long_tail_context.get("memory_context_preview"),
            "long_tail_summary": long_tail_context.get("summary"),
        }
        return {
            "decision_core": self._select_context_layer_fields(
                layer_sources,
                layering_policy.get("decision_core_fields") or [],
                summary_alias="decision_summary",
                summary_output_key="summary",
            ),
            "supporting_detail": self._select_context_layer_fields(
                layer_sources,
                layering_policy.get("supporting_detail_fields") or [],
                summary_alias="supporting_summary",
                summary_output_key="summary",
            ),
            "long_tail_context": self._select_context_layer_fields(
                layer_sources,
                layering_policy.get("long_tail_context_fields") or [],
                summary_alias="long_tail_summary",
                summary_output_key="summary",
            ),
        }

    def _build_evidence_attention_summary(
        self,
        *,
        cognition_charter: Dict[str, Any],
        cognitive_feedback_memory: Dict[str, Any],
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        shell_body_profile: Dict[str, Any],
        evidence_graph: Dict[str, Any],
        agenda_graph: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        policy = self._resolve_evidence_attention_policy(
            cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory,
        )
        if not bool(policy.get("enabled", True)):
            return {}

        ranked_topics = self._rank_evidence_attention_topics(
            policy=policy,
            evidence_graph=evidence_graph,
            agenda_graph=agenda_graph,
            recent_reference_alignment=recent_reference_alignment,
        )
        ranked_agenda_nodes = self._rank_evidence_attention_agenda_nodes(
            policy=policy,
            agenda_graph=agenda_graph,
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            recent_reference_alignment=recent_reference_alignment,
        )
        ranked_learning_items = self._rank_evidence_attention_items(
            policy=policy,
            items=recent_learning_evidence,
            agenda_graph=agenda_graph,
            recent_reference_alignment=recent_reference_alignment,
            channel_name="recent_learning",
        )
        ranked_research_items = self._rank_evidence_attention_items(
            policy=policy,
            items=external_research_evidence,
            agenda_graph=agenda_graph,
            recent_reference_alignment=recent_reference_alignment,
            channel_name="external_research",
        )
        if shell_body_profile:
            body_items = self._rank_evidence_attention_items(
                policy=policy,
                items=[shell_body_profile],
                agenda_graph=agenda_graph,
                recent_reference_alignment=recent_reference_alignment,
                channel_name="shell_body_profile",
            )
        else:
            body_items = []
        return {
            "decision_core_topics": [
                str(item.get("topic") or "").strip()
                for item in ranked_topics[: max(1, int(policy.get("decision_core_topic_limit") or 3))]
                if str(item.get("topic") or "").strip()
            ],
            "decision_core_agenda_nodes": [
                str(item.get("agenda_node") or "").strip()
                for item in ranked_agenda_nodes[: max(1, int(policy.get("decision_core_topic_limit") or 3))]
                if str(item.get("agenda_node") or "").strip()
            ],
            "supporting_topics": [
                str(item.get("topic") or "").strip()
                for item in ranked_topics[
                    max(0, int(policy.get("decision_core_topic_limit") or 3)) :
                    max(
                        0,
                        int(policy.get("decision_core_topic_limit") or 3)
                        + int(policy.get("supporting_item_limit") or 4),
                    )
                ]
                if str(item.get("topic") or "").strip()
            ],
            "long_tail_learning_items": [
                dict(item)
                for item in ranked_learning_items[: max(1, int(policy.get("long_tail_item_limit") or 3))]
                if isinstance(item, dict)
            ],
            "long_tail_research_items": [
                dict(item)
                for item in ranked_research_items[: max(1, int(policy.get("long_tail_item_limit") or 3))]
                if isinstance(item, dict)
            ],
            "body_attention_items": [
                dict(item)
                for item in body_items[:1]
                if isinstance(item, dict)
            ],
            "top_topic_scores": [
                {
                    "topic": str(item.get("topic") or "").strip(),
                    "attention_score": round(
                        self._clamp01(float(item.get("attention_score") or 0.0)),
                        4,
                    ),
                }
                for item in ranked_topics[:4]
                if str(item.get("topic") or "").strip()
            ],
        }

    def _resolve_evidence_attention_policy(
        self,
        cognition_charter: Dict[str, Any],
        *,
        cognitive_feedback_memory: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        raw_policy = dict(cognition_charter.get("evidence_attention_policy") or {})
        resolved = {
            "enabled": bool(raw_policy.get("enabled", True)),
            "confidence_weight": float(raw_policy.get("confidence_weight") or 0.3),
            "novelty_weight": float(raw_policy.get("novelty_weight") or 0.08),
            "freshness_weight": float(raw_policy.get("freshness_weight") or 0.14),
            "agenda_relevance_weight": float(raw_policy.get("agenda_relevance_weight") or 0.24),
            "conflict_weight": float(raw_policy.get("conflict_weight") or 0.14),
            "self_relevance_weight": float(raw_policy.get("self_relevance_weight") or 0.1),
            "decision_core_topic_limit": max(1, int(raw_policy.get("decision_core_topic_limit") or 3)),
            "supporting_item_limit": max(1, int(raw_policy.get("supporting_item_limit") or 4)),
            "long_tail_item_limit": max(1, int(raw_policy.get("long_tail_item_limit") or 3)),
        }
        return self._apply_cognitive_feedback_to_evidence_attention_policy(
            resolved,
            cognition_charter=cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory or {},
        )

    def _apply_cognitive_feedback_to_evidence_attention_policy(
        self,
        policy: Dict[str, Any],
        *,
        cognition_charter: Dict[str, Any],
        cognitive_feedback_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        feedback_policy = self._resolve_cognitive_feedback_policy(cognition_charter)
        if not bool(feedback_policy.get("enabled", True)):
            return policy
        if not bool(cognitive_feedback_memory.get("available")):
            return policy

        adjusted = dict(policy)
        adaptation_strength = self._clamp01(
            float(feedback_policy.get("adaptation_strength") or 0.22)
        )
        if cognitive_feedback_memory.get("reference_feedback_direction") == "weak":
            adjusted["conflict_weight"] = self._clamp01(
                float(adjusted.get("conflict_weight") or 0.0)
                + float(feedback_policy.get("conflict_weight_step") or 0.0) * adaptation_strength
            )
            adjusted["agenda_relevance_weight"] = self._clamp01(
                float(adjusted.get("agenda_relevance_weight") or 0.0)
                + float(feedback_policy.get("agenda_relevance_weight_step") or 0.0) * adaptation_strength
            )
        if cognitive_feedback_memory.get("freshness_feedback_direction") == "weak":
            adjusted["freshness_weight"] = self._clamp01(
                float(adjusted.get("freshness_weight") or 0.0)
                + float(feedback_policy.get("freshness_weight_step") or 0.0) * adaptation_strength
            )
        if cognitive_feedback_memory.get("confidence_feedback_direction") == "weak":
            adjusted["confidence_weight"] = self._clamp01(
                float(adjusted.get("confidence_weight") or 0.0)
                + float(feedback_policy.get("confidence_weight_step") or 0.0) * adaptation_strength
            )
        if cognitive_feedback_memory.get("self_relevance_feedback_direction") == "strong":
            adjusted["self_relevance_weight"] = self._clamp01(
                float(adjusted.get("self_relevance_weight") or 0.0)
                + float(feedback_policy.get("self_relevance_weight_step") or 0.0) * adaptation_strength
            )
        if cognitive_feedback_memory.get("long_tail_signal_bias") == "compress":
            adjusted["long_tail_item_limit"] = max(
                1,
                int(adjusted.get("long_tail_item_limit") or 3) - 1,
            )
        return adjusted

    def _resolve_cognitive_feedback_policy(
        self,
        cognition_charter: Dict[str, Any],
    ) -> Dict[str, Any]:
        raw_policy = dict(cognition_charter.get("cognitive_feedback_policy") or {})
        return {
            "enabled": bool(raw_policy.get("enabled", True)),
            "adaptation_strength": float(raw_policy.get("adaptation_strength") or 0.22),
            "confidence_weight_step": float(raw_policy.get("confidence_weight_step") or 0.08),
            "freshness_weight_step": float(raw_policy.get("freshness_weight_step") or 0.06),
            "agenda_relevance_weight_step": float(raw_policy.get("agenda_relevance_weight_step") or 0.1),
            "conflict_weight_step": float(raw_policy.get("conflict_weight_step") or 0.08),
            "self_relevance_weight_step": float(raw_policy.get("self_relevance_weight_step") or 0.06),
        }

    def _build_cognitive_feedback_memory(
        self,
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        if not outcomes:
            return {
                "available": False,
                "summary": "No cognitive feedback memory is available yet.",
            }

        quality_scores: List[float] = []
        reference_scores: List[float] = []
        cognitive_scores: List[float] = []
        freshness_scores: List[float] = []
        self_relevance_scores: List[float] = []

        for outcome in outcomes[:12]:
            quality_scores.append(self._clamp01(float(outcome.get("quality_score") or 0.0)))
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            reference_alignment = outcome.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")
            reference_scores.append(
                self._clamp01((reference_alignment or {}).get("alignment_score") or 0.0)
            )
            cognitive_alignment = outcome.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            cognitive_scores.append(
                self._clamp01((cognitive_alignment or {}).get("score") or 0.0)
            )
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            normalized = self._normalize_lm_cognitive_assessment(assessment)
            freshness_scores.append(
                1.0
                if list(normalized.get("primary_grounding_gaps") or [])
                else 0.4
            )
            self_relevance_scores.append(
                1.0
                if str(normalized.get("self_iteration_target") or "").strip() in {"grounding", "self_model"}
                else 0.35
            )

        def _avg(values: List[float]) -> float:
            if not values:
                return 0.0
            return self._clamp01(sum(values) / len(values))

        average_quality = _avg(quality_scores)
        average_reference = _avg(reference_scores)
        average_cognitive = _avg(cognitive_scores)
        average_freshness_signal = _avg(freshness_scores)
        average_self_relevance_signal = _avg(self_relevance_scores)

        return {
            "available": True,
            "average_quality_score": round(average_quality, 4),
            "average_reference_alignment_score": round(average_reference, 4),
            "average_cognitive_alignment_score": round(average_cognitive, 4),
            "average_freshness_signal": round(average_freshness_signal, 4),
            "average_self_relevance_signal": round(average_self_relevance_signal, 4),
            "confidence_feedback_direction": "weak" if average_quality < 0.55 else "strong",
            "reference_feedback_direction": "weak" if average_reference < 0.58 else "strong",
            "freshness_feedback_direction": "weak" if average_freshness_signal < 0.55 else "strong",
            "self_relevance_feedback_direction": "strong" if average_self_relevance_signal >= 0.6 else "weak",
            "long_tail_signal_bias": "compress" if average_reference < 0.5 and average_quality < 0.55 else "keep",
            "summary": (
                "Recent cognitive outcomes suggest "
                f"quality={average_quality:.2f}, reference={average_reference:.2f}, "
                f"cognitive={average_cognitive:.2f}."
            ),
        }

    def _build_cognitive_strategy_delta(
        self,
        *,
        cognition_charter: Dict[str, Any],
        cognitive_feedback_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        delta_policy = self._resolve_cognitive_strategy_delta_policy(cognition_charter)
        if not bool(delta_policy.get("enabled", True)):
            return {
                "available": False,
                "summary": "Cognitive strategy delta suggestions are disabled.",
            }
        if not bool(cognitive_feedback_memory.get("available")):
            return {
                "available": False,
                "summary": "No cognitive feedback memory is available to propose strategy deltas.",
            }

        base_policy = self._resolve_evidence_attention_policy(
            cognition_charter,
            cognitive_feedback_memory={},
        )
        adjusted_policy = self._resolve_evidence_attention_policy(
            cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory,
        )
        proposal_threshold = self._clamp01(
            float(delta_policy.get("proposal_threshold") or 0.015)
        )
        max_changes = max(
            1,
            int(delta_policy.get("max_recommended_changes") or 6),
        )
        change_rows: List[Dict[str, Any]] = []
        for key in (
            "confidence_weight",
            "freshness_weight",
            "agenda_relevance_weight",
            "conflict_weight",
            "self_relevance_weight",
        ):
            before = float(base_policy.get(key) or 0.0)
            after = float(adjusted_policy.get(key) or 0.0)
            delta_value = round(after - before, 4)
            if abs(delta_value) < proposal_threshold:
                continue
            direction = "increase" if delta_value > 0 else "decrease"
            reason = self._strategy_delta_reason_for_key(
                key=key,
                cognitive_feedback_memory=cognitive_feedback_memory,
            )
            change_rows.append(
                {
                    "target": f"evidence_attention_policy.{key}",
                    "direction": direction,
                    "current_value": round(before, 4),
                    "suggested_value": round(after, 4),
                    "delta": delta_value,
                    "reason": reason,
                }
            )
        for key in ("long_tail_item_limit",):
            before = int(base_policy.get(key) or 0)
            after = int(adjusted_policy.get(key) or 0)
            delta_value = after - before
            if delta_value == 0:
                continue
            direction = "increase" if delta_value > 0 else "decrease"
            reason = self._strategy_delta_reason_for_key(
                key=key,
                cognitive_feedback_memory=cognitive_feedback_memory,
            )
            change_rows.append(
                {
                    "target": f"evidence_attention_policy.{key}",
                    "direction": direction,
                    "current_value": before,
                    "suggested_value": after,
                    "delta": delta_value,
                    "reason": reason,
                }
            )
        change_rows = sorted(
            change_rows,
            key=lambda item: abs(float(item.get("delta") or 0.0)),
            reverse=True,
        )[:max_changes]
        if not change_rows:
            return {
                "available": False,
                "summary": "Recent cognitive feedback does not yet justify a strategy delta proposal.",
            }
        return {
            "available": True,
            "recommended_changes": change_rows,
            "summary": (
                "Recent cognitive feedback suggests adjusting "
                f"{len(change_rows)} evidence-attention parameters."
            ),
        }

    def _build_cognitive_evolution_draft(
        self,
        *,
        cognition_charter: Dict[str, Any],
        cognitive_feedback_memory: Dict[str, Any],
        cognitive_strategy_delta: Dict[str, Any],
        meta_cognition_profile: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        mission_pressure = self._cognitive_evolution_mission_pressure(
            cognition_charter=cognition_charter,
            cognitive_feedback_memory=cognitive_feedback_memory,
            meta_cognition_profile=meta_cognition_profile,
            self_iteration_trend_memory=self_iteration_trend_memory,
            post_task_effect_memory=post_task_effect_memory,
        )
        attention_policy_delta = self._build_attention_policy_delta(
            cognitive_strategy_delta=cognitive_strategy_delta,
        )
        charter_delta = self._build_charter_delta(
            cognitive_feedback_memory=cognitive_feedback_memory,
            meta_cognition_profile=meta_cognition_profile,
            recent_reference_alignment=recent_reference_alignment,
            self_iteration_trend_memory=self_iteration_trend_memory,
            post_task_effect_memory=post_task_effect_memory,
        )
        evidence_basis = self._build_cognitive_evolution_evidence_basis(
            cognitive_feedback_memory=cognitive_feedback_memory,
            recent_reference_alignment=recent_reference_alignment,
            self_iteration_trend_memory=self_iteration_trend_memory,
            post_task_effect_memory=post_task_effect_memory,
            meta_cognition_profile=meta_cognition_profile,
        )
        available = bool(attention_policy_delta.get("available")) or bool(
            charter_delta.get("available")
        )
        if not available:
            return {
                "available": False,
                "mission_pressure": mission_pressure,
                "attention_policy_delta": attention_policy_delta,
                "charter_delta": charter_delta,
                "evidence_basis": evidence_basis,
                "summary": "Current cognitive feedback is not yet strong enough to justify an evolution draft.",
            }
        attention_count = len(
            list(attention_policy_delta.get("recommended_changes") or [])
        )
        charter_count = len(list(charter_delta.get("recommended_changes") or []))
        return {
            "available": True,
            "mission_pressure": mission_pressure,
            "attention_policy_delta": attention_policy_delta,
            "charter_delta": charter_delta,
            "evidence_basis": evidence_basis,
            "summary": (
                "Cognitive evolution draft proposes "
                f"{attention_count} attention-policy adjustments and "
                f"{charter_count} charter-level adjustments."
            ),
        }

    def _build_attention_policy_delta(
        self,
        *,
        cognitive_strategy_delta: Dict[str, Any],
    ) -> Dict[str, Any]:
        recommended_changes = [
            dict(item)
            for item in list(cognitive_strategy_delta.get("recommended_changes") or [])
            if isinstance(item, dict) and str(item.get("target") or "").strip()
        ]
        if not recommended_changes:
            return {
                "available": False,
                "recommended_changes": [],
                "summary": "No attention-policy adjustments are currently recommended.",
            }
        return {
            "available": True,
            "recommended_changes": recommended_changes[:6],
            "summary": str(cognitive_strategy_delta.get("summary") or "").strip(),
        }

    def _build_charter_delta(
        self,
        *,
        cognitive_feedback_memory: Dict[str, Any],
        meta_cognition_profile: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        recommended_changes: List[Dict[str, Any]] = []
        if (
            str(cognitive_feedback_memory.get("reference_feedback_direction") or "").strip()
            == "weak"
            or float(recent_reference_alignment.get("average_alignment_score") or 0.0) < 0.58
        ):
            recommended_changes.append(
                {
                    "target": "prompt_output_requirements",
                    "direction": "strengthen",
                    "priority": "high",
                    "reason": "reference alignment remains weak, so proposals should bind evidence and agenda nodes more explicitly.",
                    "suggested_additions": [
                        "提案必须明确列出关键 evidence / agenda 绑定关系，避免引用漂移。",
                        "当 reference alignment 偏弱时，优先输出 review / observation / learning，而不是跳到 improvement。",
                    ],
                }
            )
        if (
            str(meta_cognition_profile.get("dominant_failure_mode") or "").strip()
            in {"grounding_instability", "weak self structure grounding"}
            or str(meta_cognition_profile.get("top_self_iteration_domain") or "").strip()
            == "grounding"
        ):
            recommended_changes.append(
                {
                    "target": "task_generation_focus",
                    "direction": "strengthen",
                    "priority": "high",
                    "reason": "grounding remains the dominant failure mode, so task generation should keep prioritizing evidence repair before aggressive self-iteration.",
                    "suggested_additions": [
                        "当 grounding gap 未闭合时，优先提出修复证据链、核对引用和补足自身理解的任务。",
                        "只有在 grounding 压力明显下降后，才提高 improvement 类任务优先级。",
                    ],
                }
            )
        if (
            str(post_task_effect_memory.get("effect_direction") or "").strip() == "mixed"
            or str(self_iteration_trend_memory.get("trend_state") or "").strip()
            in {"locked", "stalled"}
        ):
            recommended_changes.append(
                {
                    "target": "self_iteration_guardrails",
                    "direction": "clarify",
                    "priority": "medium",
                    "reason": "recent self-iteration effects are mixed, so the charter should better distinguish when to keep iterating and when to pause for evidence repair.",
                    "suggested_additions": [
                        "当近期自我迭代结果呈 mixed 或 stalled 时，应优先追加诊断型任务而不是连续放大同一路径。",
                        "若收益方向不稳定，应允许显式返回空 proposals 或低风险 review 任务。",
                    ],
                }
            )
        if not recommended_changes:
            return {
                "available": False,
                "recommended_changes": [],
                "summary": "No charter-level adjustments are currently recommended.",
            }
        return {
            "available": True,
            "recommended_changes": recommended_changes[:4],
            "summary": (
                "Recent cognition suggests clarifying charter focus, output requirements, "
                "or self-iteration guardrails."
            ),
        }

    def _build_cognitive_evolution_evidence_basis(
        self,
        *,
        cognitive_feedback_memory: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
        meta_cognition_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "reference_alignment_score": round(
                self._clamp01(
                    float(recent_reference_alignment.get("average_alignment_score") or 0.0)
                ),
                4,
            ),
            "cognitive_alignment_score": round(
                self._clamp01(
                    float(
                        cognitive_feedback_memory.get("average_cognitive_alignment_score")
                        or 0.0
                    )
                ),
                4,
            ),
            "quality_score": round(
                self._clamp01(
                    float(cognitive_feedback_memory.get("average_quality_score") or 0.0)
                ),
                4,
            ),
            "trend_state": str(self_iteration_trend_memory.get("trend_state") or "").strip(),
            "effect_direction": str(post_task_effect_memory.get("effect_direction") or "").strip(),
            "dominant_failure_mode": str(
                meta_cognition_profile.get("dominant_failure_mode") or ""
            ).strip(),
        }

    def _cognitive_evolution_mission_pressure(
        self,
        *,
        cognition_charter: Dict[str, Any],
        cognitive_feedback_memory: Dict[str, Any],
        meta_cognition_profile: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        core_mission = str(cognition_charter.get("core_mission") or "").strip()
        if not core_mission:
            core_mission = "Maintain evidence-grounded self-iteration under governance constraints."
        return {
            "core_mission": core_mission,
            "dominant_failure_mode": str(
                meta_cognition_profile.get("dominant_failure_mode") or ""
            ).strip(),
            "top_self_iteration_domain": str(
                meta_cognition_profile.get("top_self_iteration_domain") or ""
            ).strip(),
            "reference_feedback_direction": str(
                cognitive_feedback_memory.get("reference_feedback_direction") or ""
            ).strip(),
            "confidence_feedback_direction": str(
                cognitive_feedback_memory.get("confidence_feedback_direction") or ""
            ).strip(),
            "trend_state": str(self_iteration_trend_memory.get("trend_state") or "").strip(),
            "effect_direction": str(post_task_effect_memory.get("effect_direction") or "").strip(),
            "summary": (
                "Mission pressure currently centers on "
                f"{str(meta_cognition_profile.get('top_self_iteration_domain') or 'unknown').strip() or 'unknown'} "
                "under ongoing evidence and outcome feedback."
            ),
        }

    def _resolve_cognitive_strategy_delta_policy(
        self,
        cognition_charter: Dict[str, Any],
    ) -> Dict[str, Any]:
        raw_policy = dict(cognition_charter.get("cognitive_strategy_delta_policy") or {})
        return {
            "enabled": bool(raw_policy.get("enabled", True)),
            "proposal_threshold": float(raw_policy.get("proposal_threshold") or 0.015),
            "max_recommended_changes": max(
                1,
                int(raw_policy.get("max_recommended_changes") or 6),
            ),
        }

    def _strategy_delta_reason_for_key(
        self,
        *,
        key: str,
        cognitive_feedback_memory: Dict[str, Any],
    ) -> str:
        if key == "conflict_weight":
            return (
                "reference alignment remains weak, so conflict-sensitive evidence should be weighted more heavily."
            )
        if key == "agenda_relevance_weight":
            return (
                "recent outcomes suggest agenda binding should be emphasized more strongly."
            )
        if key == "freshness_weight":
            return (
                "recent feedback suggests stale or weakly refreshed evidence is hurting cognition quality."
            )
        if key == "confidence_weight":
            return (
                "recent outcomes suggest evidence confidence should play a stronger role in attention allocation."
            )
        if key == "self_relevance_weight":
            return (
                "recent self-iteration outcomes suggest self-referential evidence is especially informative."
            )
        if key == "long_tail_item_limit":
            bias = str(cognitive_feedback_memory.get("long_tail_signal_bias") or "").strip()
            if bias == "compress":
                return "recent weak outcomes suggest compressing long-tail context to reduce distraction."
            return "recent outcomes suggest expanding long-tail context to capture more weak signals."
        return "recent cognitive feedback suggests this parameter should be adjusted."

    def _rank_evidence_attention_topics(
        self,
        *,
        policy: Dict[str, Any],
        evidence_graph: Dict[str, Any],
        agenda_graph: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        agenda_nodes = {
            str(item.get("gap") or "").strip()
            for item in list(agenda_graph.get("unresolved_gaps") or [])[:8]
            if isinstance(item, dict) and str(item.get("gap") or "").strip()
        }
        focus = str(agenda_graph.get("focus") or "").strip()
        if focus:
            agenda_nodes.add(focus)
        missing_nodes = {
            str(node).strip()
            for entry in list(recent_reference_alignment.get("recent_entries") or [])[:4]
            if isinstance(entry, dict)
            for node in list(entry.get("missing_evidence_nodes") or [])[:3]
            if str(node).strip()
        }
        ranked: List[Dict[str, Any]] = []
        for row in list(evidence_graph.get("nodes") or [])[:16]:
            if not isinstance(row, dict):
                continue
            topic = str(row.get("topic") or "").strip()
            if not topic:
                continue
            confidence = self._clamp01(float(row.get("avg_confidence") or 0.0))
            novelty = self._clamp01(0.2 + min(len(topic.split("_")), 4) * 0.1)
            agenda_relevance = 1.0 if topic in agenda_nodes else 0.3 if "self" in topic else 0.0
            conflict_signal = self._clamp01(float(row.get("contradict_count") or 0.0) * 0.35)
            freshness = 1.0 if topic in missing_nodes else 0.45
            self_relevance = 1.0 if topic.startswith("self_") or topic in {"self_structure", "body_state"} else 0.0
            score = (
                confidence * float(policy.get("confidence_weight") or 0.0)
                + novelty * float(policy.get("novelty_weight") or 0.0)
                + freshness * float(policy.get("freshness_weight") or 0.0)
                + agenda_relevance * float(policy.get("agenda_relevance_weight") or 0.0)
                + conflict_signal * float(policy.get("conflict_weight") or 0.0)
                + self_relevance * float(policy.get("self_relevance_weight") or 0.0)
            )
            ranked.append(
                {
                    **dict(row),
                    "attention_score": round(self._clamp01(score), 4),
                }
            )
        ranked.sort(
            key=lambda item: (
                -float(item.get("attention_score") or 0.0),
                -float(item.get("avg_confidence") or 0.0),
                str(item.get("topic") or "").strip(),
            )
        )
        return ranked

    def _rank_evidence_attention_agenda_nodes(
        self,
        *,
        policy: Dict[str, Any],
        agenda_graph: Dict[str, Any],
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        shell_body_profile: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        evidence_texts = [
            " ".join(
                [
                    str(item.get("title") or item.get("slot_id") or "").strip(),
                    str(item.get("summary") or "").strip(),
                    " ".join(str(v).strip() for v in list(item.get("evidence_summary") or []) if str(v).strip()),
                ]
            ).lower()
            for item in (
                list(recent_learning_evidence or [])[:6]
                + list(external_research_evidence or [])[:8]
                + ([shell_body_profile] if shell_body_profile else [])
            )
            if isinstance(item, dict)
        ]
        reference_terms = {
            str(node).strip().lower()
            for entry in list(recent_reference_alignment.get("recent_entries") or [])[:4]
            if isinstance(entry, dict)
            for node in (
                list(entry.get("missing_evidence_nodes") or [])[:3]
                + list(entry.get("missing_agenda_nodes") or [])[:3]
            )
            if str(node).strip()
        }

        candidates: List[Dict[str, Any]] = []
        focus = str(agenda_graph.get("focus") or "").strip()
        if focus:
            candidates.append(
                {
                    "agenda_node": f"focus:{focus}",
                    "priority": float(agenda_graph.get("focus_confidence") or 0.0),
                    "source": "focus",
                }
            )
        for item in list(agenda_graph.get("unresolved_gaps") or [])[:8]:
            if not isinstance(item, dict):
                continue
            gap = str(item.get("gap") or "").strip()
            if gap:
                candidates.append(
                    {
                        "agenda_node": gap,
                        "priority": float(item.get("priority") or 0.0),
                        "source": "gap",
                    }
                )
        for item in list(agenda_graph.get("recommended_directions") or [])[:8]:
            if not isinstance(item, dict):
                continue
            direction = str(item.get("direction") or "").strip()
            if direction:
                candidates.append(
                    {
                        "agenda_node": direction,
                        "priority": float(item.get("priority") or 0.0),
                        "source": "direction",
                    }
                )

        synthetic_nodes = set()
        for text in evidence_texts:
            for token in ("focus:learning_expansion", "focus:truthfulness", "focus:memory_continuity"):
                if token in text and token not in synthetic_nodes:
                    synthetic_nodes.add(token)
                    candidates.append(
                        {
                            "agenda_node": token,
                            "priority": 0.74,
                            "source": "evidence_focus",
                        }
                    )

        ranked: List[Dict[str, Any]] = []
        for item in candidates:
            node = str(item.get("agenda_node") or "").strip()
            if not node:
                continue
            node_text = node.lower()
            evidence_match = 1.0 if any(node_text in text for text in evidence_texts) else 0.0
            token_match = 0.0
            node_tokens = [
                token
                for token in re.findall(r"[a-zA-Z0-9_]+", node_text)
                if len(token) >= 4
            ]
            if node_tokens and any(any(token in text for token in node_tokens) for text in evidence_texts):
                token_match = 0.65
            reference_match = 1.0 if node_text in reference_terms else 0.0
            priority = self._clamp01(float(item.get("priority") or 0.0))
            score = self._clamp01(
                priority * float(policy.get("agenda_relevance_weight") or 0.0)
                + evidence_match * float(policy.get("confidence_weight") or 0.0)
                + token_match * float(policy.get("freshness_weight") or 0.0)
                + reference_match * float(policy.get("conflict_weight") or 0.0)
            )
            ranked.append(
                {
                    **dict(item),
                    "attention_score": round(score, 4),
                }
            )
        ranked.sort(
            key=lambda item: (
                -float(item.get("attention_score") or 0.0),
                -float(item.get("priority") or 0.0),
                str(item.get("agenda_node") or "").strip(),
            )
        )
        return ranked

    def _rank_evidence_attention_items(
        self,
        *,
        policy: Dict[str, Any],
        items: List[Dict[str, Any]],
        agenda_graph: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        channel_name: str,
    ) -> List[Dict[str, Any]]:
        focus = str(agenda_graph.get("focus") or "").strip().lower()
        agenda_terms = {
            focus,
            *{
                str(item.get("gap") or "").strip().lower()
                for item in list(agenda_graph.get("unresolved_gaps") or [])[:8]
                if isinstance(item, dict) and str(item.get("gap") or "").strip()
            },
        }
        missing_terms = {
            str(node).strip().lower()
            for entry in list(recent_reference_alignment.get("recent_entries") or [])[:4]
            if isinstance(entry, dict)
            for node in (
                list(entry.get("missing_evidence_nodes") or [])[:3]
                + list(entry.get("missing_agenda_nodes") or [])[:3]
            )
            if str(node).strip()
        }
        ranked: List[Dict[str, Any]] = []
        for item in items[:16]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("slot_id") or "").strip()
            summary = str(item.get("summary") or "").strip()
            text = f"{title} {summary}".lower()
            confidence = self._clamp01(
                float(item.get("confidence_score") or item.get("source_reliability") or 0.0)
            )
            novelty = self._clamp01(float(item.get("novelty_score") or 0.0))
            freshness = self._evidence_item_freshness_score(item)
            agenda_relevance = 1.0 if any(term and term in text for term in agenda_terms) else 0.0
            conflict_signal = 1.0 if any(term and term in text for term in missing_terms) else 0.0
            self_relevance = 1.0 if channel_name in {"recent_learning", "shell_body_profile"} else 0.0
            score = (
                confidence * float(policy.get("confidence_weight") or 0.0)
                + novelty * float(policy.get("novelty_weight") or 0.0)
                + freshness * float(policy.get("freshness_weight") or 0.0)
                + agenda_relevance * float(policy.get("agenda_relevance_weight") or 0.0)
                + conflict_signal * float(policy.get("conflict_weight") or 0.0)
                + self_relevance * float(policy.get("self_relevance_weight") or 0.0)
            )
            ranked.append(
                {
                    **dict(item),
                    "attention_score": round(self._clamp01(score), 4),
                }
            )
        ranked.sort(
            key=lambda item: (
                -float(item.get("attention_score") or 0.0),
                -float(item.get("confidence_score") or item.get("source_reliability") or 0.0),
                str(item.get("title") or item.get("slot_id") or "").strip(),
            )
        )
        return ranked

    def _evidence_item_freshness_score(self, item: Dict[str, Any]) -> float:
        timestamp = item.get("published_at") or item.get("completed_at")
        parsed = self._parse_timestamp(timestamp)
        if parsed is None:
            return 0.35
        age_days = max(0, (datetime.now(timezone.utc) - parsed).days)
        if age_days <= 14:
            return 1.0
        if age_days <= 90:
            return 0.7
        return 0.3

    def _resolve_cognitive_context_layering_policy(
        self,
        cognition_charter: Dict[str, Any],
    ) -> Dict[str, List[str]]:
        default_policy = {
            "decision_core_fields": [
                "current_judgement",
                "dominant_constraint",
                "grounding_pressure",
                "governance_posture",
                "compatible_projection_bias",
                "compatible_projection_score",
                "top_self_iteration_domain",
                "top_self_iteration_hypothesis",
                "primary_evidence_nodes",
                "primary_agenda_nodes",
                "queue_state_summary",
                "cognitive_posture",
                "decision_summary",
            ],
            "supporting_detail_fields": [
                "grounding_gaps",
                "contradictory_topics",
                "weak_or_missing_channels",
                "self_understanding_gaps",
                "why_not_improvement_now",
                "trend_state",
                "stay_or_switch_bias",
                "recent_effect_direction",
                "reference_alignment_score",
                "self_iteration_readiness_score",
                "supporting_summary",
            ],
            "long_tail_context_fields": [
                "recent_learning_titles",
                "recent_learning_evidence",
                "external_research_titles",
                "evidence_channels",
                "memory_context_preview",
                "long_tail_summary",
            ],
        }
        raw_policy = dict(cognition_charter.get("context_layering_policy") or {})
        resolved: Dict[str, List[str]] = {}
        for key, fallback in default_policy.items():
            items = [
                str(item).strip()
                for item in list(raw_policy.get(key) or [])
                if str(item).strip()
            ]
            resolved[key] = items or list(fallback)
        return resolved

    def _select_context_layer_fields(
        self,
        layer_sources: Dict[str, Any],
        field_names: List[str],
        *,
        summary_alias: str,
        summary_output_key: str,
    ) -> Dict[str, Any]:
        layer: Dict[str, Any] = {}
        for field_name in field_names:
            source_key = str(field_name or "").strip()
            if not source_key:
                continue
            if source_key == summary_alias:
                value = layer_sources.get(summary_alias)
                if value not in ("", [], {}, None):
                    layer[summary_output_key] = value
                continue
            if source_key not in layer_sources:
                continue
            value = layer_sources.get(source_key)
            if value in ("", None):
                continue
            layer[source_key] = value
        return layer

    def _generate_lm_task_proposals(
        self,
        *,
        evidence_packet: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        role = "governance_reasoner"
        runtime_config = getattr(self.config, "service_runtime", None)
        if runtime_config is not None:
            role = str(
                getattr(runtime_config, "endogenous_drive_lm_task_model_role", "governance_reasoner")
                or "governance_reasoner"
            )
        cognition_charter = self._resolve_endogenous_cognition_charter(runtime_config)
        max_candidates = max(
            0,
            int(getattr(runtime_config, "endogenous_drive_lm_task_max_candidates", 3) or 3),
        )
        core_mission = str(cognition_charter.get("core_mission") or "").strip()
        if not core_mission or max_candidates <= 0:
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="disabled",
                proposal_count=0,
                raw_candidate_kinds=[],
                error=(
                    "missing_core_mission"
                    if not core_mission
                    else "max_candidates_disabled"
                ),
            )
            return []
        try:
            from memai.model_config import resolve_mem_llm_client

            llm_client, _ = resolve_mem_llm_client(role=role)
            if llm_client is None:
                self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                    evidence_packet=evidence_packet,
                    cognition_charter=cognition_charter,
                    role=role,
                    max_candidates=max_candidates,
                    status="llm_unavailable",
                    proposal_count=0,
                    raw_candidate_kinds=[],
                    error="llm_client_unavailable",
                )
                return []
        except Exception as exc:
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="llm_unavailable",
                proposal_count=0,
                raw_candidate_kinds=[],
                error=str(exc),
            )
            return []

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
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="generation_error",
                proposal_count=0,
                raw_candidate_kinds=[],
                error=str(exc),
            )
            return []
        if not isinstance(result, dict):
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="invalid_response",
                proposal_count=0,
                raw_candidate_kinds=[],
                error="non_dict_response",
            )
            return []
        cognitive_assessment = self._normalize_lm_cognitive_assessment(
            result.get("cognitive_assessment")
        )
        proposals = result.get("proposals")
        if not isinstance(proposals, list):
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="invalid_response",
                proposal_count=0,
                raw_candidate_kinds=[],
                cognitive_assessment=cognitive_assessment,
                error="missing_proposals_list",
            )
            return []
        normalized_proposals = [dict(item) for item in proposals if isinstance(item, dict)]
        self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
            evidence_packet=evidence_packet,
            cognition_charter=cognition_charter,
            role=role,
            max_candidates=max_candidates,
            status="completed",
            proposal_count=len(normalized_proposals),
            raw_candidate_kinds=[
                str(item.get("candidate_kind") or "").strip()
                for item in normalized_proposals
                if str(item.get("candidate_kind") or "").strip()
            ][:8],
            cognitive_assessment=cognitive_assessment,
        )
        return normalized_proposals

    def _normalize_lm_cognitive_assessment(
        self,
        assessment: Any,
    ) -> Dict[str, Any]:
        if not isinstance(assessment, dict):
            return {}
        current_judgement = str(assessment.get("current_judgement") or "").strip()
        dominant_constraint = str(assessment.get("dominant_constraint") or "").strip()
        primary_grounding_gaps = [
            str(item).strip()
            for item in list(assessment.get("primary_grounding_gaps") or [])[:6]
            if str(item).strip()
        ]
        why_this_task_type_now = [
            str(item).strip()
            for item in list(assessment.get("why_this_task_type_now") or [])[:6]
            if str(item).strip()
        ]
        why_not_improvement_now = [
            str(item).strip()
            for item in list(assessment.get("why_not_improvement_now") or [])[:6]
            if str(item).strip()
        ]
        self_iteration_target = str(assessment.get("self_iteration_target") or "").strip()
        self_iteration_hypothesis = str(
            assessment.get("self_iteration_hypothesis") or ""
        ).strip()
        stay_or_switch = str(assessment.get("stay_or_switch") or "").strip().lower()
        if stay_or_switch not in {"stay", "switch"}:
            stay_or_switch = ""
        switch_reason = str(assessment.get("switch_reason") or "").strip()
        normalized = {
            "current_judgement": current_judgement,
            "dominant_constraint": dominant_constraint,
            "primary_grounding_gaps": primary_grounding_gaps,
            "why_this_task_type_now": why_this_task_type_now,
            "why_not_improvement_now": why_not_improvement_now,
            "self_iteration_target": self_iteration_target,
            "self_iteration_hypothesis": self_iteration_hypothesis,
            "stay_or_switch": stay_or_switch,
            "switch_reason": switch_reason,
        }
        return {
            key: value
            for key, value in normalized.items()
            if value not in ("", []) and value is not None
        }

    def _resolve_endogenous_cognition_charter(
        self,
        runtime_config: Any,
    ) -> Dict[str, Any]:
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        if hasattr(charter_model, "model_dump"):
            cognition_charter = charter_model.model_dump(mode="json")
        else:
            cognition_charter = dict(charter_model or {})
        if not str(cognition_charter.get("core_mission") or "").strip():
            cognition_charter["core_mission"] = str(
                getattr(runtime_config, "endogenous_drive_core_mission_prompt", "") or ""
            ).strip()
        if not list(cognition_charter.get("task_generation_policy") or []):
            cognition_charter["task_generation_policy"] = list(
                getattr(runtime_config, "endogenous_drive_task_generation_principles", []) or []
            )
        if not list(cognition_charter.get("task_generation_focus") or []):
            cognition_charter["task_generation_focus"] = [
                "先综合主证据主题、主议程主题、grounding 缺口和近期认知记忆，再判断当前最该做什么。",
                "把 cognitive_assessment 当作真实认知中间层，而不是装饰性说明。",
                "当存在自我迭代目标时，优先解释当前最值得迭代的缺陷域，以及为什么现在处理它。",
            ]
        if not list(cognition_charter.get("prompt_output_requirements") or []):
            cognition_charter["prompt_output_requirements"] = [
                "提案必须显式绑定 evidence graph / agenda graph 节点，避免漂浮任务。",
                "提案必须说明为什么现在做、为什么不是别的任务类型、为什么执行模式匹配当前风险。",
                "如果证据不足或冲突明显，应允许返回空 proposals，而不是硬凑任务。",
            ]
        context_layering_policy = dict(cognition_charter.get("context_layering_policy") or {})
        if not list(context_layering_policy.get("decision_core_fields") or []):
            context_layering_policy["decision_core_fields"] = [
                "current_judgement",
                "dominant_constraint",
                "grounding_pressure",
                "governance_posture",
                "compatible_projection_bias",
                "compatible_projection_score",
                "top_self_iteration_domain",
                "top_self_iteration_hypothesis",
                "primary_evidence_nodes",
                "primary_agenda_nodes",
                "queue_state_summary",
                "cognitive_posture",
                "decision_summary",
            ]
        if not list(context_layering_policy.get("supporting_detail_fields") or []):
            context_layering_policy["supporting_detail_fields"] = [
                "grounding_gaps",
                "contradictory_topics",
                "weak_or_missing_channels",
                "self_understanding_gaps",
                "why_not_improvement_now",
                "trend_state",
                "stay_or_switch_bias",
                "recent_effect_direction",
                "reference_alignment_score",
                "self_iteration_readiness_score",
                "supporting_summary",
            ]
        if not list(context_layering_policy.get("long_tail_context_fields") or []):
            context_layering_policy["long_tail_context_fields"] = [
                "recent_learning_titles",
                "recent_learning_evidence",
                "external_research_titles",
                "evidence_channels",
                "memory_context_preview",
                "long_tail_summary",
            ]
        cognition_charter["context_layering_policy"] = context_layering_policy
        prompt_attention_policy = dict(cognition_charter.get("prompt_attention_policy") or {})
        if not int(prompt_attention_policy.get("max_chars") or 0):
            prompt_attention_policy["max_chars"] = 11500
        if not list(prompt_attention_policy.get("priority_order") or []):
            prompt_attention_policy["priority_order"] = [
                "identity",
                "decision_core",
                "supporting_detail",
                "long_tail_context",
                "queue_state_snapshot",
                "perception",
                "world_model",
                "reflection",
                "adaptive_policy",
                "meta_cognition_profile",
                "cognitive_posture",
                "grounding_focus",
                "self_iteration_hypotheses",
                "self_iteration_trend_memory",
                "switch_self_regulation_memory",
                "post_task_effect_memory",
                "self_model_snapshot",
                "agenda_graph",
                "evidence_credibility_summary",
                "cognitive_assessment_memory",
                "proposal_drift_memory",
                "task_type_priors",
                "evidence_channels",
                "recent_learning_evidence",
                "external_research_evidence",
                "shell_body_profile",
                "research_digest",
                "recent_reference_alignment",
                "evidence_graph",
                "needs",
                "intents",
                "signals",
                "recent_learning_titles",
                "checks",
                "idle_seconds",
                "plans",
                "queued_learning_titles",
                "queued_body_improvement_titles",
                "queued_tasks",
                "shell_slot",
                "memory_context",
            ]
        if not list(prompt_attention_policy.get("structure_keys") or []):
            prompt_attention_policy["structure_keys"] = [
                "decision_core",
                "supporting_detail",
                "long_tail_context",
                "queue_state_snapshot",
            ]
        if not list(prompt_attention_policy.get("trim_stage_order") or []):
            prompt_attention_policy["trim_stage_order"] = [
                "primary_context_compaction",
                "graph_compaction",
                "grounding_focus_compaction",
                "evidence_tail_compaction",
                "activity_tail_compaction",
            ]
        cognition_charter["prompt_attention_policy"] = prompt_attention_policy
        evidence_attention_policy = dict(cognition_charter.get("evidence_attention_policy") or {})
        if "enabled" not in evidence_attention_policy:
            evidence_attention_policy["enabled"] = True
        for key, fallback in (
            ("confidence_weight", 0.3),
            ("novelty_weight", 0.08),
            ("freshness_weight", 0.14),
            ("agenda_relevance_weight", 0.24),
            ("conflict_weight", 0.14),
            ("self_relevance_weight", 0.1),
        ):
            if evidence_attention_policy.get(key) is None:
                evidence_attention_policy[key] = fallback
        for key, fallback in (
            ("decision_core_topic_limit", 3),
            ("supporting_item_limit", 4),
            ("long_tail_item_limit", 3),
        ):
            if not int(evidence_attention_policy.get(key) or 0):
                evidence_attention_policy[key] = fallback
        cognition_charter["evidence_attention_policy"] = evidence_attention_policy
        cognitive_feedback_policy = dict(cognition_charter.get("cognitive_feedback_policy") or {})
        if "enabled" not in cognitive_feedback_policy:
            cognitive_feedback_policy["enabled"] = True
        for key, fallback in (
            ("adaptation_strength", 0.22),
            ("confidence_weight_step", 0.08),
            ("freshness_weight_step", 0.06),
            ("agenda_relevance_weight_step", 0.1),
            ("conflict_weight_step", 0.08),
            ("self_relevance_weight_step", 0.06),
        ):
            if cognitive_feedback_policy.get(key) is None:
                cognitive_feedback_policy[key] = fallback
        cognition_charter["cognitive_feedback_policy"] = cognitive_feedback_policy
        cognitive_strategy_delta_policy = dict(
            cognition_charter.get("cognitive_strategy_delta_policy") or {}
        )
        if "enabled" not in cognitive_strategy_delta_policy:
            cognitive_strategy_delta_policy["enabled"] = True
        if cognitive_strategy_delta_policy.get("proposal_threshold") is None:
            cognitive_strategy_delta_policy["proposal_threshold"] = 0.015
        if not int(cognitive_strategy_delta_policy.get("max_recommended_changes") or 0):
            cognitive_strategy_delta_policy["max_recommended_changes"] = 6
        cognition_charter["cognitive_strategy_delta_policy"] = (
            cognitive_strategy_delta_policy
        )
        return cognition_charter

    def _resolve_cognitive_posture_from_policy(
        self,
        *,
        policy: Dict[str, Any],
        deliberation_dict: Dict[str, Any],
        self_model_snapshot: Dict[str, Any],
        evidence_credibility_summary: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        profiles = dict(policy.get("posture_profiles") or {})
        selection_mode = str(policy.get("posture_selection_mode") or "auto").strip().lower()
        manual_profile = str(policy.get("active_posture_profile") or "balanced").strip().lower()
        profile_name = manual_profile or "balanced"
        selection_reason = "manual_selection"

        perception = dict(deliberation_dict.get("perception") or {})
        reflection = dict(deliberation_dict.get("reflection") or {})
        recent_cognitive_alignment = self._build_recent_cognitive_alignment_summary(
            drive_context.get("drive_history") or {},
        )
        weak_channels = [
            str(item).strip()
            for item in list(evidence_credibility_summary.get("weak_or_missing_channels") or [])[:6]
            if str(item).strip()
        ]
        self_gaps = [
            str(item).strip()
            for item in list(self_model_snapshot.get("self_understanding_gaps") or [])[:6]
            if str(item).strip()
        ]
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        correction_signals = max(0, int(perception.get("correction_signals") or 0))
        active_sessions = max(0, int(perception.get("active_sessions") or 0))
        readiness_score = self._clamp01(
            (self_model_snapshot.get("readiness") or {}).get("self_iteration_readiness_score") or 0.0
        )
        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        posture_alignment_health = str(
            proposal_drift_memory.get("posture_alignment_health") or ""
        ).strip().lower()
        priority_basis_health = str(
            proposal_drift_memory.get("priority_basis_health") or ""
        ).strip().lower()
        missing_posture_alignment_count = max(
            0,
            int(proposal_drift_memory.get("missing_posture_alignment_count") or 0),
        )
        missing_priority_basis_count = max(
            0,
            int(proposal_drift_memory.get("missing_priority_basis_count") or 0),
        )
        dominant_posture_conflict_reason = str(
            proposal_drift_memory.get("dominant_posture_conflict_reason") or ""
        ).strip().lower()
        alignment_average_score = self._clamp01(
            recent_cognitive_alignment.get("average_score") or 0.0
        )
        dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()

        if selection_mode != "manual":
            service_threshold = max(
                0,
                int(policy.get("auto_service_active_sessions_threshold") or 1),
            )
            truthfulness_threshold = max(
                1,
                int(policy.get("auto_truthfulness_correction_signal_threshold") or 3),
            )
            evidence_threshold = max(
                1,
                int(policy.get("auto_evidence_repair_signal_threshold") or 3),
            )
            explanation_missing_threshold = max(
                1,
                int(policy.get("auto_explanation_repair_missing_threshold") or 2),
            )
            explanation_inconsistent_threshold = max(
                1,
                int(policy.get("auto_explanation_repair_inconsistent_threshold") or 1),
            )
            explanation_missing_pressure = max(
                missing_posture_alignment_count,
                missing_priority_basis_count,
            )
            explanation_inconsistent_pressure = 0
            if posture_alignment_health == "inconsistent":
                explanation_inconsistent_pressure += 1
            if priority_basis_health == "inconsistent":
                explanation_inconsistent_pressure += 1
            if active_sessions >= service_threshold:
                profile_name = "conservative"
                selection_reason = "service_pressure_requires_conservative_posture"
            elif correction_signals >= truthfulness_threshold:
                profile_name = "truthfulness_first"
                selection_reason = "truthfulness_signals_are_elevated"
            elif (
                explanation_missing_pressure >= explanation_missing_threshold
                and explanation_inconsistent_pressure >= explanation_inconsistent_threshold
            ):
                profile_name = "evidence_repair_first"
                selection_reason = "explanation_quality_requires_evidence_repair"
            elif explanation_missing_pressure >= explanation_missing_threshold:
                profile_name = "observe_first"
                selection_reason = "missing_explanation_memory_requires_observation"
            elif explanation_inconsistent_pressure >= explanation_inconsistent_threshold:
                if "truthfulness" in dominant_posture_conflict_reason or "reference_alignment" in dominant_posture_conflict_reason:
                    profile_name = "truthfulness_first"
                    selection_reason = "explanation_conflict_requires_truthfulness_repair"
                else:
                    profile_name = "observe_first"
                    selection_reason = "explanation_conflict_requires_observation"
            elif (
                weak_reference_count >= evidence_threshold
                or len(weak_channels) >= evidence_threshold
                or "reference_alignment_is_unstable" in self_gaps
            ):
                profile_name = "evidence_repair_first"
                selection_reason = "evidence_repair_pressure_is_elevated"
            elif (
                drift_state in {"drifting", "correcting"}
                or alignment_average_score < self._clamp01(
                    policy.get("drift_observe_trigger_score") or 0.5
                )
                or readiness_score < self._clamp01(
                    policy.get("readiness_min_score") or 0.52
                )
                or dominant_constraint in {"queue_blockage", "historical_underdelivery"}
            ):
                profile_name = "observe_first"
                selection_reason = "drift_or_readiness_requires_observation"
            else:
                profile_name = "balanced"
                selection_reason = "balanced_posture_is_sufficient"

        selected = profiles.get(profile_name)
        if not isinstance(selected, dict):
            profile_name = "balanced"
            selected = profiles.get(profile_name) or {}
        return {
            "name": profile_name,
            "selection_mode": selection_mode,
            "selection_reason": selection_reason,
            "summary": str(selected.get("summary") or "").strip(),
            "observation_multiplier": round(
                self._clamp01(selected.get("observation_multiplier") or 1.0),
                4,
            ),
            "throttle_multiplier": round(
                self._clamp01(selected.get("throttle_multiplier") or 1.0),
                4,
            ),
            "truthfulness_multiplier": round(
                self._clamp01(selected.get("truthfulness_multiplier") or 1.0),
                4,
            ),
            "learning_suppression_multiplier": round(
                self._clamp01(selected.get("learning_suppression_multiplier") or 1.0),
                4,
            ),
        }

    def _build_recent_cognitive_alignment_summary(
        self,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_history = dict(history_snapshot or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        entries: List[Dict[str, Any]] = []
        quality_counts = {"strong": 0, "partial": 0, "weak": 0}
        for outcome in outcomes[:12]:
            cognitive_alignment = outcome.get("cognitive_alignment")
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict) or not cognitive_alignment:
                continue
            quality = str(cognitive_alignment.get("quality") or "partial").strip().lower()
            if quality not in quality_counts:
                quality = "partial"
            quality_counts[quality] += 1
            entries.append(
                {
                    "score": float(cognitive_alignment.get("score") or 0.0),
                    "quality": quality,
                }
            )
            if len(entries) >= 4:
                break
        if not entries:
            return {
                "available": False,
                "average_score": 0.0,
                "quality_counts": {},
            }
        average_score = sum(item["score"] for item in entries) / len(entries)
        return {
            "available": True,
            "average_score": round(self._clamp01(average_score), 4),
            "quality_counts": quality_counts,
        }

    def _build_lm_task_generation_context_snapshot(
        self,
        *,
        evidence_packet: Dict[str, Any],
        cognition_charter: Dict[str, Any],
        role: str,
        max_candidates: int,
        status: str,
        proposal_count: int,
        raw_candidate_kinds: List[str],
        cognitive_assessment: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        self_model_snapshot = dict(evidence_packet.get("self_model_snapshot") or {})
        readiness = dict(self_model_snapshot.get("readiness") or {})
        evidence_credibility_summary = dict(
            evidence_packet.get("evidence_credibility_summary") or {}
        )
        task_type_priors = dict(evidence_packet.get("task_type_priors") or {})
        self_iteration_hypotheses = dict(
            evidence_packet.get("self_iteration_hypotheses") or {}
        )
        meta_cognition_profile = dict(evidence_packet.get("meta_cognition_profile") or {})
        proposal_drift_memory = dict(evidence_packet.get("proposal_drift_memory") or {})
        cognitive_assessment_memory = dict(
            evidence_packet.get("cognitive_assessment_memory") or {}
        )
        self_iteration_trend_memory = dict(
            evidence_packet.get("self_iteration_trend_memory") or {}
        )
        switch_self_regulation_memory = dict(
            evidence_packet.get("switch_self_regulation_memory") or {}
        )
        post_task_effect_memory = dict(
            evidence_packet.get("post_task_effect_memory") or {}
        )
        recent_reference_alignment = dict(
            evidence_packet.get("recent_reference_alignment") or {}
        )
        cognitive_posture = dict(evidence_packet.get("cognitive_posture") or {})
        evidence_channels = [
            {
                "channel": str(channel.get("channel") or "").strip(),
                "kind": str(channel.get("kind") or "").strip(),
                "confidence": round(self._clamp01(channel.get("confidence") or 0.0), 4),
                "evidence_strength": str(channel.get("evidence_strength") or "").strip(),
                "item_count": max(0, int(channel.get("item_count") or 0)),
            }
            for channel in list((evidence_packet.get("evidence_channels") or {}).get("channels") or [])[:6]
            if isinstance(channel, dict) and str(channel.get("channel") or "").strip()
        ]
        prior_rows = [
            {
                "task_type": str(item.get("task_type") or "").strip(),
                "score": round(self._clamp01(item.get("score") or 0.0), 4),
                "reasons": [
                    str(reason).strip()
                    for reason in list(item.get("reasons") or [])[:3]
                    if str(reason).strip()
                ],
            }
            for item in list(task_type_priors.get("priors") or [])[:5]
            if isinstance(item, dict) and str(item.get("task_type") or "").strip()
        ]
        top_priority_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
        top_priority_score = round(
            self._clamp01(task_type_priors.get("top_priority_score") or 0.0),
            4,
        )
        summary = (
            f"LM cognition status={status}; strongest compatible execution shape if action is justified="
            f"{top_priority_task_type or 'unknown'} ({top_priority_score:.2f}); "
            f"proposal drift={str(proposal_drift_memory.get('drift_state') or 'unknown').strip() or 'unknown'}."
        )
        if error:
            summary += f" Error={error}."
        return {
            "status": status,
            "model_role": role,
            "max_candidates": max(0, int(max_candidates)),
            "proposal_count": max(0, int(proposal_count)),
            "raw_candidate_kinds": list(raw_candidate_kinds or [])[:8],
            "cognitive_assessment": dict(cognitive_assessment or {}),
            "error": error,
            "charter": {
                "core_mission": str(cognition_charter.get("core_mission") or "").strip(),
                "self_model_principles": [
                    str(item).strip()
                    for item in list(cognition_charter.get("self_model_principles") or [])[:8]
                    if str(item).strip()
                ],
                "evidence_policy": [
                    str(item).strip()
                    for item in list(cognition_charter.get("evidence_policy") or [])[:8]
                    if str(item).strip()
                ],
                "task_generation_policy": [
                    str(item).strip()
                    for item in list(cognition_charter.get("task_generation_policy") or [])[:8]
                    if str(item).strip()
                ],
                "self_iteration_guardrails": [
                    str(item).strip()
                    for item in list(cognition_charter.get("self_iteration_guardrails") or [])[:8]
                    if str(item).strip()
                ],
            },
            "task_type_priors": {
                "top_priority_task_type": top_priority_task_type,
                "top_priority_score": top_priority_score,
                "priors": prior_rows,
            },
            "meta_cognition_profile": {
                "available": bool(meta_cognition_profile.get("available")),
                "current_judgement": str(
                    meta_cognition_profile.get("current_judgement") or ""
                ).strip(),
                "dominant_constraint": str(
                    meta_cognition_profile.get("dominant_constraint") or ""
                ).strip(),
                "grounding_pressure": str(
                    meta_cognition_profile.get("grounding_pressure") or ""
                ).strip(),
                "top_self_iteration_domain": str(
                    meta_cognition_profile.get("top_self_iteration_domain") or ""
                ).strip(),
                "top_self_iteration_hypothesis": str(
                    meta_cognition_profile.get("top_self_iteration_hypothesis") or ""
                ).strip(),
                "stay_or_switch_bias": str(
                    meta_cognition_profile.get("stay_or_switch_bias") or ""
                ).strip(),
                "switch_bias_effectiveness": str(
                    meta_cognition_profile.get("switch_bias_effectiveness") or ""
                ).strip(),
                "recent_effect_direction": str(
                    meta_cognition_profile.get("recent_effect_direction") or ""
                ).strip(),
                "dominant_failure_mode": str(
                    meta_cognition_profile.get("dominant_failure_mode") or ""
                ).strip(),
                "governance_posture": str(
                    meta_cognition_profile.get("governance_posture")
                    or meta_cognition_profile.get("recommended_task_posture")
                    or ""
                ).strip(),
                "compatible_projection_bias": str(
                    meta_cognition_profile.get("compatible_projection_bias") or ""
                ).strip(),
                "priority_signals": [
                    str(item).strip()
                    for item in list(meta_cognition_profile.get("priority_signals") or [])[:6]
                    if str(item).strip()
                ],
            },
            "self_iteration_hypotheses": {
                "available": bool(self_iteration_hypotheses.get("available")),
                "dominant_hypothesis": str(
                    self_iteration_hypotheses.get("dominant_hypothesis") or ""
                ).strip(),
                "top_target_domain": str(
                    self_iteration_hypotheses.get("top_target_domain") or ""
                ).strip(),
                "hypotheses": [
                    {
                        "target_domain": str(item.get("target_domain") or "").strip(),
                        "hypothesis": str(item.get("hypothesis") or "").strip(),
                        "priority": round(
                            self._clamp01(item.get("priority") or 0.0),
                            4,
                        ),
                        "suggested_task_types": [
                            str(row).strip()
                            for row in list(item.get("suggested_task_types") or [])[:3]
                            if str(row).strip()
                        ],
                    }
                    for item in list(self_iteration_hypotheses.get("hypotheses") or [])[:3]
                    if isinstance(item, dict) and str(item.get("hypothesis") or "").strip()
                ],
            },
            "self_iteration_trend_memory": {
                "available": bool(self_iteration_trend_memory.get("available")),
                "dominant_target": str(
                    self_iteration_trend_memory.get("dominant_target") or ""
                ).strip(),
                "trend_state": str(
                    self_iteration_trend_memory.get("trend_state") or ""
                ).strip(),
                "target_stability": str(
                    self_iteration_trend_memory.get("target_stability") or ""
                ).strip(),
                "common_targets": [
                    str(item).strip()
                    for item in list(
                        self_iteration_trend_memory.get("common_targets") or []
                    )[:4]
                    if str(item).strip()
                ],
                "common_hypotheses": [
                    str(item).strip()
                    for item in list(
                        self_iteration_trend_memory.get("common_hypotheses") or []
                    )[:4]
                    if str(item).strip()
                ],
                "common_stay_or_switch": [
                    str(item).strip()
                    for item in list(
                        self_iteration_trend_memory.get("common_stay_or_switch") or []
                    )[:2]
                    if str(item).strip()
                ],
                "common_switch_reasons": [
                    str(item).strip()
                    for item in list(
                        self_iteration_trend_memory.get("common_switch_reasons") or []
                    )[:4]
                    if str(item).strip()
                ],
            },
            "switch_self_regulation_memory": {
                "available": bool(switch_self_regulation_memory.get("available")),
                "preferred_switch_bias": str(
                    switch_self_regulation_memory.get("preferred_switch_bias") or ""
                ).strip(),
                "switch_effectiveness": str(
                    switch_self_regulation_memory.get("switch_effectiveness") or ""
                ).strip(),
                "stay_effectiveness": str(
                    switch_self_regulation_memory.get("stay_effectiveness") or ""
                ).strip(),
                "average_switch_quality": round(
                    self._clamp01(
                        switch_self_regulation_memory.get("average_switch_quality") or 0.0
                    ),
                    4,
                ),
                "average_stay_quality": round(
                    self._clamp01(
                        switch_self_regulation_memory.get("average_stay_quality") or 0.0
                    ),
                    4,
                ),
            },
            "post_task_effect_memory": {
                "available": bool(post_task_effect_memory.get("available")),
                "effect_direction": str(
                    post_task_effect_memory.get("effect_direction") or ""
                ).strip(),
                "average_quality_score": round(
                    self._clamp01(post_task_effect_memory.get("average_quality_score") or 0.0),
                    4,
                ),
                "average_cognitive_alignment_score": round(
                    self._clamp01(
                        post_task_effect_memory.get("average_cognitive_alignment_score") or 0.0
                    ),
                    4,
                ),
                "average_reference_alignment_score": round(
                    self._clamp01(
                        post_task_effect_memory.get("average_reference_alignment_score") or 0.0
                    ),
                    4,
                ),
                "dominant_target_effect": str(
                    post_task_effect_memory.get("dominant_target_effect") or ""
                ).strip(),
            },
            "cognitive_assessment_memory": {
                "available": bool(cognitive_assessment_memory.get("available")),
                "dominant_constraint": str(
                    cognitive_assessment_memory.get("dominant_constraint") or ""
                ).strip(),
                "common_current_judgements": [
                    str(item).strip()
                    for item in list(
                        cognitive_assessment_memory.get("common_current_judgements") or []
                    )[:4]
                    if str(item).strip()
                ],
                "common_why_not_improvement_now": [
                    str(item).strip()
                    for item in list(
                        cognitive_assessment_memory.get("common_why_not_improvement_now") or []
                    )[:4]
                    if str(item).strip()
                ],
                "common_self_iteration_targets": [
                    str(item).strip()
                    for item in list(
                        cognitive_assessment_memory.get("common_self_iteration_targets") or []
                    )[:4]
                    if str(item).strip()
                ],
                "common_self_iteration_hypotheses": [
                    str(item).strip()
                    for item in list(
                        cognitive_assessment_memory.get("common_self_iteration_hypotheses") or []
                    )[:4]
                    if str(item).strip()
                ],
            },
            "proposal_drift_memory": {
                "available": bool(proposal_drift_memory.get("available")),
                "average_score": round(
                    self._clamp01(proposal_drift_memory.get("average_score") or 0.0),
                    4,
                ),
                "drift_state": str(proposal_drift_memory.get("drift_state") or "").strip(),
                "quality_counts": dict(proposal_drift_memory.get("quality_counts") or {}),
                "common_posture_alignment": [
                    str(item).strip()
                    for item in list(proposal_drift_memory.get("common_posture_alignment") or [])[:4]
                    if str(item).strip()
                ],
                "common_priority_basis": [
                    str(item).strip()
                    for item in list(proposal_drift_memory.get("common_priority_basis") or [])[:4]
                    if str(item).strip()
                ],
                "posture_alignment_health": str(
                    proposal_drift_memory.get("posture_alignment_health") or ""
                ).strip(),
                "priority_basis_health": str(
                    proposal_drift_memory.get("priority_basis_health") or ""
                ).strip(),
                "dominant_posture_conflict_reason": str(
                    proposal_drift_memory.get("dominant_posture_conflict_reason") or ""
                ).strip(),
            },
            "recent_reference_alignment": {
                "available": bool(recent_reference_alignment.get("available")),
                "average_alignment_score": round(
                    self._clamp01(
                        recent_reference_alignment.get("average_alignment_score") or 0.0
                    ),
                    4,
                ),
                "weak_or_partial_count": max(
                    0,
                    int(recent_reference_alignment.get("weak_or_partial_count") or 0),
                ),
            },
            "cognitive_posture": {
                "name": str(cognitive_posture.get("name") or "").strip(),
                "selection_mode": str(cognitive_posture.get("selection_mode") or "").strip(),
                "selection_reason": str(cognitive_posture.get("selection_reason") or "").strip(),
                "summary": str(cognitive_posture.get("summary") or "").strip(),
                "observation_multiplier": round(
                    self._clamp01(cognitive_posture.get("observation_multiplier") or 0.0),
                    4,
                ),
                "throttle_multiplier": round(
                    self._clamp01(cognitive_posture.get("throttle_multiplier") or 0.0),
                    4,
                ),
                "truthfulness_multiplier": round(
                    self._clamp01(cognitive_posture.get("truthfulness_multiplier") or 0.0),
                    4,
                ),
                "learning_suppression_multiplier": round(
                    self._clamp01(
                        cognitive_posture.get("learning_suppression_multiplier") or 0.0
                    ),
                    4,
                ),
            },
            "evidence_basis": {
                "self_iteration_readiness_score": round(
                    self._clamp01(
                        readiness.get("self_iteration_readiness_score") or 0.0
                    ),
                    4,
                ),
                "autonomy_readiness": round(
                    self._clamp01(readiness.get("autonomy_readiness") or 0.0),
                    4,
                ),
                "self_understanding_gaps": [
                    str(item).strip()
                    for item in list(self_model_snapshot.get("self_understanding_gaps") or [])[:6]
                    if str(item).strip()
                ],
                "high_credibility_channels": [
                    str(item).strip()
                    for item in list(
                        evidence_credibility_summary.get("high_credibility_channels") or []
                    )[:5]
                    if str(item).strip()
                ],
                "weak_or_missing_channels": [
                    str(item).strip()
                    for item in list(
                        evidence_credibility_summary.get("weak_or_missing_channels") or []
                    )[:5]
                    if str(item).strip()
                ],
                "reference_alignment_score": round(
                    self._clamp01(
                        evidence_credibility_summary.get("reference_alignment_score") or 0.0
                    ),
                    4,
                ),
                "evidence_channels": evidence_channels,
            },
            "summary": summary,
        }

    def _build_meta_cognition_profile(
        self,
        *,
        grounding_focus: Dict[str, Any],
        self_iteration_hypotheses: Dict[str, Any],
        cognitive_assessment_memory: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        switch_self_regulation_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
        task_type_priors: Dict[str, Any],
    ) -> Dict[str, Any]:
        current_judgement = str(
            (
                list(cognitive_assessment_memory.get("common_current_judgements") or [None])[0]
                if cognitive_assessment_memory.get("common_current_judgements")
                else ""
            )
            or ""
        ).strip()
        dominant_constraint = str(
            cognitive_assessment_memory.get("dominant_constraint") or ""
        ).strip()
        lm_self_iteration_target = str(
            (
                list(cognitive_assessment_memory.get("common_self_iteration_targets") or [None])[0]
                if cognitive_assessment_memory.get("common_self_iteration_targets")
                else ""
            )
            or ""
        ).strip()
        lm_self_iteration_hypothesis = str(
            (
                list(
                    cognitive_assessment_memory.get(
                        "common_self_iteration_hypotheses"
                    )
                    or [None]
                )[0]
                if cognitive_assessment_memory.get("common_self_iteration_hypotheses")
                else ""
            )
            or ""
        ).strip()
        top_self_iteration_domain = str(
            lm_self_iteration_target
            or self_iteration_trend_memory.get("dominant_target")
            or self_iteration_hypotheses.get("top_target_domain")
            or ""
        ).strip()
        top_self_iteration_hypothesis = str(
            lm_self_iteration_hypothesis
            or (
                list(self_iteration_trend_memory.get("common_hypotheses") or [None])[0]
                if self_iteration_trend_memory.get("common_hypotheses")
                else ""
            )
            or self_iteration_hypotheses.get("dominant_hypothesis")
            or (
                hypotheses[0].get("hypothesis")
                if (
                    isinstance(
                        hypotheses := list(self_iteration_hypotheses.get("hypotheses") or []),
                        list,
                    )
                    and hypotheses
                    and isinstance(hypotheses[0], dict)
                )
                else ""
            )
            or ""
        ).strip()
        stay_or_switch_bias = str(
            (
                list(self_iteration_trend_memory.get("common_stay_or_switch") or [None])[0]
                if self_iteration_trend_memory.get("common_stay_or_switch")
                else switch_self_regulation_memory.get("preferred_switch_bias")
            )
            or ""
        ).strip()
        if stay_or_switch_bias == "balanced":
            stay_or_switch_bias = ""
        switch_bias_effectiveness = str(
            switch_self_regulation_memory.get("switch_effectiveness") or ""
        ).strip()
        if stay_or_switch_bias == "stay":
            switch_bias_effectiveness = str(
                switch_self_regulation_memory.get("stay_effectiveness") or ""
            ).strip()
        recent_effect_direction = str(
            post_task_effect_memory.get("effect_direction") or ""
        ).strip()
        common_why_not_improvement_now = [
            str(item).strip()
            for item in list(cognitive_assessment_memory.get("common_why_not_improvement_now") or [])[:4]
            if str(item).strip()
        ]
        grounding_gap_count = len(
            [str(item).strip() for item in list(grounding_focus.get("grounding_gaps") or []) if str(item).strip()]
        )
        contradictory_count = len(
            [str(item).strip() for item in list(grounding_focus.get("contradictory_topics") or []) if str(item).strip()]
        )
        grounding_pressure = "low"
        if grounding_gap_count >= 4 or contradictory_count >= 2:
            grounding_pressure = "high"
        elif grounding_gap_count >= 1 or contradictory_count >= 1:
            grounding_pressure = "medium"

        top_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        dominant_failure_mode = ""
        if grounding_pressure == "high":
            dominant_failure_mode = "grounding_instability"
        elif drift_state in {"drifting", "correcting"}:
            dominant_failure_mode = "proposal_selection_drift"
        elif recent_effect_direction == "degrading":
            dominant_failure_mode = "self_iteration_not_improving_outcomes"
        elif dominant_constraint:
            dominant_failure_mode = dominant_constraint

        governance_posture = "review"
        if grounding_pressure == "high":
            governance_posture = "observation_or_review"
        elif recent_effect_direction == "degrading":
            governance_posture = "review"
        elif common_why_not_improvement_now:
            governance_posture = "review"
        elif current_judgement and any(
            token in current_judgement.lower()
            for token in ("review", "observe", "observation", "grounding")
        ):
            governance_posture = "review"
        elif top_task_type in {"observation", "review"}:
            governance_posture = top_task_type

        has_substantive_profile = any(
            [
                current_judgement,
                dominant_constraint,
                top_self_iteration_domain,
                top_self_iteration_hypothesis,
                stay_or_switch_bias,
                switch_bias_effectiveness,
                recent_effect_direction,
                dominant_failure_mode,
                grounding_pressure != "low",
            ]
        )
        priority_signals = [
            (
                f"grounding_pressure:{grounding_pressure}"
                if grounding_pressure != "low"
                else ""
            ),
            f"top_self_iteration_domain:{top_self_iteration_domain}" if top_self_iteration_domain else "",
            f"stay_or_switch_bias:{stay_or_switch_bias}" if stay_or_switch_bias else "",
            f"recent_effect_direction:{recent_effect_direction}" if recent_effect_direction else "",
            f"dominant_failure_mode:{dominant_failure_mode}" if dominant_failure_mode else "",
        ]
        if (
            has_substantive_profile
            and top_task_type
            and top_task_type in {"observation", "review"}
        ):
            priority_signals.append(f"compatible_projection_bias:{top_task_type}")
        priority_signals = [item for item in priority_signals if item]

        if not has_substantive_profile:
            return {
                "available": False,
                "summary": "No unified meta-cognition profile is available yet.",
            }

        return {
            "available": True,
            "current_judgement": current_judgement or None,
            "dominant_constraint": dominant_constraint or None,
            "grounding_pressure": grounding_pressure,
            "top_self_iteration_domain": top_self_iteration_domain or None,
            "top_self_iteration_hypothesis": top_self_iteration_hypothesis or None,
            "stay_or_switch_bias": stay_or_switch_bias or None,
            "switch_bias_effectiveness": switch_bias_effectiveness or None,
            "recent_effect_direction": recent_effect_direction or None,
            "dominant_failure_mode": dominant_failure_mode or None,
            "governance_posture": governance_posture,
            "compatible_projection_bias": top_task_type or None,
            "priority_signals": priority_signals[:6],
            "summary": (
                "Unified meta-cognition indicates "
                f"judgement={current_judgement or 'unknown'}; "
                f"constraint={dominant_constraint or 'unknown'}; "
                f"grounding_pressure={grounding_pressure}; "
                f"self_iteration_domain={top_self_iteration_domain or 'unknown'}; "
                f"recent_effect_direction={recent_effect_direction or 'unknown'}."
            ),
        }

    def _materialize_lm_task_proposals(
        self,
        *,
        proposals: List[Dict[str, Any]],
        existing_keys: set[str],
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        evidence_packet: Dict[str, Any],
    ) -> List[EndogenousTaskCandidate]:
        realized: List[EndogenousTaskCandidate] = []
        perception = deliberation.perception
        adaptive_policy = deliberation.adaptive_policy
        evidence_graph = dict(evidence_packet.get("evidence_graph") or {})
        agenda_graph = dict(evidence_packet.get("agenda_graph") or {})
        batch_cognitive_assessment = self._normalize_lm_cognitive_assessment(
            self._latest_lm_task_generation_context.get("cognitive_assessment")
        )
        intent_by_kind = {
            str(intent.candidate_kind or "").strip(): intent
            for intent in deliberation.intents
            if intent.candidate_kind
        }
        kind_map = {
            "memory_maintenance": {
                "stable_prefix": "lm:continuity:memory_maintenance",
                "governance_task_type": "memory_maintenance",
                "task_family": "memory_maintenance",
                "execution_kind": "memory_maintenance",
                "value_tags": ["continuity"],
            },
            "truthfulness_review": {
                "stable_prefix": "lm:truthfulness:review",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_kind": None,
                "value_tags": ["truthfulness"],
            },
            "exploratory_learning": {
                "stable_prefix": "lm:creativity:exploratory",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_kind": None,
                "value_tags": ["creativity"],
            },
            "shell_baseline_learning": {
                "stable_prefix": "lm:creativity:shell_baseline",
                "governance_task_type": "self_learning",
                "task_family": "self_learning",
                "execution_kind": None,
                "value_tags": ["creativity"],
            },
            "queue_hygiene_review": {
                "stable_prefix": "lm:continuity:queue_hygiene",
                "governance_task_type": "self_evolution",
                "task_family": "general_self_evolution",
                "execution_kind": "general_self_evolution",
                "value_tags": ["continuity", "truthfulness"],
            },
            "body_improvement": {
                "stable_prefix": "lm:creativity:body_improvement",
                "governance_task_type": "self_evolution",
                "task_family": "body_upgrade",
                "execution_kind": "body_improvement",
                "value_tags": ["creativity", "continuity"],
            },
        }

        for item in proposals:
            candidate_kind = str(item.get("candidate_kind") or "").strip()
            mapping = kind_map.get(candidate_kind)
            if mapping is None:
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not title or not summary:
                continue
            stable_key = f"{mapping['stable_prefix']}:{_stable_key_for_topic(title)}"
            if stable_key in existing_keys:
                continue
            confidence = self._clamp01(item.get("confidence") or 0.5)
            evidence_summary = [
                str(row).strip()
                for row in list(item.get("evidence_summary") or [])
                if str(row).strip()
            ][:6]
            rationale = str(item.get("rationale") or "").strip()
            task_type = self._normalize_lm_task_type(item.get("task_type"), candidate_kind)
            risk_level = self._normalize_lm_risk_level(item.get("risk_level"), candidate_kind)
            evidence_level = self._normalize_lm_evidence_level(
                item.get("evidence_level"),
                confidence=confidence,
            )
            llm_observation_required = self._normalize_lm_observation_required(
                item.get("observation_required"),
                candidate_kind=candidate_kind,
                evidence_level=evidence_level,
                risk_level=risk_level,
            )
            llm_execution_mode = self._normalize_lm_execution_mode(
                item.get("execution_mode"),
                candidate_kind=candidate_kind,
                evidence_level=evidence_level,
                risk_level=risk_level,
                observation_required=llm_observation_required,
            )
            blocking_factors = self._normalize_lm_string_list(
                item.get("blocking_factors"),
                limit=6,
            )
            referenced_evidence_nodes = self._normalize_lm_string_list(
                item.get("referenced_evidence_nodes"),
                limit=8,
            )
            referenced_agenda_nodes = self._normalize_lm_string_list(
                item.get("referenced_agenda_nodes"),
                limit=8,
            )
            posture_alignment = self._normalize_lm_string_list(
                item.get("posture_alignment"),
                limit=6,
            )
            priority_basis = self._normalize_lm_string_list(
                item.get("priority_basis"),
                limit=6,
            )
            reference_alignment = self._align_lm_references(
                referenced_evidence_nodes=referenced_evidence_nodes,
                referenced_agenda_nodes=referenced_agenda_nodes,
                evidence_graph=evidence_graph,
                agenda_graph=agenda_graph,
            )
            supervisor_advisory = self._supervisor_advisory_for_lm_proposal(
                candidate_kind=candidate_kind,
                evidence_level=evidence_level,
                risk_level=risk_level,
                observation_required=llm_observation_required,
                execution_mode=llm_execution_mode,
                blocking_factors=blocking_factors,
                reference_alignment=reference_alignment,
            )
            cognitive_alignment = self._score_lm_proposal_cognitive_alignment(
                candidate_kind=candidate_kind,
                task_type=task_type,
                evidence_level=evidence_level,
                risk_level=risk_level,
                observation_required=llm_observation_required,
                execution_mode=llm_execution_mode,
                blocking_factors=blocking_factors,
                reference_alignment=reference_alignment,
                evidence_packet=evidence_packet,
                posture_alignment=posture_alignment,
                priority_basis=priority_basis,
            )
            intent = intent_by_kind.get(candidate_kind)
            constraints = self._constraints_for_lm_candidate_kind(
                candidate_kind=candidate_kind,
            )
            constraints.update(
                {
                    "lm_execution_mode": llm_execution_mode,
                    "lm_observation_required": llm_observation_required,
                }
            )
            if blocking_factors:
                constraints["lm_blocking_factors"] = list(blocking_factors)
            if referenced_evidence_nodes:
                constraints["lm_referenced_evidence_nodes"] = list(referenced_evidence_nodes)
            if referenced_agenda_nodes:
                constraints["lm_referenced_agenda_nodes"] = list(referenced_agenda_nodes)
            if posture_alignment:
                constraints["lm_posture_alignment"] = list(posture_alignment)
            if priority_basis:
                constraints["lm_priority_basis"] = list(priority_basis)
            constraints["reference_alignment"] = reference_alignment
            constraints["cognitive_alignment"] = cognitive_alignment
            constraints["supervisor_recommended_execution_mode"] = supervisor_advisory["recommended_execution_mode"]
            constraints["supervisor_recommended_observation_required"] = supervisor_advisory["recommended_observation_required"]
            if supervisor_advisory["advisory_reasons"]:
                constraints["supervisor_advisory_reasons"] = list(supervisor_advisory["advisory_reasons"])
            realized.append(
                self._build_scored_candidate(
                    stable_key=stable_key,
                    title=title,
                    summary=summary,
                    priority="high" if confidence >= 0.75 else "normal",
                    governance_task_type=str(mapping["governance_task_type"]),
                    task_family=str(mapping["task_family"]),
                    execution_kind=mapping["execution_kind"],
                    value_tags=list(mapping["value_tags"]),
                    candidate_kind=candidate_kind,
                    score_inputs={
                        "core_value_strength": 0.72,
                        "urgency": confidence,
                        "novelty": 0.66,
                        "specificity": self._clamp01(0.45 + min(len(summary), 240) / 400.0),
                        "execution_readiness": self._clamp01(
                            0.48
                            + confidence * 0.4
                            - self._clamp01(reference_alignment.get("grounding_penalty") or 0.0) * 0.28
                            - (
                                0.08
                                if list(reference_alignment.get("missing_primary_evidence_nodes") or [])
                                else 0.0
                            )
                            - (
                                0.08
                                if list(reference_alignment.get("missing_primary_agenda_nodes") or [])
                                else 0.0
                            )
                        ),
                        "queue_pressure_penalty": self._queue_pressure_penalty(
                            drive_context,
                            governance_task_type=str(mapping["governance_task_type"]),
                            task_family=str(mapping["task_family"]),
                            execution_kind=mapping["execution_kind"],
                        ),
                        "repetition_penalty": self._clamp01(
                            float(reference_alignment.get("grounding_penalty") or 0.0) * 0.55
                            + (
                                0.12
                                if not referenced_evidence_nodes or not referenced_agenda_nodes
                                else 0.0
                            )
                        ),
                        "adaptive_factor": self._adaptive_factor_for_candidate(
                            candidate_kind=candidate_kind,
                            adaptive_policy=adaptive_policy,
                        ),
                    },
                    metadata={
                        "llm_task_generated": True,
                        "llm_task_confidence": confidence,
                        "llm_task_rationale": rationale,
                        "llm_task_type": task_type,
                        "llm_task_risk_level": risk_level,
                        "llm_task_evidence_level": evidence_level,
                        "llm_task_observation_required": llm_observation_required,
                        "llm_task_execution_mode": llm_execution_mode,
                        "llm_task_blocking_factors": list(blocking_factors),
                        "llm_referenced_evidence_nodes": list(referenced_evidence_nodes),
                        "llm_referenced_agenda_nodes": list(referenced_agenda_nodes),
                        "llm_posture_alignment": list(posture_alignment),
                        "llm_priority_basis": list(priority_basis),
                        "llm_cognitive_assessment": dict(batch_cognitive_assessment),
                        "reference_alignment": reference_alignment,
                        "cognitive_alignment": cognitive_alignment,
                        "supervisor_advisory": supervisor_advisory,
                        **(
                            {
                                "drive_judgement": self._intent_metadata(
                                    intent=intent,
                                    needs=list(deliberation.needs),
                                    perception=deliberation.perception,
                                    world_model=deliberation.world_model,
                                    reflection=deliberation.reflection,
                                    adaptive_policy=deliberation.adaptive_policy,
                                )
                            }
                            if intent is not None
                            else {}
                        ),
                    },
                    evidence={
                        "llm_generated": True,
                        "evidence_summary": evidence_summary,
                        "llm_rationale": rationale,
                        "llm_task_type": task_type,
                        "llm_risk_level": risk_level,
                        "llm_evidence_level": evidence_level,
                        "llm_observation_required": llm_observation_required,
                        "llm_execution_mode": llm_execution_mode,
                        "llm_blocking_factors": list(blocking_factors),
                        "llm_referenced_evidence_nodes": list(referenced_evidence_nodes),
                        "llm_referenced_agenda_nodes": list(referenced_agenda_nodes),
                        "llm_posture_alignment": list(posture_alignment),
                        "llm_priority_basis": list(priority_basis),
                        "llm_cognitive_assessment": dict(batch_cognitive_assessment),
                        "reference_alignment": reference_alignment,
                        "cognitive_alignment": cognitive_alignment,
                        "supervisor_advisory": supervisor_advisory,
                        "active_sessions": perception.active_sessions,
                    },
                    constraints=constraints,
                )
            )
            existing_keys.add(stable_key)
        return realized

    def _constraints_for_lm_candidate_kind(
        self,
        *,
        candidate_kind: str,
    ) -> Dict[str, Any]:
        if candidate_kind in {"exploratory_learning", "truthfulness_review", "shell_baseline_learning"}:
            constraints: Dict[str, Any] = {
                "execution_policy": "learn_only",
                "must_not_modify_active_body": True,
            }
            if candidate_kind == "shell_baseline_learning":
                constraints["execution_policy"] = "learn_shell_baseline"
            return constraints
        if candidate_kind == "queue_hygiene_review":
            return {"must_not_execute_without_review": True}
        if candidate_kind == "body_improvement":
            return {
                "execution_policy": "improve_shell_body",
                "target_slot": "shell",
                "must_commit": True,
                "evolution_boundary_check": True,
            }
        return {}

    def _normalize_lm_task_type(self, value: Any, candidate_kind: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in _LM_TASK_TYPES:
            return normalized
        return self._task_type_for_candidate_kind(candidate_kind)

    def _task_type_for_candidate_kind(self, candidate_kind: Any) -> str:
        normalized_kind = str(candidate_kind or "").strip()
        defaults = {
            "memory_maintenance": "maintenance",
            "truthfulness_review": "review",
            "exploratory_learning": "learning",
            "shell_baseline_learning": "learning",
            "queue_hygiene_review": "review",
            "body_improvement": "improvement",
        }
        return defaults.get(normalized_kind, "observation")

    def _normalize_lm_risk_level(self, value: Any, candidate_kind: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in _LM_RISK_LEVELS:
            return normalized
        defaults = {
            "memory_maintenance": "medium",
            "truthfulness_review": "low",
            "exploratory_learning": "low",
            "shell_baseline_learning": "low",
            "queue_hygiene_review": "medium",
            "body_improvement": "high",
        }
        return defaults.get(candidate_kind, "medium")

    def _normalize_lm_evidence_level(self, value: Any, *, confidence: float) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in _LM_EVIDENCE_LEVELS:
            return normalized
        if confidence >= 0.8:
            return "strong"
        if confidence >= 0.45:
            return "moderate"
        return "weak"

    def _normalize_lm_observation_required(
        self,
        value: Any,
        *,
        candidate_kind: str,
        evidence_level: str,
        risk_level: str,
    ) -> bool:
        if isinstance(value, bool):
            normalized = value
        else:
            text = str(value or "").strip().lower()
            if text in {"true", "1", "yes", "on"}:
                normalized = True
            elif text in {"false", "0", "no", "off"}:
                normalized = False
            else:
                normalized = candidate_kind in {"truthfulness_review", "queue_hygiene_review"}
        return normalized

    def _normalize_lm_execution_mode(
        self,
        value: Any,
        *,
        candidate_kind: str,
        evidence_level: str,
        risk_level: str,
        observation_required: bool,
    ) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _LM_EXECUTION_MODES:
            defaults = {
                "memory_maintenance": "guarded_execution",
                "truthfulness_review": "observe_only",
                "exploratory_learning": "review_then_queue",
                "shell_baseline_learning": "review_then_queue",
                "queue_hygiene_review": "review_then_queue",
                "body_improvement": "guarded_execution",
            }
            normalized = defaults.get(candidate_kind, "review_then_queue")
        return normalized

    def _normalize_lm_string_list(self, value: Any, *, limit: int = 6) -> List[str]:
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

    def _align_lm_references(
        self,
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
        valid_agenda_nodes = set()
        agenda_priorities: Dict[str, float] = {}
        focus = str(agenda_graph.get("focus") or "").strip()
        if focus:
            valid_agenda_nodes.add(f"focus:{focus}")
            agenda_priorities[f"focus:{focus}"] = float(agenda_graph.get("focus_confidence") or 0.0)
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

        matched_evidence = [node for node in referenced_evidence_nodes if node in valid_evidence_nodes]
        missing_evidence = [node for node in referenced_evidence_nodes if node not in valid_evidence_nodes]
        matched_agenda = [node for node in referenced_agenda_nodes if node in valid_agenda_nodes]
        missing_agenda = [node for node in referenced_agenda_nodes if node not in valid_agenda_nodes]

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
            round(self._clamp01(total_matched / total_requested - weak_penalty), 4)
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
                        if isinstance(row, dict) and str(row.get("direction") or "").strip()
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
            alignment_score = round(self._clamp01(alignment_score - grounding_penalty), 4)
            if alignment_quality == "strong":
                alignment_quality = "partial"
            if alignment_score < 0.45 or (
                primary_evidence_nodes and not matched_primary_evidence_nodes and primary_agenda_nodes and not matched_primary_agenda_nodes
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
            "grounding_penalty": round(self._clamp01(grounding_penalty), 4),
            "alignment_score": alignment_score,
            "alignment_quality": alignment_quality,
        }

    def _supervisor_advisory_for_lm_proposal(
        self,
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
                recommended_execution_mode = "review_then_queue"
        if risk_level == "high":
            advisory_reasons.append("high_risk_requires_governance_review")
            recommended_observation_required = True
            if recommended_execution_mode == "guarded_execution":
                recommended_execution_mode = "review_then_queue"
        if candidate_kind in {"truthfulness_review", "queue_hygiene_review"}:
            advisory_reasons.append("review_family_prefers_observation_or_review_first")
            recommended_observation_required = True
        if blocking_factors:
            advisory_reasons.append("blocking_factors_present")
        alignment = dict(reference_alignment or {})
        alignment_quality = str(alignment.get("alignment_quality") or "").strip().lower()
        missing_primary_evidence_nodes = list(alignment.get("missing_primary_evidence_nodes") or [])
        missing_primary_agenda_nodes = list(alignment.get("missing_primary_agenda_nodes") or [])
        if alignment_quality in {"weak", "drifted"}:
            advisory_reasons.append("reference_binding_is_not_grounded_enough")
            recommended_observation_required = True
            if recommended_execution_mode == "guarded_execution":
                recommended_execution_mode = "review_then_queue"
        if missing_primary_evidence_nodes or missing_primary_agenda_nodes:
            advisory_reasons.append("primary_evidence_or_agenda_binding_is_missing")
            recommended_observation_required = True
            if recommended_execution_mode == "guarded_execution":
                recommended_execution_mode = "review_then_queue"
        if recommended_observation_required and recommended_execution_mode == "guarded_execution":
            recommended_execution_mode = "review_then_queue"
        return {
            "recommended_execution_mode": recommended_execution_mode,
            "recommended_observation_required": recommended_observation_required,
            "advisory_reasons": advisory_reasons,
        }

    def _score_lm_proposal_cognitive_alignment(
        self,
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
        prior_score = self._clamp01(prior_row.get("score") or 0.0)
        top_priority_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
        top_priority_score = self._clamp01(task_type_priors.get("top_priority_score") or 0.0)

        evidence_credibility_summary = dict(evidence_packet.get("evidence_credibility_summary") or {})
        weak_channels = [
            str(item).strip()
            for item in list(evidence_credibility_summary.get("weak_or_missing_channels") or [])
            if str(item).strip()
        ]
        high_channels = [
            str(item).strip()
            for item in list(evidence_credibility_summary.get("high_credibility_channels") or [])
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

        alignment_score = self._clamp01(reference_alignment.get("alignment_score") or 0.0)
        score += alignment_score * 0.18
        if alignment_score >= 0.75:
            reasons.append("reference_alignment_is_strong")
        elif alignment_score < 0.5:
            score -= 0.08
            reasons.append("reference_alignment_is_weak")
        grounding_penalty = self._clamp01(reference_alignment.get("grounding_penalty") or 0.0)
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

        score = self._clamp01(score)
        quality = "strong"
        if score < 0.45:
            quality = "weak"
        elif score < 0.7:
            quality = "partial"
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

    def _build_external_research_evidence(self) -> List[Dict[str, Any]]:
        service_runtime = getattr(self.config, "service_runtime", None)
        if service_runtime is None:
            return []
        if not bool(getattr(service_runtime, "endogenous_drive_external_research_enabled", False)):
            return []
        entries = list(
            getattr(service_runtime, "endogenous_drive_external_research_entries", []) or []
        )
        evidence_rows = self._normalize_external_research_entries(entries)
        evidence_rows.extend(
            self._load_external_research_files(
                list(getattr(service_runtime, "endogenous_drive_external_research_files", []) or [])
            )
        )
        return evidence_rows[:16]

    def _normalize_external_research_entries(self, entries: List[Any]) -> List[Dict[str, Any]]:
        evidence_rows: List[Dict[str, Any]] = []
        for raw in entries[:12]:
            text = str(raw or "").strip()
            if not text:
                continue
            if "::" in text:
                title, detail = text.split("::", 1)
                row = {
                    "title": title.strip(),
                    "summary": detail.strip(),
                    "source": "configured_external_research",
                }
                row.update(
                    self._item_evidence_quality(
                        item=row,
                        source_reliability=0.62,
                        supports=["external_research", "forward_direction"],
                        contradicts=[],
                    )
                )
                evidence_rows.append(row)
            else:
                row = {
                    "title": text[:80],
                    "summary": text,
                    "source": "configured_external_research",
                }
                row.update(
                    self._item_evidence_quality(
                        item=row,
                        source_reliability=0.58,
                        supports=["external_research"],
                        contradicts=[],
                    )
                )
                evidence_rows.append(row)
        return evidence_rows

    def _load_external_research_files(self, file_entries: List[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for raw_path in file_entries[:6]:
            path_text = str(raw_path or "").strip()
            if not path_text:
                continue
            rows.extend(self._load_external_research_file(path_text))
            if len(rows) >= 12:
                break
        return rows[:12]

    def _load_external_research_file(self, raw_path: str) -> List[Dict[str, Any]]:
        try:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                repo_root = Path(getattr(self.config.execution, "git_repo_path", "./") or "./")
                path = (repo_root / path).resolve()
            else:
                path = path.resolve()
            if not path.exists() or not path.is_file():
                return []
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        rows = self._normalize_external_research_file_payload(data, source_path=str(path))
        return rows[:8]

    def _normalize_external_research_file_payload(
        self,
        data: Any,
        *,
        source_path: str,
    ) -> List[Dict[str, Any]]:
        if isinstance(data, dict):
            items = data.get("entries")
            if isinstance(items, list):
                return self._normalize_external_research_items(items, source_path=source_path)
            return self._normalize_external_research_items([data], source_path=source_path)
        if isinstance(data, list):
            return self._normalize_external_research_items(data, source_path=source_path)
        return []

    def _normalize_external_research_items(
        self,
        items: List[Any],
        *,
        source_path: str,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in items[:12]:
            if isinstance(item, dict):
                title = str(item.get("title") or item.get("topic") or "").strip()
                summary = str(item.get("summary") or item.get("note") or item.get("content") or "").strip()
                if not title and not summary:
                    continue
                row: Dict[str, Any] = {
                    "title": title or summary[:80],
                    "summary": summary or title,
                    "source": str(item.get("source") or "external_research_file"),
                    "source_path": source_path,
                }
                if item.get("url"):
                    row["url"] = str(item.get("url"))
                if item.get("published_at"):
                    row["published_at"] = str(item.get("published_at"))
                if item.get("tags"):
                    row["tags"] = [
                        str(tag).strip()
                        for tag in list(item.get("tags") or [])
                        if str(tag).strip()
                    ][:6]
                row.update(
                    self._item_evidence_quality(
                        item=row,
                        source_reliability=0.74 if row.get("url") else 0.64,
                        supports=["external_research", "forward_direction"],
                        contradicts=[],
                    )
                )
                rows.append(row)
            else:
                text = str(item or "").strip()
                if not text:
                    continue
                row = {
                    "title": text[:80],
                    "summary": text,
                    "source": "external_research_file",
                    "source_path": source_path,
                }
                row.update(
                    self._item_evidence_quality(
                        item=row,
                        source_reliability=0.56,
                        supports=["external_research"],
                        contradicts=[],
                    )
                )
                rows.append(row)
        return rows

    def _build_recent_learning_evidence(self, drive_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        completed_learning_tasks = list(drive_context.get("completed_learning_tasks") or [])
        evidence_rows: List[Dict[str, Any]] = []
        for task in completed_learning_tasks[:5]:
            title = str(task.get("title") or task.get("topic") or "").strip()
            if not title:
                continue
            row: Dict[str, Any] = {
                "title": title,
                "summary": str(task.get("summary") or "").strip()[:280],
                "quality_score": task.get("quality_score"),
                "completed_at": task.get("completed_at"),
                "task_family": task.get("task_family"),
                "execution_kind": task.get("execution_kind"),
            }
            evidence = task.get("evidence")
            if isinstance(evidence, dict):
                row["evidence_summary"] = [
                    str(item).strip()
                    for item in list(evidence.get("evidence_summary") or [])
                    if str(item).strip()
                ][:4]
            row.update(
                self._item_evidence_quality(
                    item=row,
                    source_reliability=0.84,
                    supports=["self_understanding", "learning_trace"],
                    contradicts=[],
                )
            )
            evidence_rows.append(row)
        return evidence_rows

    def _build_shell_body_profile(self, shell_slot_meta: Dict[str, Any]) -> Dict[str, Any]:
        profile: Dict[str, Any] = {
            "slot_id": str(shell_slot_meta.get("slot_id") or "").strip(),
            "worktree_path": str(shell_slot_meta.get("worktree_path") or "").strip(),
            "body_version": shell_slot_meta.get("body_version"),
            "generation": shell_slot_meta.get("generation"),
            "materialized_from": shell_slot_meta.get("materialized_from"),
            "candidate_branch": shell_slot_meta.get("candidate_branch"),
            "candidate_commit": shell_slot_meta.get("candidate_commit"),
        }
        worktree_path = profile["worktree_path"]
        if not worktree_path:
            profile["profile_status"] = "missing_worktree"
            return profile

        worktree = Path(worktree_path)
        if not worktree.exists():
            profile["profile_status"] = "worktree_missing_on_disk"
            return profile

        manifest_path = worktree / ".body-origin.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                profile["origin_manifest"] = {
                    "source": manifest.get("source"),
                    "source_root": manifest.get("source_root"),
                    "source_branch": manifest.get("source_branch"),
                    "source_commit": manifest.get("source_commit"),
                    "candidate_branch": manifest.get("candidate_branch"),
                    "candidate_commit": manifest.get("candidate_commit"),
                    "materialized_at": manifest.get("materialized_at"),
                }
            except Exception:
                profile["origin_manifest_error"] = True

        editable_indicators = [
            "agent",
            "skills",
            "tools",
            "prompts",
            "systems",
            "Mem",
            "tests",
        ]
        present_roots = [name for name in editable_indicators if (worktree / name).exists()]
        try:
            top_level_entries = sorted(child.name for child in worktree.iterdir())[:20]
        except Exception:
            top_level_entries = []

        profile.update(
            {
                "profile_status": "ready",
                "present_roots": present_roots,
                "top_level_entries": top_level_entries,
                "has_run_agent": (worktree / "run_agent.py").exists(),
                "has_config": (worktree / "config.yaml").exists(),
            }
        )
        profile.update(
            self._item_evidence_quality(
                item=profile,
                source_reliability=0.9,
                supports=["self_structure", "body_state"],
                contradicts=[],
            )
        )
        return profile

    def _item_evidence_quality(
        self,
        *,
        item: Dict[str, Any],
        source_reliability: float,
        supports: List[str],
        contradicts: List[str],
    ) -> Dict[str, Any]:
        confidence_score = self._item_confidence_score(
            item=item,
            source_reliability=source_reliability,
        )
        novelty_score = self._item_novelty_score(item)
        return {
            "confidence_score": confidence_score,
            "novelty_score": novelty_score,
            "source_reliability": round(self._clamp01(source_reliability), 4),
            "supports": list(supports),
            "contradicts": list(contradicts),
        }

    def _item_confidence_score(
        self,
        *,
        item: Dict[str, Any],
        source_reliability: float,
    ) -> float:
        quality_component = 0.0
        try:
            quality_component = self._clamp01(float(item.get("quality_score") or 0.0))
        except (TypeError, ValueError):
            quality_component = 0.0
        evidence_summary = list(item.get("evidence_summary") or [])
        evidence_bonus = min(len(evidence_summary), 4) * 0.06
        freshness_bonus = 0.0
        published_at = item.get("published_at") or item.get("completed_at")
        parsed_time = self._parse_timestamp(published_at)
        if parsed_time is not None:
            age_days = max(0, (datetime.now(timezone.utc) - parsed_time).days)
            if age_days <= 14:
                freshness_bonus = 0.18
            elif age_days <= 90:
                freshness_bonus = 0.1
            else:
                freshness_bonus = 0.03
        base = (
            0.22
            + self._clamp01(source_reliability) * 0.45
            + quality_component * 0.18
            + evidence_bonus
            + freshness_bonus
        )
        return round(self._clamp01(base), 4)

    def _item_novelty_score(self, item: Dict[str, Any]) -> float:
        text = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
            ]
        ).strip()
        if not text:
            return 0.2
        token_count = len({token.lower() for token in _TOPIC_WORD_RE.findall(text)})
        return round(self._clamp01(0.18 + min(token_count, 12) * 0.055), 4)

    def _build_evidence_channels(
        self,
        *,
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        shell_body_profile: Dict[str, Any],
        deliberation_dict: Dict[str, Any],
    ) -> Dict[str, Any]:
        learning_strength = self._channel_strength_from_learning(recent_learning_evidence)
        learning_confidence = self._channel_confidence_from_learning(recent_learning_evidence)
        body_confidence = self._channel_confidence_from_body(shell_body_profile)
        body_strength = "strong" if shell_body_profile.get("profile_status") == "ready" else "weak"
        research_strength = self._channel_strength_from_research(external_research_evidence)
        research_confidence = self._channel_confidence_from_research(external_research_evidence)
        research_freshness = self._research_freshness_hint(external_research_evidence)
        conflict_flags = self._evidence_conflict_flags(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
        )
        evidence_graph = self._build_evidence_graph(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
        )
        learning_channel = {
            "channel": "recent_learning",
            "kind": "internal_learning_evidence",
            "item_count": len(recent_learning_evidence),
            "freshness_hint": "recent",
            "confidence": learning_confidence,
            "evidence_strength": learning_strength,
            "conflict_flags": [
                flag for flag in conflict_flags if flag.startswith("learning_")
            ],
            "items": recent_learning_evidence[:5],
        }
        body_channel = {
            "channel": "shell_body_profile",
            "kind": "self_structure_evidence",
            "item_count": 1 if shell_body_profile else 0,
            "freshness_hint": "current",
            "confidence": body_confidence,
            "evidence_strength": body_strength,
            "conflict_flags": [
                flag for flag in conflict_flags if flag.startswith("body_")
            ],
            "items": [shell_body_profile] if shell_body_profile else [],
        }
        research_channel = {
            "channel": "external_research",
            "kind": "external_research_evidence",
            "item_count": len(external_research_evidence),
            "freshness_hint": research_freshness,
            "confidence": research_confidence,
            "evidence_strength": research_strength,
            "conflict_flags": [
                flag for flag in conflict_flags if flag.startswith("research_")
            ],
            "items": external_research_evidence[:8],
        }
        cognition_channel = {
            "channel": "deliberation_state",
            "kind": "internal_cognition_state",
            "item_count": 1,
            "freshness_hint": "current",
            "confidence": self._clamp01(
                0.45
                + float(deliberation_dict.get("world_model", {}).get("self_confidence") or 0.0) * 0.4
            ),
            "evidence_strength": "moderate",
            "conflict_flags": [],
            "items": [
                {
                    "perception": deliberation_dict.get("perception", {}),
                    "world_model": deliberation_dict.get("world_model", {}),
                    "reflection": deliberation_dict.get("reflection", {}),
                    "adaptive_policy": deliberation_dict.get("adaptive_policy", {}),
                }
            ],
        }
        return {
            "channels": [
                learning_channel,
                body_channel,
                research_channel,
                cognition_channel,
            ],
            "research_digest": {
                "item_count": len(external_research_evidence),
                "freshness_hint": research_freshness,
                "confidence": research_confidence,
                "evidence_strength": research_strength,
                "conflict_flags": [
                    flag for flag in conflict_flags if flag.startswith("research_")
                ],
                "sources": sorted(
                    {
                        str(item.get("source") or "").strip()
                        for item in external_research_evidence
                        if str(item.get("source") or "").strip()
                    }
                ),
                "topics": [
                    str(item.get("title") or "").strip()
                    for item in external_research_evidence[:6]
                    if str(item.get("title") or "").strip()
                ],
            },
            "evidence_graph": evidence_graph,
        }

    def _channel_strength_from_learning(self, items: List[Dict[str, Any]]) -> str:
        if not items:
            return "weak"
        quality_scores: List[float] = []
        for item in items[:5]:
            try:
                quality_scores.append(self._clamp01(float(item.get("quality_score") or 0.0)))
            except (TypeError, ValueError):
                continue
        if not quality_scores:
            return "moderate"
        avg = sum(quality_scores) / len(quality_scores)
        if avg >= 0.75:
            return "strong"
        if avg >= 0.4:
            return "moderate"
        return "weak"

    def _channel_confidence_from_learning(self, items: List[Dict[str, Any]]) -> float:
        if not items:
            return 0.22
        quality_scores: List[float] = []
        for item in items[:5]:
            try:
                quality_scores.append(self._clamp01(float(item.get("quality_score") or 0.0)))
            except (TypeError, ValueError):
                continue
        if not quality_scores:
            return 0.45
        avg = sum(quality_scores) / len(quality_scores)
        return round(self._clamp01(0.3 + avg * 0.6), 4)

    def _channel_confidence_from_body(self, shell_body_profile: Dict[str, Any]) -> float:
        status = str(shell_body_profile.get("profile_status") or "").strip().lower()
        if status == "ready":
            return 0.86
        if status in {"missing_worktree", "worktree_missing_on_disk"}:
            return 0.2
        return 0.45

    def _channel_strength_from_research(self, items: List[Dict[str, Any]]) -> str:
        if not items:
            return "weak"
        if len(items) >= 3 and self._research_freshness_hint(items) in {"fresh", "recent"}:
            return "strong"
        if len(items) >= 1:
            return "moderate"
        return "weak"

    def _channel_confidence_from_research(self, items: List[Dict[str, Any]]) -> float:
        if not items:
            return 0.18
        freshness = self._research_freshness_hint(items)
        freshness_bonus = {
            "fresh": 0.3,
            "recent": 0.22,
            "stale": 0.08,
            "unknown": 0.14,
        }.get(freshness, 0.12)
        source_count = len(
            {
                str(item.get("source") or "").strip()
                for item in items
                if str(item.get("source") or "").strip()
            }
        )
        return round(self._clamp01(0.24 + min(len(items), 4) * 0.08 + source_count * 0.05 + freshness_bonus), 4)

    def _evidence_conflict_flags(
        self,
        *,
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        shell_body_profile: Dict[str, Any],
    ) -> List[str]:
        flags: List[str] = []
        if not recent_learning_evidence:
            flags.append("learning_missing_recent_history")
        if recent_learning_evidence and self._channel_strength_from_learning(recent_learning_evidence) == "weak":
            flags.append("learning_weak_quality_signal")
        if shell_body_profile.get("profile_status") != "ready":
            flags.append("body_profile_incomplete")
        if not external_research_evidence:
            flags.append("research_missing_external_support")
        elif self._research_freshness_hint(external_research_evidence) == "stale":
            flags.append("research_stale_support")
        return flags

    def _build_recent_reference_alignment(self, drive_context: Dict[str, Any]) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        recent_alignment_entries: List[Dict[str, Any]] = []
        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            alignment = outcome.get("reference_alignment")
            if not isinstance(alignment, dict):
                alignment = metadata.get("reference_alignment")
            if not isinstance(alignment, dict):
                alignment = evidence.get("reference_alignment")
            if not isinstance(alignment, dict):
                continue
            recent_alignment_entries.append(
                {
                    "title": str(outcome.get("title") or "").strip(),
                    "alignment_quality": str(alignment.get("alignment_quality") or "").strip(),
                    "alignment_score": float(alignment.get("alignment_score") or 0.0),
                    "missing_evidence_nodes": list(alignment.get("missing_evidence_nodes") or [])[:4],
                    "missing_agenda_nodes": list(alignment.get("missing_agenda_nodes") or [])[:4],
                }
            )
            if len(recent_alignment_entries) >= 4:
                break

        if not recent_alignment_entries:
            return {
                "available": False,
                "recent_entries": [],
                "summary": "No recent reference-alignment feedback is available yet.",
            }

        avg_score = sum(entry["alignment_score"] for entry in recent_alignment_entries) / len(recent_alignment_entries)
        weak_count = sum(
            1
            for entry in recent_alignment_entries
            if entry["alignment_quality"] in {"weak", "partial", "drifted"}
        )
        return {
            "available": True,
            "recent_entries": recent_alignment_entries,
            "average_alignment_score": round(self._clamp01(avg_score), 4),
            "weak_or_partial_count": weak_count,
            "summary": (
                f"Recent proposals show average reference alignment {self._clamp01(avg_score):.2f}; "
                f"{weak_count} entries were weak/partial/drifted."
            ),
        }

    def _build_cognitive_assessment_memory(self, drive_context: Dict[str, Any]) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        current_judgement_counts: Dict[str, int] = {}
        dominant_constraint_counts: Dict[str, int] = {}
        why_not_improvement_counts: Dict[str, int] = {}
        self_iteration_target_counts: Dict[str, int] = {}
        self_iteration_hypothesis_counts: Dict[str, int] = {}
        gap_counts: Dict[str, int] = {}
        entries: List[Dict[str, Any]] = []

        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            normalized = self._normalize_lm_cognitive_assessment(assessment)
            if not normalized:
                continue
            current_judgement = str(normalized.get("current_judgement") or "").strip()
            dominant_constraint = str(normalized.get("dominant_constraint") or "").strip()
            why_not_improvement_now = [
                str(item).strip()
                for item in list(normalized.get("why_not_improvement_now") or [])[:3]
                if str(item).strip()
            ]
            self_iteration_target = str(
                normalized.get("self_iteration_target") or ""
            ).strip()
            self_iteration_hypothesis = str(
                normalized.get("self_iteration_hypothesis") or ""
            ).strip()
            primary_grounding_gaps = [
                str(item).strip()
                for item in list(normalized.get("primary_grounding_gaps") or [])[:3]
                if str(item).strip()
            ]
            if current_judgement:
                current_judgement_counts[current_judgement] = (
                    current_judgement_counts.get(current_judgement, 0) + 1
                )
            if dominant_constraint:
                dominant_constraint_counts[dominant_constraint] = (
                    dominant_constraint_counts.get(dominant_constraint, 0) + 1
                )
            if self_iteration_target:
                self_iteration_target_counts[self_iteration_target] = (
                    self_iteration_target_counts.get(self_iteration_target, 0) + 1
                )
            if self_iteration_hypothesis:
                self_iteration_hypothesis_counts[self_iteration_hypothesis] = (
                    self_iteration_hypothesis_counts.get(self_iteration_hypothesis, 0) + 1
                )
            for item in why_not_improvement_now:
                why_not_improvement_counts[item] = (
                    why_not_improvement_counts.get(item, 0) + 1
                )
            for item in primary_grounding_gaps:
                gap_counts[item] = gap_counts.get(item, 0) + 1
            entries.append(
                {
                    "title": str(outcome.get("title") or "").strip(),
                    "current_judgement": current_judgement or None,
                    "dominant_constraint": dominant_constraint or None,
                    "self_iteration_target": self_iteration_target or None,
                    "self_iteration_hypothesis": self_iteration_hypothesis or None,
                    "primary_grounding_gaps": primary_grounding_gaps,
                    "why_not_improvement_now": why_not_improvement_now,
                    "event_type": str(outcome.get("event_type") or "").strip(),
                }
            )
            if len(entries) >= 4:
                break

        if not entries:
            return {
                "available": False,
                "entries": [],
                "summary": "No recent LM cognitive-assessment memory is available yet.",
            }

        common_current_judgements = [
            item
            for item, _count in sorted(
                current_judgement_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_why_not_improvement_now = [
            item
            for item, _count in sorted(
                why_not_improvement_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_self_iteration_targets = [
            item
            for item, _count in sorted(
                self_iteration_target_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_self_iteration_hypotheses = [
            item
            for item, _count in sorted(
                self_iteration_hypothesis_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        dominant_constraint = ""
        if dominant_constraint_counts:
            dominant_constraint = sorted(
                dominant_constraint_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[0][0]
        common_grounding_gaps = [
            item
            for item, _count in sorted(
                gap_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        return {
            "available": True,
            "entries": entries,
            "dominant_constraint": dominant_constraint or None,
            "common_current_judgements": common_current_judgements,
            "common_why_not_improvement_now": common_why_not_improvement_now,
            "common_self_iteration_targets": common_self_iteration_targets,
            "common_self_iteration_hypotheses": common_self_iteration_hypotheses,
            "common_grounding_gaps": common_grounding_gaps,
            "summary": (
                "Recent LM cognitive assessments repeatedly judge "
                f"{common_current_judgements[0] if common_current_judgements else 'the current state as unsettled'}; "
                f"dominant constraint={dominant_constraint or 'unknown'}."
            ),
        }

    def _build_self_iteration_trend_memory(
        self,
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        target_counts: Dict[str, int] = {}
        hypothesis_counts: Dict[str, int] = {}
        stay_switch_counts: Dict[str, int] = {}
        switch_reason_counts: Dict[str, int] = {}
        ordered_targets: List[str] = []
        entries: List[Dict[str, Any]] = []

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            normalized = self._normalize_lm_cognitive_assessment(assessment)
            if not normalized:
                continue
            target = str(normalized.get("self_iteration_target") or "").strip()
            hypothesis = str(normalized.get("self_iteration_hypothesis") or "").strip()
            stay_or_switch = str(normalized.get("stay_or_switch") or "").strip().lower()
            switch_reason = str(normalized.get("switch_reason") or "").strip()
            if not target and not hypothesis:
                continue
            if target:
                target_counts[target] = target_counts.get(target, 0) + 1
                ordered_targets.append(target)
            if hypothesis:
                hypothesis_counts[hypothesis] = hypothesis_counts.get(hypothesis, 0) + 1
            if stay_or_switch in {"stay", "switch"}:
                stay_switch_counts[stay_or_switch] = (
                    stay_switch_counts.get(stay_or_switch, 0) + 1
                )
            if switch_reason:
                switch_reason_counts[switch_reason] = (
                    switch_reason_counts.get(switch_reason, 0) + 1
                )
            entries.append(
                {
                    "title": str(outcome.get("title") or "").strip(),
                    "self_iteration_target": target or None,
                    "self_iteration_hypothesis": hypothesis or None,
                    "stay_or_switch": stay_or_switch or None,
                    "switch_reason": switch_reason or None,
                    "event_type": str(outcome.get("event_type") or "").strip(),
                }
            )
            if len(entries) >= 6:
                break

        if not entries:
            return {
                "available": False,
                "entries": [],
                "summary": "No long-horizon self-iteration trend memory is available yet.",
            }

        common_targets = [
            item
            for item, _count in sorted(
                target_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_hypotheses = [
            item
            for item, _count in sorted(
                hypothesis_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_stay_or_switch = [
            item
            for item, _count in sorted(
                stay_switch_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:2]
        ]
        common_switch_reasons = [
            item
            for item, _count in sorted(
                switch_reason_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        dominant_target = common_targets[0] if common_targets else ""
        recent_targets = [target for target in ordered_targets[:4] if str(target or "").strip()]
        unique_recent_targets = set(recent_targets)
        target_stability = "mixed"
        if len(unique_recent_targets) <= 1 and dominant_target:
            target_stability = "stable"
        elif len(unique_recent_targets) >= 3:
            target_stability = "volatile"
        trend_state = "exploring"
        dominant_count = target_counts.get(dominant_target, 0) if dominant_target else 0
        if dominant_target and dominant_count >= 3 and target_stability == "stable":
            trend_state = "locked"
        elif dominant_target and dominant_count >= 2:
            trend_state = "consolidating"
        elif target_stability == "volatile":
            trend_state = "searching"
        return {
            "available": True,
            "entries": entries,
            "dominant_target": dominant_target or None,
            "trend_state": trend_state,
            "target_stability": target_stability,
            "common_targets": common_targets,
            "common_hypotheses": common_hypotheses,
            "common_stay_or_switch": common_stay_or_switch,
            "common_switch_reasons": common_switch_reasons,
            "summary": (
                "Recent self-iteration reasoning trends toward "
                f"{dominant_target or 'unknown'}; trend_state={trend_state}; "
                f"target_stability={target_stability}."
            ),
        }

    def _build_switch_self_regulation_memory(
        self,
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        switch_quality_scores: List[float] = []
        stay_quality_scores: List[float] = []
        switch_alignment_scores: List[float] = []
        stay_alignment_scores: List[float] = []
        switch_reference_scores: List[float] = []
        stay_reference_scores: List[float] = []
        switch_result_statuses: List[str] = []
        stay_result_statuses: List[str] = []

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            normalized = self._normalize_lm_cognitive_assessment(assessment)
            if not normalized:
                continue
            decision = str(normalized.get("stay_or_switch") or "").strip().lower()
            if decision not in {"stay", "switch"}:
                continue
            quality_score = self._clamp01(float(outcome.get("quality_score") or 0.0))
            cognitive_alignment = outcome.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            alignment_score = self._clamp01(
                (cognitive_alignment or {}).get("score") or 0.0
            )
            reference_alignment = outcome.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")
            reference_score = self._clamp01(
                (reference_alignment or {}).get("alignment_score") or 0.0
            )
            result_status = str(outcome.get("result_status") or "").strip().lower()
            if decision == "switch":
                switch_quality_scores.append(quality_score)
                switch_alignment_scores.append(alignment_score)
                switch_reference_scores.append(reference_score)
                if result_status:
                    switch_result_statuses.append(result_status)
            else:
                stay_quality_scores.append(quality_score)
                stay_alignment_scores.append(alignment_score)
                stay_reference_scores.append(reference_score)
                if result_status:
                    stay_result_statuses.append(result_status)

        if not switch_quality_scores and not stay_quality_scores:
            return {
                "available": False,
                "summary": "No switch self-regulation memory is available yet.",
            }

        def _avg(values: List[float]) -> float:
            if not values:
                return 0.0
            return self._clamp01(sum(values) / len(values))

        average_switch_quality = _avg(switch_quality_scores)
        average_stay_quality = _avg(stay_quality_scores)
        average_switch_alignment = _avg(switch_alignment_scores)
        average_stay_alignment = _avg(stay_alignment_scores)
        average_switch_reference = _avg(switch_reference_scores)
        average_stay_reference = _avg(stay_reference_scores)

        switch_effectiveness = "unknown"
        stay_effectiveness = "unknown"
        if switch_quality_scores:
            switch_effectiveness = (
                "strong" if average_switch_quality >= 0.65 else "weak"
            )
        if stay_quality_scores:
            stay_effectiveness = (
                "strong" if average_stay_quality >= 0.65 else "weak"
            )
        preferred_switch_bias = "balanced"
        if switch_quality_scores and stay_quality_scores:
            if average_switch_quality >= average_stay_quality + 0.12:
                preferred_switch_bias = "switch"
            elif average_stay_quality >= average_switch_quality + 0.12:
                preferred_switch_bias = "stay"
        elif switch_quality_scores:
            preferred_switch_bias = "switch"
        elif stay_quality_scores:
            preferred_switch_bias = "stay"

        return {
            "available": True,
            "preferred_switch_bias": preferred_switch_bias,
            "switch_effectiveness": switch_effectiveness,
            "stay_effectiveness": stay_effectiveness,
            "average_switch_quality": round(average_switch_quality, 4),
            "average_stay_quality": round(average_stay_quality, 4),
            "average_switch_alignment": round(average_switch_alignment, 4),
            "average_stay_alignment": round(average_stay_alignment, 4),
            "average_switch_reference": round(average_switch_reference, 4),
            "average_stay_reference": round(average_stay_reference, 4),
            "switch_result_statuses": switch_result_statuses[:6],
            "stay_result_statuses": stay_result_statuses[:6],
            "summary": (
                "Recent stay/switch outcomes suggest "
                f"preferred_bias={preferred_switch_bias}; "
                f"switch_quality={average_switch_quality:.2f}; "
                f"stay_quality={average_stay_quality:.2f}."
            ),
        }

    def _build_post_task_effect_memory(
        self,
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        quality_scores: List[float] = []
        cognitive_scores: List[float] = []
        reference_scores: List[float] = []
        target_effect_counts: Dict[str, int] = {}
        entries: List[Dict[str, Any]] = []

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            cognitive_alignment = outcome.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            reference_alignment = outcome.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            normalized = self._normalize_lm_cognitive_assessment(assessment)
            quality_score = self._clamp01(float(outcome.get("quality_score") or 0.0))
            cognitive_score = self._clamp01(
                (cognitive_alignment or {}).get("score") or 0.0
            )
            reference_score = self._clamp01(
                (reference_alignment or {}).get("alignment_score") or 0.0
            )
            target = str(normalized.get("self_iteration_target") or "").strip()
            if not quality_score and not cognitive_score and not reference_score:
                continue
            quality_scores.append(quality_score)
            cognitive_scores.append(cognitive_score)
            reference_scores.append(reference_score)
            if target:
                effect_label = "helped" if quality_score >= 0.65 and cognitive_score >= 0.55 else "unclear"
                if quality_score < 0.4 or reference_score < 0.4:
                    effect_label = "hurt"
                effect_key = f"{target}:{effect_label}"
                target_effect_counts[effect_key] = target_effect_counts.get(effect_key, 0) + 1
            entries.append(
                {
                    "title": str(outcome.get("title") or "").strip(),
                    "quality_score": round(quality_score, 4),
                    "cognitive_alignment_score": round(cognitive_score, 4),
                    "reference_alignment_score": round(reference_score, 4),
                    "self_iteration_target": target or None,
                }
            )
            if len(entries) >= 6:
                break

        if not entries:
            return {
                "available": False,
                "summary": "No post-task effect memory is available yet.",
            }

        def _avg(values: List[float]) -> float:
            if not values:
                return 0.0
            return self._clamp01(sum(values) / len(values))

        average_quality_score = _avg(quality_scores)
        average_cognitive_alignment_score = _avg(cognitive_scores)
        average_reference_alignment_score = _avg(reference_scores)
        effect_direction = "mixed"
        if (
            average_quality_score >= 0.65
            and average_cognitive_alignment_score >= 0.55
            and average_reference_alignment_score >= 0.55
        ):
            effect_direction = "improving"
        elif (
            average_quality_score < 0.4
            or average_cognitive_alignment_score < 0.4
            or average_reference_alignment_score < 0.4
        ):
            effect_direction = "degrading"
        dominant_target_effect = ""
        if target_effect_counts:
            dominant_target_effect = sorted(
                target_effect_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[0][0]
        return {
            "available": True,
            "entries": entries,
            "effect_direction": effect_direction,
            "average_quality_score": round(average_quality_score, 4),
            "average_cognitive_alignment_score": round(
                average_cognitive_alignment_score,
                4,
            ),
            "average_reference_alignment_score": round(
                average_reference_alignment_score,
                4,
            ),
            "dominant_target_effect": dominant_target_effect or None,
            "summary": (
                "Recent post-task effects appear "
                f"{effect_direction}; avg_quality={average_quality_score:.2f}; "
                f"avg_cognitive_alignment={average_cognitive_alignment_score:.2f}; "
                f"avg_reference_alignment={average_reference_alignment_score:.2f}."
            ),
        }

    def _build_self_iteration_hypotheses(
        self,
        *,
        self_model_snapshot: Dict[str, Any],
        evidence_credibility_summary: Dict[str, Any],
        task_type_priors: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
        cognitive_assessment_memory: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        switch_self_regulation_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
        grounding_focus: Dict[str, Any],
    ) -> Dict[str, Any]:
        readiness = dict(self_model_snapshot.get("readiness") or {})
        self_gaps = [
            str(item).strip()
            for item in list(self_model_snapshot.get("self_understanding_gaps") or [])[:6]
            if str(item).strip()
        ]
        weak_channels = [
            str(item).strip()
            for item in list(evidence_credibility_summary.get("weak_or_missing_channels") or [])[:6]
            if str(item).strip()
        ]
        grounding_gaps = [
            str(item).strip()
            for item in list(grounding_focus.get("grounding_gaps") or [])[:6]
            if str(item).strip()
        ]
        top_priority_task_type = str(
            task_type_priors.get("top_priority_task_type") or ""
        ).strip()
        top_priority_score = self._clamp01(task_type_priors.get("top_priority_score") or 0.0)
        readiness_score = self._clamp01(
            readiness.get("self_iteration_readiness_score") or 0.0
        )
        reference_alignment_score = self._clamp01(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        dominant_constraint = str(
            cognitive_assessment_memory.get("dominant_constraint")
            or (self_model_snapshot.get("current_state") or {}).get("dominant_constraint")
            or ""
        ).strip()
        common_why_not_improvement_now = [
            str(item).strip()
            for item in list(
                cognitive_assessment_memory.get("common_why_not_improvement_now") or []
            )[:4]
            if str(item).strip()
        ]
        trend_state = str(self_iteration_trend_memory.get("trend_state") or "").strip().lower()
        dominant_trend_target = str(
            self_iteration_trend_memory.get("dominant_target") or ""
        ).strip()
        preferred_switch_bias = str(
            switch_self_regulation_memory.get("preferred_switch_bias") or ""
        ).strip()
        effect_direction = str(
            post_task_effect_memory.get("effect_direction") or ""
        ).strip()
        hypotheses: List[Dict[str, Any]] = []

        if grounding_gaps or reference_alignment_score < 0.65:
            hypotheses.append(
                {
                    "target_domain": "grounding",
                    "hypothesis": (
                        "repair evidence-to-agenda grounding before attempting aggressive self-iteration"
                    ),
                    "priority": self._clamp01(
                        0.72
                        + min(len(grounding_gaps), 4) * 0.05
                        + max(0.0, 0.65 - reference_alignment_score) * 0.35
                    ),
                    "evidence": grounding_gaps[:4]
                    + ([f"dominant_constraint:{dominant_constraint}"] if dominant_constraint else []),
                    "suggested_task_types": ["observation", "review", "learning"],
                }
            )
        if self_gaps or readiness_score < 0.6:
            hypotheses.append(
                {
                    "target_domain": "self_model",
                    "hypothesis": (
                        "expand self-understanding before escalating to irreversible body or strategy changes"
                    ),
                    "priority": self._clamp01(
                        0.66
                        + min(len(self_gaps), 4) * 0.05
                        + max(0.0, 0.6 - readiness_score) * 0.4
                    ),
                    "evidence": self_gaps[:4],
                    "suggested_task_types": ["observation", "learning", "review"],
                }
            )
        if any(
            item in {"research", "external_research", "recent_learning", "learning_trace"}
            or "research" in item
            for item in weak_channels
        ):
            hypotheses.append(
                {
                    "target_domain": "frontier_research",
                    "hypothesis": (
                        "refresh external and learning evidence so self-iteration remains tied to current knowledge"
                    ),
                    "priority": self._clamp01(0.52 + min(len(weak_channels), 4) * 0.04),
                    "evidence": weak_channels[:4],
                    "suggested_task_types": ["learning", "observation"],
                }
            )
        if drift_state in {"drifting", "correcting"}:
            hypotheses.append(
                {
                    "target_domain": "task_selection",
                    "hypothesis": (
                        "repair proposal selection logic and explanation quality before broadening autonomous action"
                    ),
                    "priority": self._clamp01(
                        0.58
                        + (0.16 if drift_state == "drifting" else 0.08)
                        + (0.08 if top_priority_task_type in {"observation", "review"} else 0.0)
                    ),
                    "evidence": [
                        f"proposal_drift:{drift_state}",
                        *[
                            str(item).strip()
                            for item in list(
                                proposal_drift_memory.get("common_priority_basis") or []
                            )[:3]
                            if str(item).strip()
                        ],
                    ],
                    "suggested_task_types": ["review", "observation"],
                }
            )
        if common_why_not_improvement_now:
            hypotheses.append(
                {
                    "target_domain": "improvement_readiness",
                    "hypothesis": (
                        "clarify why improvement is being deferred so future self-iteration can become more decisive"
                    ),
                    "priority": self._clamp01(
                        0.46 + min(len(common_why_not_improvement_now), 4) * 0.04
                    ),
                    "evidence": common_why_not_improvement_now[:4],
                    "suggested_task_types": ["review", "learning"],
                }
            )
        if dominant_trend_target:
            hypotheses.append(
                {
                    "target_domain": dominant_trend_target,
                    "hypothesis": (
                        "respect the recent self-iteration trend unless new evidence strongly justifies a domain switch"
                    ),
                    "priority": self._clamp01(
                        0.44
                        + (0.1 if trend_state == "locked" else 0.04)
                        + (0.08 if dominant_trend_target == top_priority_task_type else 0.0)
                    ),
                    "evidence": [
                        f"trend_state:{trend_state or 'unknown'}",
                        f"dominant_target:{dominant_trend_target}",
                    ],
                    "suggested_task_types": [top_priority_task_type or "review", "observation"],
                }
            )
        if preferred_switch_bias in {"stay", "switch"}:
            hypotheses.append(
                {
                    "target_domain": "switch_regulation",
                    "hypothesis": (
                        "calibrate stay-versus-switch cadence based on recent outcome quality instead of changing direction reflexively"
                    ),
                    "priority": self._clamp01(
                        0.4
                        + (
                            abs(
                                float(
                                    switch_self_regulation_memory.get("average_switch_quality") or 0.0
                                )
                                - float(
                                    switch_self_regulation_memory.get("average_stay_quality") or 0.0
                                )
                            )
                            * 0.3
                        )
                    ),
                    "evidence": [
                        f"preferred_switch_bias:{preferred_switch_bias}",
                        f"switch_effectiveness:{str(switch_self_regulation_memory.get('switch_effectiveness') or 'unknown')}",
                        f"stay_effectiveness:{str(switch_self_regulation_memory.get('stay_effectiveness') or 'unknown')}",
                    ],
                    "suggested_task_types": ["review", "observation"],
                }
            )
        if effect_direction in {"improving", "degrading", "mixed"}:
            hypotheses.append(
                {
                    "target_domain": "task_effectiveness",
                    "hypothesis": (
                        "prefer tasks that measurably improve reference alignment and cognitive alignment, not just plausible-looking tasks"
                    ),
                    "priority": self._clamp01(
                        0.42
                        + (
                            0.16
                            if effect_direction == "degrading"
                            else (0.08 if effect_direction == "mixed" else 0.02)
                        )
                    ),
                    "evidence": [
                        f"post_task_effect:{effect_direction}",
                        f"dominant_target_effect:{str(post_task_effect_memory.get('dominant_target_effect') or 'unknown')}",
                    ],
                    "suggested_task_types": ["review", "observation", "learning"],
                }
            )

        normalized_hypotheses = [
            dict(item)
            for item in sorted(
                hypotheses,
                key=lambda row: (
                    -float(row.get("priority") or 0.0),
                    str(row.get("target_domain") or "").strip(),
                ),
            )
            if isinstance(item, dict) and str(item.get("hypothesis") or "").strip()
        ]
        if not normalized_hypotheses:
            return {
                "available": False,
                "hypotheses": [],
                "summary": "No explicit self-iteration hypotheses are available yet.",
            }

        top_hypothesis = normalized_hypotheses[0]
        return {
            "available": True,
            "dominant_hypothesis": str(top_hypothesis.get("hypothesis") or "").strip(),
            "top_target_domain": str(top_hypothesis.get("target_domain") or "").strip(),
            "hypotheses": normalized_hypotheses[:4],
            "summary": (
                "Current self-iteration should likely focus on "
                f"{str(top_hypothesis.get('target_domain') or 'unknown').strip() or 'unknown'}; "
                f"dominant hypothesis={str(top_hypothesis.get('hypothesis') or '').strip() or 'unknown'}; "
                f"current compatible projection bias={top_priority_task_type or 'unknown'} ({top_priority_score:.2f})."
            ),
        }

    def _build_proposal_drift_memory(self, drive_context: Dict[str, Any]) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        recent_entries: List[Dict[str, Any]] = []
        quality_counts = {"strong": 0, "partial": 0, "weak": 0}
        posture_alignment_counts: Dict[str, int] = {}
        priority_basis_counts: Dict[str, int] = {}
        posture_conflict_reason_counts: Dict[str, int] = {}
        missing_posture_alignment_count = 0
        missing_priority_basis_count = 0
        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            cognitive_alignment = outcome.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                continue
            quality = str(cognitive_alignment.get("quality") or "partial").strip().lower() or "partial"
            if quality not in quality_counts:
                quality = "partial"
            quality_counts[quality] += 1
            posture_alignment = outcome.get("llm_posture_alignment")
            if not isinstance(posture_alignment, list):
                posture_alignment = metadata.get("llm_posture_alignment")
            if not isinstance(posture_alignment, list):
                posture_alignment = evidence.get("llm_posture_alignment")
            normalized_posture_alignment = [
                str(item).strip()
                for item in list(posture_alignment or [])[:3]
                if str(item).strip()
            ]
            if normalized_posture_alignment:
                for item in normalized_posture_alignment:
                    posture_alignment_counts[item] = posture_alignment_counts.get(item, 0) + 1
            else:
                missing_posture_alignment_count += 1
            priority_basis = outcome.get("llm_priority_basis")
            if not isinstance(priority_basis, list):
                priority_basis = metadata.get("llm_priority_basis")
            if not isinstance(priority_basis, list):
                priority_basis = evidence.get("llm_priority_basis")
            normalized_priority_basis = [
                str(item).strip()
                for item in list(priority_basis or [])[:3]
                if str(item).strip()
            ]
            if normalized_priority_basis:
                for item in normalized_priority_basis:
                    priority_basis_counts[item] = priority_basis_counts.get(item, 0) + 1
            else:
                missing_priority_basis_count += 1
            conflict_reasons = [
                str(item).strip()
                for item in list(cognitive_alignment.get("reasons") or [])[:4]
                if str(item).strip()
                and (
                    "posture" in str(item).strip().lower()
                    or "priority" in str(item).strip().lower()
                    or "task_type" in str(item).strip().lower()
                )
            ]
            for item in conflict_reasons:
                posture_conflict_reason_counts[item] = posture_conflict_reason_counts.get(item, 0) + 1
            recent_entries.append(
                {
                    "title": str(outcome.get("title") or "").strip(),
                    "quality": quality,
                    "score": float(cognitive_alignment.get("score") or 0.0),
                    "top_priority_task_type": str(
                        cognitive_alignment.get("top_priority_task_type") or ""
                    ).strip(),
                    "reasons": [
                        str(item).strip()
                        for item in list(cognitive_alignment.get("reasons") or [])[:4]
                        if str(item).strip()
                    ],
                    "llm_posture_alignment": normalized_posture_alignment,
                    "llm_priority_basis": normalized_priority_basis,
                }
            )
            if len(recent_entries) >= 4:
                break

        if not recent_entries:
            return {
                "available": False,
                "recent_entries": [],
                "summary": "No recent proposal-drift memory is available yet.",
            }

        avg_score = sum(entry["score"] for entry in recent_entries) / len(recent_entries)
        weak_or_partial = quality_counts["weak"] + quality_counts["partial"]
        drift_state = "stable"
        if quality_counts["weak"] >= 2 or avg_score < 0.45:
            drift_state = "drifting"
        elif (quality_counts["weak"] >= 1 and quality_counts["strong"] >= 1) or weak_or_partial >= 2:
            drift_state = "correcting"
        common_posture_alignment = [
            item
            for item, _count in sorted(
                posture_alignment_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_priority_basis = [
            item
            for item, _count in sorted(
                priority_basis_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        dominant_posture_conflict_reason = None
        if posture_conflict_reason_counts:
            dominant_posture_conflict_reason = max(
                posture_conflict_reason_counts.items(),
                key=lambda pair: (pair[1], pair[0]),
            )[0]
        posture_alignment_health = "strong"
        if missing_posture_alignment_count >= 2:
            posture_alignment_health = "missing"
        elif quality_counts["weak"] >= 1 or dominant_posture_conflict_reason:
            posture_alignment_health = "inconsistent"
        priority_basis_health = "strong"
        if missing_priority_basis_count >= 2:
            priority_basis_health = "missing"
        elif quality_counts["weak"] >= 1 or not common_priority_basis:
            priority_basis_health = "inconsistent"
        return {
            "available": True,
            "recent_entries": recent_entries,
            "average_score": round(self._clamp01(avg_score), 4),
            "quality_counts": quality_counts,
            "drift_state": drift_state,
            "common_posture_alignment": common_posture_alignment,
            "common_priority_basis": common_priority_basis,
            "missing_posture_alignment_count": missing_posture_alignment_count,
            "missing_priority_basis_count": missing_priority_basis_count,
            "dominant_posture_conflict_reason": dominant_posture_conflict_reason,
            "posture_alignment_health": posture_alignment_health,
            "priority_basis_health": priority_basis_health,
            "summary": (
                f"Recent proposal alignment is {drift_state}; average cognitive alignment "
                f"score is {self._clamp01(avg_score):.2f}."
            ),
        }

    def _build_self_model_snapshot(
        self,
        *,
        perception: Dict[str, Any],
        world_model: Dict[str, Any],
        reflection: Dict[str, Any],
        adaptive_policy: Dict[str, Any],
        shell_body_profile: Dict[str, Any],
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        recent_reference_alignment: Dict[str, Any],
        evidence_graph: Dict[str, Any],
        agenda_graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        body_profile_status = str(shell_body_profile.get("profile_status") or "unknown").strip()
        learning_state = str(reflection.get("learning_yield_state") or "unknown").strip()
        dominant_constraint = str(reflection.get("dominant_constraint") or "unknown").strip()
        preferred_focus = str(adaptive_policy.get("preferred_focus") or "observation").strip()
        queue_health = str(world_model.get("queue_health") or "unknown").strip()
        alignment_available = bool(recent_reference_alignment.get("available"))
        alignment_score = self._clamp01(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        weak_alignment_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0)
        )
        research_freshness = self._research_freshness_hint(external_research_evidence)
        top_topics = [
            str(item.get("topic") or "").strip()
            for item in list(evidence_graph.get("nodes") or [])[:4]
            if isinstance(item, dict) and str(item.get("topic") or "").strip()
        ]
        unresolved_gaps = [
            str(item.get("gap") or "").strip()
            for item in list(agenda_graph.get("unresolved_gaps") or [])[:4]
            if isinstance(item, dict) and str(item.get("gap") or "").strip()
        ]
        current_directions = [
            str(item.get("direction") or "").strip()
            for item in list(agenda_graph.get("recommended_directions") or [])[:4]
            if isinstance(item, dict) and str(item.get("direction") or "").strip()
        ]
        self_understanding_gaps: List[str] = []
        if body_profile_status != "ready":
            self_understanding_gaps.append("body_profile_incomplete")
        if not recent_learning_evidence:
            self_understanding_gaps.append("missing_recent_learning_trace")
        elif learning_state in {"weak", "low_yield", "unknown"}:
            self_understanding_gaps.append("recent_learning_not_yet_reliable")
        if not external_research_evidence:
            self_understanding_gaps.append("missing_external_research_support")
        elif research_freshness == "stale":
            self_understanding_gaps.append("external_research_is_stale")
        if alignment_available and weak_alignment_count > 0:
            self_understanding_gaps.append("reference_alignment_is_unstable")

        readiness_factors = {
            "body_structure": body_profile_status == "ready",
            "recent_learning": bool(recent_learning_evidence),
            "external_research": bool(external_research_evidence),
            "reference_alignment_feedback": alignment_available,
        }
        readiness_score = (
            (1.0 if readiness_factors["body_structure"] else 0.0) * 0.28
            + (1.0 if readiness_factors["recent_learning"] else 0.0) * 0.24
            + (1.0 if readiness_factors["external_research"] else 0.0) * 0.16
            + alignment_score * 0.16
            + self._clamp01(world_model.get("self_confidence") or 0.0) * 0.16
        )

        summary = (
            f"Current self model sees dominant constraint={dominant_constraint}, "
            f"preferred focus={preferred_focus}, body status={body_profile_status}, "
            f"learning state={learning_state}, queue health={queue_health}."
        )
        if self_understanding_gaps:
            summary += " Active self-understanding gaps: " + ", ".join(self_understanding_gaps[:4]) + "."

        return {
            "identity_view": {
                "role": "endogenous_supervisory_core",
                "responsibility": "self-understanding before self-iteration",
                "execution_scope": "governance_only",
            },
            "current_state": {
                "user_mode": perception.get("user_mode"),
                "system_posture": perception.get("system_posture"),
                "dominant_constraint": dominant_constraint,
                "preferred_focus": preferred_focus,
                "queue_health": queue_health,
                "learning_yield_state": learning_state,
                "body_profile_status": body_profile_status,
                "research_freshness": research_freshness,
            },
            "readiness": {
                "self_iteration_readiness_score": round(self._clamp01(readiness_score), 4),
                "autonomy_readiness": round(
                    self._clamp01(reflection.get("autonomy_readiness") or 0.0),
                    4,
                ),
                "readiness_factors": readiness_factors,
            },
            "self_understanding_gaps": self_understanding_gaps,
            "reference_alignment_feedback": {
                "available": alignment_available,
                "average_alignment_score": round(alignment_score, 4),
                "weak_or_partial_count": weak_alignment_count,
                "summary": recent_reference_alignment.get("summary"),
            },
            "current_topics": top_topics,
            "unresolved_gaps": unresolved_gaps,
            "current_directions": current_directions,
            "summary": summary,
        }

    def _build_evidence_credibility_summary(
        self,
        *,
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        shell_body_profile: Dict[str, Any],
        evidence_channels: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        learning_confidence = self._channel_confidence_from_learning(recent_learning_evidence)
        research_confidence = self._channel_confidence_from_research(external_research_evidence)
        body_confidence = self._channel_confidence_from_body(shell_body_profile)
        channel_rows = [
            {
                "channel": "recent_learning",
                "confidence": round(self._clamp01(learning_confidence), 4),
                "evidence_strength": self._channel_strength_from_learning(recent_learning_evidence),
                "item_count": len(recent_learning_evidence),
            },
            {
                "channel": "external_research",
                "confidence": round(self._clamp01(research_confidence), 4),
                "evidence_strength": self._channel_strength_from_research(external_research_evidence),
                "item_count": len(external_research_evidence),
            },
            {
                "channel": "shell_body_profile",
                "confidence": round(self._clamp01(body_confidence), 4),
                "evidence_strength": (
                    "strong" if str(shell_body_profile.get("profile_status") or "") == "ready" else "weak"
                ),
                "item_count": 1 if shell_body_profile else 0,
            },
        ]
        high_credibility_channels = [
            row["channel"]
            for row in channel_rows
            if row["confidence"] >= 0.72 and row["evidence_strength"] in {"moderate", "strong"}
        ]
        weak_or_missing_channels = [
            row["channel"]
            for row in channel_rows
            if row["confidence"] < 0.45 or row["item_count"] <= 0 or row["evidence_strength"] == "weak"
        ]
        conflict_flags: List[str] = []
        for channel in list(evidence_channels.get("channels") or []):
            if not isinstance(channel, dict):
                continue
            for flag in list(channel.get("conflict_flags") or []):
                text = str(flag).strip()
                if text and text not in conflict_flags:
                    conflict_flags.append(text)
        alignment_score = self._clamp01(recent_reference_alignment.get("average_alignment_score") or 0.0)
        summary = (
            f"High-credibility channels: {', '.join(high_credibility_channels) if high_credibility_channels else 'none'}. "
            f"Weak or missing channels: {', '.join(weak_or_missing_channels) if weak_or_missing_channels else 'none'}. "
            f"Reference alignment score={alignment_score:.2f}."
        )
        return {
            "channels": channel_rows,
            "high_credibility_channels": high_credibility_channels,
            "weak_or_missing_channels": weak_or_missing_channels,
            "conflict_flags": conflict_flags[:8],
            "reference_alignment_score": round(alignment_score, 4),
            "summary": summary,
        }

    def _build_task_type_priors(
        self,
        *,
        reflection: Dict[str, Any],
        adaptive_policy: Dict[str, Any],
        self_model_snapshot: Dict[str, Any],
        evidence_credibility_summary: Dict[str, Any],
        agenda_graph: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
    ) -> Dict[str, Any]:
        preferred_focus = str(adaptive_policy.get("preferred_focus") or "observation").strip()
        dominant_constraint = str(reflection.get("dominant_constraint") or "unknown").strip()
        self_gaps = list(self_model_snapshot.get("self_understanding_gaps") or [])
        weak_channels = list(evidence_credibility_summary.get("weak_or_missing_channels") or [])
        high_channels = list(evidence_credibility_summary.get("high_credibility_channels") or [])
        unresolved_gaps = [
            str(item.get("gap") or "").strip()
            for item in list(agenda_graph.get("unresolved_gaps") or [])[:4]
            if isinstance(item, dict) and str(item.get("gap") or "").strip()
        ]
        alignment_score = self._clamp01(recent_reference_alignment.get("average_alignment_score") or 0.0)
        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        drift_average_score = self._clamp01(proposal_drift_memory.get("average_score") or 0.0)

        observation_score = 0.22
        review_score = 0.2
        learning_score = 0.24
        maintenance_score = 0.12
        improvement_score = 0.1

        if preferred_focus == "observation":
            observation_score += 0.22
            review_score += 0.08
        if preferred_focus == "truthfulness":
            review_score += 0.2
            observation_score += 0.06
        if preferred_focus == "learning_expansion":
            learning_score += 0.18
        if preferred_focus == "memory_continuity":
            maintenance_score += 0.18
        if preferred_focus == "body_growth":
            improvement_score += 0.16

        if dominant_constraint in {"weak_learning_yield", "historical_underdelivery"}:
            observation_score += 0.12
            review_score += 0.1
        if dominant_constraint == "queue_blockage":
            review_score += 0.14
            maintenance_score += 0.08

        if self_gaps:
            observation_score += min(len(self_gaps), 4) * 0.06
            learning_score += 0.08
            improvement_score -= 0.05
        if weak_channels:
            observation_score += min(len(weak_channels), 3) * 0.05
            review_score += 0.07
            improvement_score -= 0.06
        if high_channels:
            learning_score += 0.06
            improvement_score += 0.04
        if alignment_score < 0.7:
            observation_score += 0.08
            review_score += 0.08
            improvement_score -= 0.05
        if drift_state == "drifting":
            observation_score += 0.14
            review_score += 0.14
            learning_score -= 0.05
            improvement_score -= 0.08
        elif drift_state == "correcting":
            observation_score += 0.08
            review_score += 0.06
            improvement_score -= 0.04
        if drift_average_score < 0.5 and proposal_drift_memory.get("available"):
            observation_score += 0.06
            review_score += 0.05
            improvement_score -= 0.04
        if "prepare_body_growth" in unresolved_gaps and not self_gaps and alignment_score >= 0.7:
            improvement_score += 0.1

        prior_rows = [
            {
                "task_type": "observation",
                "score": round(self._clamp01(observation_score), 4),
                "reasons": self._task_type_prior_reasons(
                    task_type="observation",
                    preferred_focus=preferred_focus,
                    dominant_constraint=dominant_constraint,
                    self_gaps=self_gaps,
                    weak_channels=weak_channels,
                    unresolved_gaps=unresolved_gaps,
                    alignment_score=alignment_score,
                    drift_state=drift_state,
                ),
            },
            {
                "task_type": "review",
                "score": round(self._clamp01(review_score), 4),
                "reasons": self._task_type_prior_reasons(
                    task_type="review",
                    preferred_focus=preferred_focus,
                    dominant_constraint=dominant_constraint,
                    self_gaps=self_gaps,
                    weak_channels=weak_channels,
                    unresolved_gaps=unresolved_gaps,
                    alignment_score=alignment_score,
                    drift_state=drift_state,
                ),
            },
            {
                "task_type": "learning",
                "score": round(self._clamp01(learning_score), 4),
                "reasons": self._task_type_prior_reasons(
                    task_type="learning",
                    preferred_focus=preferred_focus,
                    dominant_constraint=dominant_constraint,
                    self_gaps=self_gaps,
                    weak_channels=weak_channels,
                    unresolved_gaps=unresolved_gaps,
                    alignment_score=alignment_score,
                    drift_state=drift_state,
                ),
            },
            {
                "task_type": "maintenance",
                "score": round(self._clamp01(maintenance_score), 4),
                "reasons": self._task_type_prior_reasons(
                    task_type="maintenance",
                    preferred_focus=preferred_focus,
                    dominant_constraint=dominant_constraint,
                    self_gaps=self_gaps,
                    weak_channels=weak_channels,
                    unresolved_gaps=unresolved_gaps,
                    alignment_score=alignment_score,
                    drift_state=drift_state,
                ),
            },
            {
                "task_type": "improvement",
                "score": round(self._clamp01(improvement_score), 4),
                "reasons": self._task_type_prior_reasons(
                    task_type="improvement",
                    preferred_focus=preferred_focus,
                    dominant_constraint=dominant_constraint,
                    self_gaps=self_gaps,
                    weak_channels=weak_channels,
                    unresolved_gaps=unresolved_gaps,
                    alignment_score=alignment_score,
                    drift_state=drift_state,
                ),
            },
        ]
        prior_rows.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
        top = prior_rows[0] if prior_rows else {"task_type": "observation", "score": 0.0}
        return {
            "top_priority_task_type": top["task_type"],
            "top_priority_score": top["score"],
            "priors": prior_rows,
            "drift_state": drift_state or "stable",
            "summary": (
                f"Program-side governance priors currently lean toward {top['task_type']} "
                f"as the safest compatible task projection (score={float(top['score']):.2f}); "
                f"proposal drift state is {drift_state or 'stable'}."
            ),
        }

    def _task_type_prior_reasons(
        self,
        *,
        task_type: str,
        preferred_focus: str,
        dominant_constraint: str,
        self_gaps: List[str],
        weak_channels: List[str],
        unresolved_gaps: List[str],
        alignment_score: float,
        drift_state: str,
    ) -> List[str]:
        reasons: List[str] = []
        if task_type == "observation":
            if self_gaps:
                reasons.append("self_understanding_gaps_require_more_observation")
            if weak_channels:
                reasons.append("weak_channels_make_direct_action_less_reliable")
            if alignment_score < 0.7:
                reasons.append("reference_alignment_is_not_yet_stable")
            if drift_state == "drifting":
                reasons.append("proposal_drift_requires_more_observation")
        elif task_type == "review":
            if dominant_constraint in {"queue_blockage", "historical_underdelivery"}:
                reasons.append("dominant_constraint_calls_for_review")
            if preferred_focus == "truthfulness":
                reasons.append("preferred_focus_is_truthfulness")
            if alignment_score < 0.7:
                reasons.append("review_can_repair_reference_drift")
            if drift_state in {"drifting", "correcting"}:
                reasons.append("review_can_help_correct_recent_proposal_drift")
        elif task_type == "learning":
            if preferred_focus == "learning_expansion":
                reasons.append("preferred_focus_is_learning_expansion")
            if "expand_learning_frontier" in unresolved_gaps:
                reasons.append("agenda_still_contains_learning_frontier_gap")
            if self_gaps:
                reasons.append("learning_can_reduce_self_model_gaps")
            if drift_state == "drifting":
                reasons.append("learning_should_wait_until_drift_stabilizes")
        elif task_type == "maintenance":
            if preferred_focus == "memory_continuity":
                reasons.append("preferred_focus_is_memory_continuity")
            if dominant_constraint == "queue_blockage":
                reasons.append("maintenance_can_reduce_backlog_pressure")
        elif task_type == "improvement":
            if "prepare_body_growth" in unresolved_gaps:
                reasons.append("agenda_contains_body_growth_gap")
            if alignment_score >= 0.7 and not self_gaps:
                reasons.append("evidence_is_stable_enough_for_guarded_improvement")
            if weak_channels:
                reasons.append("improvement_should_remain_guarded_under_weak_channels")
            if drift_state in {"drifting", "correcting"}:
                reasons.append("recent_proposal_drift_discourages_direct_improvement")
        if not reasons:
            reasons.append("baseline_program_prior")
        return reasons[:4]

    def _build_evidence_graph(
        self,
        *,
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        shell_body_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        evidence_items: List[Dict[str, Any]] = []
        evidence_items.extend(recent_learning_evidence[:5])
        evidence_items.extend(external_research_evidence[:8])
        if shell_body_profile:
            evidence_items.append(shell_body_profile)

        node_scores: Dict[str, Dict[str, Any]] = {}
        support_edges: List[Dict[str, Any]] = []
        contradiction_edges: List[Dict[str, Any]] = []

        for item in evidence_items:
            title = str(item.get("title") or item.get("slot_id") or "evidence_item").strip()
            confidence = self._clamp01(item.get("confidence_score") or item.get("source_reliability") or 0.4)
            for topic in list(item.get("supports") or []):
                topic_name = str(topic or "").strip()
                if not topic_name:
                    continue
                bucket = node_scores.setdefault(
                    topic_name,
                    {"support_count": 0, "contradict_count": 0, "confidence_sum": 0.0},
                )
                bucket["support_count"] += 1
                bucket["confidence_sum"] += confidence
                support_edges.append(
                    {
                        "from": title,
                        "to": topic_name,
                        "relation": "supports",
                        "weight": round(confidence, 4),
                    }
                )
            for topic in list(item.get("contradicts") or []):
                topic_name = str(topic or "").strip()
                if not topic_name:
                    continue
                bucket = node_scores.setdefault(
                    topic_name,
                    {"support_count": 0, "contradict_count": 0, "confidence_sum": 0.0},
                )
                bucket["contradict_count"] += 1
                bucket["confidence_sum"] += confidence
                contradiction_edges.append(
                    {
                        "from": title,
                        "to": topic_name,
                        "relation": "contradicts",
                        "weight": round(confidence, 4),
                    }
                )

        nodes: List[Dict[str, Any]] = []
        for topic_name, bucket in sorted(node_scores.items()):
            total = bucket["support_count"] + bucket["contradict_count"]
            avg_confidence = bucket["confidence_sum"] / total if total > 0 else 0.0
            nodes.append(
                {
                    "topic": topic_name,
                    "support_count": bucket["support_count"],
                    "contradict_count": bucket["contradict_count"],
                    "net_signal": bucket["support_count"] - bucket["contradict_count"],
                    "avg_confidence": round(self._clamp01(avg_confidence), 4),
                }
            )

        return {
            "node_count": len(nodes),
            "edge_count": len(support_edges) + len(contradiction_edges),
            "nodes": nodes[:16],
            "support_edges": support_edges[:10],
            "contradiction_edges": contradiction_edges[:8],
        }

    def _build_agenda_graph(
        self,
        *,
        deliberation_dict: Dict[str, Any],
        evidence_graph: Dict[str, Any],
    ) -> Dict[str, Any]:
        adaptive_policy = dict(deliberation_dict.get("adaptive_policy") or {})
        needs = [
            dict(item)
            for item in list(deliberation_dict.get("needs") or [])
            if isinstance(item, dict)
        ]
        intents = [
            dict(item)
            for item in list(deliberation_dict.get("intents") or [])
            if isinstance(item, dict)
        ]
        signals = [
            dict(item)
            for item in list(deliberation_dict.get("signals") or [])
            if isinstance(item, dict)
        ]
        evidence_nodes = [
            dict(item)
            for item in list(evidence_graph.get("nodes") or [])
            if isinstance(item, dict)
        ]

        focus = str(adaptive_policy.get("preferred_focus") or "").strip() or "observation"
        current_topics = [
            {
                "topic": str(node.get("topic") or "").strip(),
                "priority": round(
                    self._clamp01(
                        0.28
                        + max(0, float(node.get("net_signal") or 0.0)) * 0.18
                        + float(node.get("avg_confidence") or 0.0) * 0.42
                    ),
                    4,
                ),
                "status": (
                    "supported"
                    if float(node.get("net_signal") or 0.0) > 0
                    else "contested"
                    if float(node.get("contradict_count") or 0.0) > 0
                    else "emerging"
                ),
            }
            for node in evidence_nodes[:8]
            if str(node.get("topic") or "").strip()
        ]

        unresolved_gaps: List[Dict[str, Any]] = []
        for need in needs[:6]:
            need_type = str(need.get("need_type") or "").strip()
            if not need_type:
                continue
            unresolved_gaps.append(
                {
                    "gap": need_type,
                    "priority": round(self._clamp01(float(need.get("urgency") or 0.0) * 0.6 + float(need.get("severity") or 0.0) * 0.4), 4),
                    "rationale": str(need.get("rationale") or "").strip(),
                }
            )

        recommended_directions: List[Dict[str, Any]] = []
        for intent in intents[:6]:
            intent_type = str(intent.get("intent_type") or "").strip()
            if not intent_type:
                continue
            candidate_kind = intent.get("candidate_kind")
            recommended_directions.append(
                {
                    "direction": intent_type,
                    "priority": round(self._clamp01(float(intent.get("priority") or 0.0)), 4),
                    "candidate_kind": candidate_kind,
                    "task_type": self._task_type_for_candidate_kind(candidate_kind),
                    "target_horizon": intent.get("target_horizon"),
                }
            )

        active_signals = [
            {
                "signal": str(signal.get("signal_type") or "").strip(),
                "priority": round(self._clamp01(float(signal.get("priority") or 0.0)), 4),
                "message": str(signal.get("message") or "").strip(),
            }
            for signal in signals[:6]
            if str(signal.get("signal_type") or "").strip()
        ]

        relation_edges: List[Dict[str, Any]] = []
        gap_by_name = {
            str(item.get("gap") or "").strip(): item
            for item in unresolved_gaps
            if str(item.get("gap") or "").strip()
        }
        direction_by_name = {
            str(item.get("direction") or "").strip(): item
            for item in recommended_directions
            if str(item.get("direction") or "").strip()
        }

        for intent in intents[:8]:
            direction = str(intent.get("intent_type") or "").strip()
            if not direction:
                continue
            direction_meta = direction_by_name.get(direction)
            if direction_meta is None:
                continue
            for source_need in list(intent.get("source_needs") or []):
                gap_name = str(source_need or "").strip()
                gap_meta = gap_by_name.get(gap_name)
                if gap_meta is None:
                    continue
                relation_edges.append(
                    {
                        "from": gap_name,
                        "to": direction,
                        "relation": "elevates_direction",
                        "weight": round(
                            self._clamp01(
                                float(gap_meta.get("priority") or 0.0) * 0.55
                                + float(direction_meta.get("priority") or 0.0) * 0.45
                            ),
                            4,
                        ),
                    }
                )

        for signal in signals[:8]:
            signal_name = str(signal.get("signal_type") or "").strip()
            if not signal_name:
                continue
            related_intent = str(signal.get("related_intent") or "").strip()
            if related_intent and related_intent in direction_by_name:
                relation_edges.append(
                    {
                        "from": signal_name,
                        "to": related_intent,
                        "relation": "amplifies_direction",
                        "weight": round(self._clamp01(float(signal.get("priority") or 0.0)), 4),
                    }
                )
            if focus:
                relation_edges.append(
                    {
                        "from": signal_name,
                        "to": focus,
                        "relation": "shapes_focus",
                        "weight": round(self._clamp01(float(signal.get("priority") or 0.0) * 0.82), 4),
                    }
                )

        evidence_to_gap_edges: List[Dict[str, Any]] = []
        evidence_topics = {
            str(node.get("topic") or "").strip(): dict(node)
            for node in evidence_nodes
            if str(node.get("topic") or "").strip()
        }
        need_topic_map = {
            "stabilize_memory_continuity": "self_understanding",
            "repair_truthfulness": "external_research",
            "expand_learning_frontier": "external_research",
            "prepare_body_growth": "body_state",
            "clear_governance_backlog": "learning_trace",
            "observe_before_acting": "body_state",
        }
        for gap in unresolved_gaps:
            gap_name = str(gap.get("gap") or "").strip()
            topic_name = need_topic_map.get(gap_name)
            if not topic_name:
                continue
            topic_meta = evidence_topics.get(topic_name)
            if topic_meta is None:
                continue
            evidence_to_gap_edges.append(
                {
                    "from": topic_name,
                    "to": gap_name,
                    "relation": "supports_gap_assessment",
                    "weight": round(
                        self._clamp01(
                            float(topic_meta.get("avg_confidence") or 0.0) * 0.55
                            + float(gap.get("priority") or 0.0) * 0.45
                        ),
                        4,
                    ),
                    }
                )

        direction_task_links: List[Dict[str, Any]] = []
        for direction_meta in recommended_directions[:8]:
            direction = str(direction_meta.get("direction") or "").strip()
            candidate_kind = str(direction_meta.get("candidate_kind") or "").strip()
            task_type = str(direction_meta.get("task_type") or "").strip()
            if not direction or not candidate_kind or not task_type:
                continue
            direction_task_links.append(
                {
                    "from": direction,
                    "to_candidate_kind": candidate_kind,
                    "to_task_type": task_type,
                    "relation": "maps_to_task_shape",
                    "weight": round(self._clamp01(float(direction_meta.get("priority") or 0.0)), 4),
                }
            )

        return {
            "focus": focus,
            "focus_confidence": round(
                self._clamp01(
                    0.35
                    + float(adaptive_policy.get("candidate_throttle") or 0.0) * 0.18
                    + float(adaptive_policy.get("observation_bias") or 0.0) * 0.12
                ),
                4,
            ),
            "current_topics": current_topics,
            "unresolved_gaps": sorted(
                unresolved_gaps,
                key=lambda item: float(item.get("priority") or 0.0),
                reverse=True,
            )[:8],
            "recommended_directions": sorted(
                recommended_directions,
                key=lambda item: float(item.get("priority") or 0.0),
                reverse=True,
            )[:8],
            "active_signals": sorted(
                active_signals,
                key=lambda item: float(item.get("priority") or 0.0),
                reverse=True,
            )[:8],
            "evidence_to_gap_edges": evidence_to_gap_edges[:12],
            "relation_edges": relation_edges[:16],
            "direction_task_links": direction_task_links[:12],
        }

    def _research_freshness_hint(self, items: List[Dict[str, Any]]) -> str:
        published_tokens = [
            str(item.get("published_at") or "").strip()
            for item in items
            if str(item.get("published_at") or "").strip()
        ]
        if not published_tokens:
            return "unknown"
        latest_seen: Optional[datetime] = None
        for token in published_tokens:
            parsed = self._parse_timestamp(token)
            if parsed is not None and (latest_seen is None or parsed > latest_seen):
                latest_seen = parsed
        if latest_seen is None:
            return "unknown"
        age_days = max(0, (datetime.now(timezone.utc) - latest_seen).days)
        if age_days <= 14:
            return "fresh"
        if age_days <= 90:
            return "recent"
        return "stale"

    def _build_drive_context(self, idle_window: Dict[str, Any]) -> Dict[str, Any]:
        policy = dict(idle_window.get("endogenous_drive_policy") or {})
        drive_history = dict(idle_window.get("drive_history") or {})
        queued_tasks = list(idle_window.get("queued_tasks") or [])
        completed_learning_tasks = list(idle_window.get("completed_learning_tasks") or [])

        recent_learning_titles = [
            str(task.get("title") or "").strip()
            for task in completed_learning_tasks
            if str(task.get("title") or "").strip()
        ]
        queued_learning_titles = []
        queued_body_improvement_titles = []
        signatures: list[set[str]] = []
        active_queue_tasks: list[Dict[str, Any]] = []
        active_queue_by_governance: dict[str, int] = {}
        active_queue_by_family: dict[str, int] = {}
        active_queue_by_execution_kind: dict[str, int] = {}
        stale_queue_count = 0
        pending_review_count = 0
        now = datetime.now(timezone.utc)

        for title in recent_learning_titles:
            signatures.append(self._topic_signature(title))

        for task in queued_tasks:
            title = str(task.get("title") or "").strip()
            if not title:
                continue
            status = str(task.get("status") or "").strip().lower()
            execution_kind = str(task.get("execution_kind") or "").strip().lower()
            governance_task_type = str(task.get("governance_task_type") or "").strip().lower()
            task_family = str(task.get("task_family") or "").strip().lower()
            if task_family == "self_learning" and status not in {"completed", "failed", "cancelled"}:
                queued_learning_titles.append(title)
                signatures.append(self._topic_signature(title))
            if execution_kind == "body_improvement":
                queued_body_improvement_titles.append(title)
            if status not in _TERMINAL_QUEUE_STATUSES:
                active_queue_tasks.append(task)
                if governance_task_type:
                    active_queue_by_governance[governance_task_type] = (
                        active_queue_by_governance.get(governance_task_type, 0) + 1
                    )
                if task_family:
                    active_queue_by_family[task_family] = (
                        active_queue_by_family.get(task_family, 0) + 1
                    )
                if execution_kind:
                    active_queue_by_execution_kind[execution_kind] = (
                        active_queue_by_execution_kind.get(execution_kind, 0) + 1
                    )
                if status in _REVIEW_BACKLOG_STATUSES:
                    pending_review_count += 1
                updated_at = self._parse_timestamp(task.get("updated_at") or task.get("created_at"))
                if updated_at is not None and now - updated_at >= timedelta(hours=24):
                    stale_queue_count += 1

        return {
            "policy": policy,
            "drive_history": {
                "judgements": [
                    dict(item)
                    for item in list(drive_history.get("judgements") or [])
                    if isinstance(item, dict)
                ],
                "outcomes": [
                    dict(item)
                    for item in list(drive_history.get("outcomes") or [])
                    if isinstance(item, dict)
                ],
                "strategy_memory": self._normalize_strategy_memory(
                    drive_history.get("strategy_memory")
                ),
            },
            "queued_tasks": queued_tasks,
            "completed_learning_tasks": completed_learning_tasks,
            "recent_learning_titles": recent_learning_titles,
            "queued_learning_titles": queued_learning_titles,
            "queued_body_improvement_titles": queued_body_improvement_titles,
            "recent_learning_signatures": signatures,
            "active_queue_count": len(active_queue_tasks),
            "active_queue_by_governance": active_queue_by_governance,
            "active_queue_by_family": active_queue_by_family,
            "active_queue_by_execution_kind": active_queue_by_execution_kind,
            "stale_queue_count": stale_queue_count,
            "pending_review_count": pending_review_count,
        }

    @staticmethod
    def _clamp01(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0

    def _summarize_historical_pressure(
        self,
        *,
        recent_historical_outcomes: List[Dict[str, Any]],
        recent_self_learning_outcomes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        scoped_outcomes = list(recent_historical_outcomes)
        scope = "global"
        if len(recent_self_learning_outcomes) >= 3:
            scope = "self_learning"
            scoped_outcomes = list(recent_self_learning_outcomes)

        completed = 0
        failed = 0
        blocked = 0
        for item in scoped_outcomes:
            status = str(item.get("status") or "").strip().lower()
            if status == "completed":
                completed += 1
            elif status in {"failed", "cancelled"}:
                failed += 1
            elif status in {"approved", "deferred", "paused", "awaiting_review", "retry"}:
                blocked += 1
        total = completed + failed + blocked
        success_ratio = completed / total if total > 0 else 0.5
        drag_ratio = (failed + blocked) / total if total > 0 else 0.0
        has_temporal_markers = any(
            item.get("recorded_at")
            or item.get("completed_at")
            or item.get("updated_at")
            or item.get("created_at")
            for item in recent_self_learning_outcomes
        )

        def _status_counts(window: List[Dict[str, Any]]) -> tuple[int, int]:
            drag_count = 0
            completed_count = 0
            for item in window:
                status = str(item.get("status") or "").strip().lower()
                if status == "completed":
                    completed_count += 1
                elif status in {
                    "failed",
                    "cancelled",
                    "approved",
                    "deferred",
                    "paused",
                    "awaiting_review",
                    "retry",
                }:
                    drag_count += 1
            return drag_count, completed_count

        relapse_drag_count = 0
        relapse_drag_ratio = 0.0
        relapse_windows = [
            (
                list(recent_self_learning_outcomes[:3]),
                list(recent_self_learning_outcomes[3:6]),
            ),
            (
                list(recent_self_learning_outcomes[-3:]),
                list(recent_self_learning_outcomes[-6:-3]),
            ),
        ]
        for relapse_window, recovery_context in relapse_windows:
            if len(relapse_window) < 3:
                continue
            drag_count, completed_count = _status_counts(relapse_window)
            _, recovery_completed_count = _status_counts(recovery_context)
            if (
                drag_count >= 2
                and completed_count >= 1
                and recovery_completed_count >= 1
            ):
                ratio = drag_count / len(relapse_window)
                if ratio > relapse_drag_ratio:
                    relapse_drag_ratio = ratio
                    relapse_drag_count = drag_count

        underdelivery_active = (
            total >= 3
            and (
                (drag_ratio >= 0.6 and success_ratio <= 0.34)
                or (
                    len(recent_self_learning_outcomes) >= 5
                    and relapse_drag_count >= 2
                    and relapse_drag_ratio >= 0.66
                )
                or (
                    not has_temporal_markers
                    and len(recent_self_learning_outcomes) >= 7
                    and completed >= 3
                    and drag_ratio >= 0.6
                )
            )
        )

        return {
            "scope": scope,
            "scoped_outcomes": scoped_outcomes,
            "total": total,
            "success_ratio": success_ratio,
            "drag_ratio": drag_ratio,
            "has_temporal_markers": has_temporal_markers,
            "recent_relapse_drag_count": relapse_drag_count,
            "recent_relapse_drag_ratio": relapse_drag_ratio,
            "underdelivery_active": underdelivery_active,
        }

    def _normalize_historical_outcomes(
        self,
        outcomes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        indexed_rows: List[tuple[int, datetime, Dict[str, Any]]] = []
        fallback_rows: List[tuple[int, Dict[str, Any]]] = []
        for index, item in enumerate(outcomes):
            row = dict(item)
            parsed = self._parse_timestamp(
                row.get("recorded_at")
                or row.get("completed_at")
                or row.get("updated_at")
                or row.get("created_at")
            )
            if parsed is None:
                fallback_rows.append((index, row))
            else:
                indexed_rows.append((index, parsed, row))
        if not indexed_rows:
            return [row for _, row in fallback_rows]
        indexed_rows.sort(key=lambda item: (item[1], -item[0]), reverse=True)
        ordered = [row for _, _, row in indexed_rows]
        ordered.extend(row for _, row in fallback_rows)
        return ordered

    def _normalize_strategy_memory(self, raw: Any) -> Dict[str, Any]:
        source = dict(raw or {}) if isinstance(raw, dict) else {}
        raw_focus_stats = source.get("focus_stats")
        focus_stats: Dict[str, Dict[str, int]] = {}
        contextual_focus_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        agenda_topic_stats: Dict[str, Dict[str, Any]] = {}
        observation_target_stats: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw_focus_stats, dict):
            for focus, stats in raw_focus_stats.items():
                focus_name = str(focus or "").strip().lower()
                if not focus_name or not isinstance(stats, dict):
                    continue
                focus_stats[focus_name] = {
                    "judged": max(0, int(stats.get("judged") or 0)),
                    "completed": max(0, int(stats.get("completed") or 0)),
                    "failed": max(0, int(stats.get("failed") or 0)),
                    "dragging": max(0, int(stats.get("dragging") or 0)),
                }
        raw_contextual = source.get("contextual_focus_stats")
        if isinstance(raw_contextual, dict):
            for context_key, focus_map in raw_contextual.items():
                normalized_context = str(context_key or "").strip().lower()
                if not normalized_context or not isinstance(focus_map, dict):
                    continue
                context_bucket: Dict[str, Dict[str, int]] = {}
                for focus, stats in focus_map.items():
                    focus_name = str(focus or "").strip().lower()
                    if not focus_name or not isinstance(stats, dict):
                        continue
                    context_bucket[focus_name] = {
                        "judged": max(0, int(stats.get("judged") or 0)),
                        "completed": max(0, int(stats.get("completed") or 0)),
                        "failed": max(0, int(stats.get("failed") or 0)),
                        "dragging": max(0, int(stats.get("dragging") or 0)),
                    }
                if context_bucket:
                    contextual_focus_stats[normalized_context] = context_bucket
        raw_agenda_topic_stats = source.get("agenda_topic_stats")
        if isinstance(raw_agenda_topic_stats, dict):
            for topic, stats in raw_agenda_topic_stats.items():
                topic_name = str(topic or "").strip().lower()
                if not topic_name or not isinstance(stats, dict):
                    continue
                agenda_topic_stats[topic_name] = {
                    "seen": max(0, int(stats.get("seen") or 0)),
                    "active_cycles": max(0, int(stats.get("active_cycles") or 0)),
                    "resolved": max(0, int(stats.get("resolved") or 0)),
                    "dragging": max(0, int(stats.get("dragging") or 0)),
                    "last_priority": round(self._clamp01(stats.get("last_priority") or 0.0), 4),
                    "last_confidence": round(self._clamp01(stats.get("last_confidence") or 0.0), 4),
                    "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                    "last_seen_at": stats.get("last_seen_at"),
                    "last_resolved_at": stats.get("last_resolved_at"),
                    "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
                }
        raw_observation_target_stats = source.get("observation_target_stats")
        if isinstance(raw_observation_target_stats, dict):
            for target, stats in raw_observation_target_stats.items():
                target_name = str(target or "").strip().lower()
                if not target_name or not isinstance(stats, dict):
                    continue
                observation_target_stats[target_name] = {
                    "seen": max(0, int(stats.get("seen") or 0)),
                    "recommended": max(0, int(stats.get("recommended") or 0)),
                    "resolved": max(0, int(stats.get("resolved") or 0)),
                    "stalled": max(0, int(stats.get("stalled") or 0)),
                    "last_priority": round(self._clamp01(stats.get("last_priority") or 0.0), 4),
                    "last_risk": round(self._clamp01(stats.get("last_risk") or 0.0), 4),
                    "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                    "last_seen_at": stats.get("last_seen_at"),
                    "last_resolved_at": stats.get("last_resolved_at"),
                    "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
                }
        return {
            "focus_stats": focus_stats,
            "contextual_focus_stats": contextual_focus_stats,
            "agenda_topic_stats": agenda_topic_stats,
            "observation_target_stats": observation_target_stats,
        }

    def _queue_pressure_penalty(
        self,
        drive_context: Dict[str, Any],
        *,
        governance_task_type: Optional[str] = None,
        task_family: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ) -> float:
        total_active = int(drive_context.get("active_queue_count") or 0)
        related = 0
        if governance_task_type:
            related += int(
                dict(drive_context.get("active_queue_by_governance") or {}).get(
                    governance_task_type,
                    0,
                )
            )
        if task_family:
            related += int(
                dict(drive_context.get("active_queue_by_family") or {}).get(
                    task_family,
                    0,
                )
            )
        if execution_kind:
            related += int(
                dict(drive_context.get("active_queue_by_execution_kind") or {}).get(
                    execution_kind,
                    0,
                )
            )
        penalty = 0.02 * max(total_active - 1, 0) + 0.03 * related
        return round(min(penalty, 0.28), 4)

    def _memory_maintenance_urgency(self, idle_window: Dict[str, Any]) -> float:
        idle_seconds = dict(idle_window.get("idle_seconds") or {})
        coverage = [
            self._clamp01(float(idle_seconds.get(name) or 0) / 900.0)
            for name in ("user", "agent", "memory")
        ]
        avg_idle_coverage = sum(coverage) / len(coverage) if coverage else 0.0
        checks = dict(idle_window.get("checks") or {})
        execution_window_bonus = 0.1 if checks.get("in_execution_window") else 0.0
        return round(self._clamp01(0.72 + avg_idle_coverage * 0.18 + execution_window_bonus), 4)

    def _idle_learning_urgency(
        self,
        *,
        active_sessions: int,
        topic_source: str,
        governor_mode: bool,
    ) -> float:
        base = {
            "llm": 0.58,
            "mem_compressed": 0.48,
            "activity_metadata": 0.42,
            "generic_fallback": 0.3,
            "shell_baseline_bootstrap": 0.55,
            "shell_baseline_fallback": 0.4,
        }.get(topic_source, 0.4)
        session_penalty = min(max(active_sessions, 0), 3) * 0.05
        governor_bonus = 0.05 if governor_mode else 0.0
        return round(self._clamp01(base - session_penalty + governor_bonus), 4)

    def _queue_hygiene_urgency(self, drive_context: Dict[str, Any]) -> float:
        active_queue_count = int(drive_context.get("active_queue_count") or 0)
        stale_queue_count = int(drive_context.get("stale_queue_count") or 0)
        pending_review_count = int(drive_context.get("pending_review_count") or 0)
        urgency = (
            0.24
            + min(active_queue_count, 5) * 0.08
            + min(stale_queue_count + pending_review_count, 3) * 0.08
        )
        return round(self._clamp01(urgency), 4)

    def _filter_learning_topics(
        self,
        topics: List[Dict[str, str]],
        *,
        drive_context: Dict[str, Any],
        existing_keys: set[str],
        cooldown_hours: int,
        overlap_threshold: float,
        max_topics: int,
    ) -> List[Dict[str, Any]]:
        filtered: list[Dict[str, Any]] = []
        seen_signatures: list[set[str]] = []
        completed_learning_tasks = list(drive_context.get("completed_learning_tasks") or [])
        queued_tasks = list(drive_context.get("queued_tasks") or [])

        for topic in topics:
            title = str(topic.get("title") or "").strip()
            if not title:
                continue
            topic_key = _stable_key_for_topic(title)
            if topic_key in existing_keys:
                continue
            signature = self._topic_signature(title)
            if any(self._topic_overlap(signature, previous) >= overlap_threshold for previous in seen_signatures):
                continue
            if self._topic_seen_recently(
                title,
                signature,
                completed_learning_tasks=completed_learning_tasks,
                queued_tasks=queued_tasks,
                cooldown_hours=cooldown_hours,
                overlap_threshold=overlap_threshold,
            ):
                continue
            novelty_score = self._topic_novelty_score(signature, drive_context=drive_context)
            specificity_score = self._topic_specificity_score(title, signature)
            filtered.append(
                {
                    "title": title,
                    "summary": str(topic.get("summary") or title).strip(),
                    "novelty_score": novelty_score,
                    "specificity_score": specificity_score,
                }
            )
            seen_signatures.append(signature)

        filtered.sort(
            key=lambda item: (
                float(item.get("novelty_score") or 0.0),
                float(item.get("specificity_score") or 0.0),
            ),
            reverse=True,
        )
        return filtered[: max(0, max_topics)]

    def _topic_seen_recently(
        self,
        title: str,
        signature: set[str],
        *,
        completed_learning_tasks: List[Dict[str, Any]],
        queued_tasks: List[Dict[str, Any]],
        cooldown_hours: int,
        overlap_threshold: float,
    ) -> bool:
        normalized = self._normalize_topic_text(title)
        now = datetime.now(timezone.utc)

        for task in completed_learning_tasks:
            prior_title = str(task.get("title") or "").strip()
            if not prior_title:
                continue
            if normalized == self._normalize_topic_text(prior_title):
                if self._within_cooldown(task.get("completed_at"), now=now, cooldown_hours=cooldown_hours):
                    return True
            if self._topic_overlap(signature, self._topic_signature(prior_title)) >= overlap_threshold:
                if self._within_cooldown(task.get("completed_at"), now=now, cooldown_hours=cooldown_hours):
                    return True

        for task in queued_tasks:
            prior_title = str(task.get("title") or "").strip()
            if not prior_title:
                continue
            status = str(task.get("status") or "").strip().lower()
            if status in {"completed", "failed", "cancelled"}:
                continue
            prior_signature = self._topic_signature(prior_title)
            if normalized == self._normalize_topic_text(prior_title):
                return True
            if self._topic_overlap(signature, prior_signature) >= overlap_threshold:
                return True

        return False

    def _has_recent_body_improvement(
        self,
        drive_context: Dict[str, Any],
        *,
        shell_slot_meta: Dict[str, Any],
        cooldown_hours: int,
    ) -> bool:
        slot_id = str(shell_slot_meta.get("slot_id") or "").strip()
        now = datetime.now(timezone.utc)
        queued_tasks = list(drive_context.get("queued_tasks") or [])

        for task in queued_tasks:
            execution_kind = str(task.get("execution_kind") or "").strip().lower()
            if execution_kind != "body_improvement":
                continue
            status = str(task.get("status") or "").strip().lower()
            if status not in {
                "planned", "approved", "running", "paused", "deferred", "awaiting_review", "retry",
            }:
                continue
            target_slot_id = str(task.get("constraints", {}).get("target_slot_id") or "").strip()
            if not slot_id or not target_slot_id or slot_id == target_slot_id:
                return True

        for task in queued_tasks:
            execution_kind = str(task.get("execution_kind") or "").strip().lower()
            if execution_kind != "body_improvement":
                continue
            status = str(task.get("status") or "").strip().lower()
            if status != "completed":
                continue
            target_slot_id = str(task.get("constraints", {}).get("target_slot_id") or "").strip()
            if slot_id and target_slot_id and slot_id != target_slot_id:
                continue
            completed_at = task.get("updated_at") or task.get("created_at")
            if self._within_cooldown(completed_at, now=now, cooldown_hours=cooldown_hours):
                return True

        return False

    def _topic_novelty_score(self, signature: set[str], *, drive_context: Dict[str, Any]) -> float:
        if not signature:
            return 0.0
        recent_signatures = list(drive_context.get("recent_learning_signatures") or [])
        if not recent_signatures:
            return 1.0
        highest_overlap = max((self._topic_overlap(signature, prior) for prior in recent_signatures), default=0.0)
        return max(0.0, 1.0 - highest_overlap)

    def _topic_specificity_score(self, title: str, signature: set[str]) -> float:
        word_count = len(str(title or "").split())
        signature_bonus = min(len(signature), 6) / 6.0
        word_bonus = min(max(word_count, 1), 12) / 12.0
        return round(signature_bonus * 0.7 + word_bonus * 0.3, 4)

    def _normalize_topic_text(self, text: str) -> str:
        return " ".join(_TOPIC_WORD_RE.findall(str(text or "").lower())).strip()

    def _topic_signature(self, text: str) -> set[str]:
        normalized_words = {
            word.lower()
            for word in _TOPIC_WORD_RE.findall(str(text or "").lower())
            if word.lower() not in _TOPIC_STOPWORDS
        }
        return normalized_words

    def _topic_overlap(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)

    def _within_cooldown(
        self,
        raw_timestamp: Any,
        *,
        now: datetime,
        cooldown_hours: int,
    ) -> bool:
        if cooldown_hours <= 0:
            return False
        parsed = self._parse_timestamp(raw_timestamp)
        if parsed is None:
            return False
        return now - parsed <= timedelta(hours=cooldown_hours)

    def _parse_timestamp(self, raw_timestamp: Any) -> Optional[datetime]:
        if not raw_timestamp:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw_timestamp))
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _extract_learning_topic(self, activity: Dict[str, Any]) -> str:
        """Extract a concise learning topic from recent gateway activity metadata.

        Looks at the most recent user_request and agent_work metadata to find
        topics that were discussed but may benefit from deeper research.
        Returns empty string if no meaningful topic can be extracted.
        """
        recent = dict(activity.get("recent_metadata") or {})
        user_req = recent.get("user_request") or {}
        agent_work = recent.get("agent_work") or {}

        # Try to extract a topic from the user's last request
        user_text = str(user_req.get("text") or user_req.get("query") or "")
        if not user_text:
            user_text = str(user_req.get("summary") or "")
        if user_text and len(user_text) > 10:
            # Take first sentence or first 80 chars as the topic
            topic = user_text.split(".")[0].split("\n")[0].strip()
            if len(topic) > 80:
                topic = topic[:77] + "..."
            if len(topic) >= 10:
                return topic

        # Fall back to agent's last response summary
        agent_text = str(agent_work.get("summary") or agent_work.get("title") or "")
        if agent_text and len(agent_text) > 10:
            topic = agent_text.split(".")[0].strip()
            if len(topic) > 80:
                topic = topic[:77] + "..."
            if len(topic) >= 10:
                return topic

        return ""

    def _llm_generate_learning_topics(
        self,
        activity: Dict[str, Any],
        max_topics: int = 3,
        governor_mode: bool = False,
        drive_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        """Use LLM to generate intelligent learning topics from memory context.

        Unlike _extract_learning_topic (mechanical string slicing), this reads
        the compressed memory state and recent activity to produce genuinely
        useful research directions grounded in the system's actual history.

        LLM credentials are resolved by ``memai.model_config.resolve_mem_llm_client`` —
        the canonical source of truth shared with ``MemoryService`` and the
        Tier1→Tier2 bridge.  Whatever the user configured via the CLI
        ``/api`` command (which writes to ``memory.llm.*``) is what runs
        here; there is no separate supervisor-side model config.

        Returns list of {"title": ..., "summary": ...} dicts.
        Falls back to empty list if LLM is unavailable.
        """
        try:
            from memai.model_config import resolve_mem_llm_client

            client, _ = resolve_mem_llm_client(role="default")
            if client is None:
                return []

            # ── Fetch real memory context from memory_service ──
            # In governor mode, fetch deeper context (scenes + epochs too)
            memory_context = self._fetch_memory_context(deep=governor_mode)

            recent = dict(activity.get("recent_metadata") or {})
            user_req = str(recent.get("user_request", {}).get("text", ""))[:500]
            agent_resp = str(recent.get("agent_work", {}).get("summary", ""))[:500]
            errors = int(activity.get("counts", {}).get("error_count", 0))
            uncertainty = int(activity.get("counts", {}).get("uncertainty_high_count", 0))
            history_context = self._format_learning_history_context(drive_context or {})

            if governor_mode:
                # Auto/Governor mode: user requests are blocked — rely on
                # compressed memory as the primary context for topic generation.
                prompt = (
                    f"系统处于全自动 Governor Mode。用户请求已被封锁，"
                    f"你需要完全基于压缩长期记忆自主规划学习方向。\n\n"
                    f"【系统状态】错误={errors}  高不确定性={uncertainty}\n\n"
                    f"【压缩记忆上下文 — 历史弧线、场景和事件摘要】\n"
                    f"{memory_context if memory_context else '(暂无压缩记忆，请基于系统错误和不确定性生成)'}\n\n"
                    f"{history_context}\n\n"
                    f"生成 {max_topics} 个有实质价值的学习方向。优先考虑：\n"
                    f"1. 记忆中提到但未解决的架构问题或技术债务\n"
                    f"2. 反复出现的错误模式或不确定性问题\n"
                    f"3. 记忆显示的代码改进机会\n"
                    f"4. 可执行的、可验证的具体研究主题\n"
                    f"5. 避免重复最近已完成或当前队列里已经存在的学习方向\n"
                    f"输出JSON数组: [{{\"title\": \"...\", \"summary\": \"...\"}}]"
                )
                system_prompt = (
                    "你是 VoidCube 的内生驱动器。系统处于全自动模式，"
                    "用户不在线。你需要完全基于压缩长期记忆中的历史讨论、"
                    "未解决问题和架构决策来生成有意义的学习方向。"
                    "你的输出将直接决定 Agent 的研究方向——请确保每个主题"
                    "都是具体的、可操作的、基于记忆上下文而非凭空想象的。"
                )
            else:
                prompt = (
                    f"基于以下 VoidCube 系统状态和长期记忆，生成 {max_topics} 个值得探索的学习方向。\n\n"
                    f"【最近用户请求】{user_req if user_req else '无'}\n"
                    f"【最近 Agent 响应】{agent_resp if agent_resp else '无'}\n"
                    f"【系统错误】{errors}  【高不确定性】{uncertainty}\n\n"
                    f"【压缩记忆上下文 — 最近的活跃弧线和场景】\n"
                    f"{memory_context if memory_context else '(暂无压缩记忆)'}\n\n"
                    f"{history_context}\n\n"
                    f"基于以上所有信息生成学习方向。不要泛泛而谈——"
                    f"基于记忆中的实际问题、未解决的疑问、代码改进机会来生成。"
                    f"避免重复最近已完成或当前队列里已经存在的学习任务。"
                    f"输出JSON数组: [{{\"title\": \"...\", \"summary\": \"...\"}}]"
                )
                system_prompt = (
                    "你是 VoidCube 的内生驱动器。你有权访问系统的压缩长期记忆。"
                    "基于记忆中的实际问题、架构讨论、代码改进机会和未解决的疑问，"
                    "生成有实质价值的学习方向——具体的、可操作的、基于真实上下文。"
                )
            result = client.complete_json(
                system_prompt=system_prompt,
                user_payload={"context": prompt},
                task="extractor.events",
            )
            if isinstance(result, list) and len(result) > 0:
                topics = []
                for item in result[:max_topics]:
                    if isinstance(item, dict):
                        title = str(item.get("title", "")).strip()
                        summary = str(item.get("summary", "")).strip()
                        if title:
                            topics.append({"title": title, "summary": summary or title})
                if topics:
                    return topics
            return []
        except Exception:
            return []

    def _format_learning_history_context(self, drive_context: Dict[str, Any]) -> str:
        recent_learning_titles = list(drive_context.get("recent_learning_titles") or [])
        queued_learning_titles = list(drive_context.get("queued_learning_titles") or [])
        queued_body_improvement_titles = list(drive_context.get("queued_body_improvement_titles") or [])
        lines = []
        if recent_learning_titles:
            lines.append("【最近已完成的学习主题】")
            lines.extend(f"- {title}" for title in recent_learning_titles[:5])
        if queued_learning_titles:
            lines.append("【当前队列中的学习任务】")
            lines.extend(f"- {title}" for title in queued_learning_titles[:5])
        if queued_body_improvement_titles:
            lines.append("【当前队列中的替身改进任务】")
            lines.extend(f"- {title}" for title in queued_body_improvement_titles[:3])
        return "\n".join(lines)

    def _fetch_memory_context(self, deep: bool = False) -> str:
        """Fetch recent compressed memory summaries from memory_service for LLM context.

        In deep mode (governor/auto mode): fetches arcs + scenes + epochs with
        larger limits to provide richer context when user interaction is absent.
        """
        try:
            import urllib.request, json as _json
            memory_url = self._resolve_memory_url()
            if not memory_url:
                return ""
            lines = []
            if deep:
                # Governor mode: fetch arcs, scenes, and epochs for deep context
                for mem_type, limit in [("arc", 8), ("scene", 5), ("epoch", 3)]:
                    req = _json.dumps({
                        "memory_type": mem_type, "limit": limit,
                        "include_superseded": False,
                    }).encode()
                    resp = urllib.request.urlopen(
                        urllib.request.Request(
                            f"{memory_url}/compressed/search",
                            data=req, headers={"Content-Type": "application/json"},
                        ), timeout=3,
                    )
                    data = _json.loads(resp.read())
                    for r in data.get("results", []):
                        lines.append(
                            f"- [{r.get('memory_type', '?')}] {r.get('title', '')}: "
                            f"{r.get('summary', '')[:200]}"
                        )
            else:
                req = _json.dumps({
                    "memory_type": "arc", "limit": 5, "include_superseded": False,
                }).encode()
                resp = urllib.request.urlopen(
                    urllib.request.Request(
                        f"{memory_url}/compressed/search",
                        data=req, headers={"Content-Type": "application/json"},
                    ), timeout=3,
                )
                data = _json.loads(resp.read())
                for r in data.get("results", [])[:5]:
                    lines.append(
                        f"- [{r.get('memory_type', '?')}] {r.get('title', '')}: "
                        f"{r.get('summary', '')[:200]}"
                    )
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

    def _mem_extract_learning_topics(
        self, activity: Dict[str, Any], max_topics: int = 3
    ) -> List[Dict[str, str]]:
        """Tier-2 fallback: pull learning topics from Mem compressed memories.

        This is the local path that does NOT require an LLM API key — it
        reads `compressed_memories` rows (Arc / Scene / Epoch summaries) and
        turns each row's title + summary into a self-learning topic candidate.
        The architectural baseline §3.4 "LLM 优先 + 启发式降级" pattern
        applies here: when the LLM path is unavailable, structured compressed
        memory is still meaningful enough to drive a learning task.

        The HTTP call goes through the same gateway-resolved memory URL that
        `_fetch_memory_context` uses, keeping with baseline §4.2
        (gateway as the internal entry point).
        """
        try:
            import urllib.request, json as _json
            memory_url = self._resolve_memory_url()
            if not memory_url:
                return []
            req = _json.dumps({
                "memory_type": "arc",
                "limit": max_topics,
                "include_superseded": False,
            }).encode()
            resp = urllib.request.urlopen(
                urllib.request.Request(
                    f"{memory_url}/compressed/search",
                    data=req,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=3,
            )
            data = _json.loads(resp.read())
            results = data.get("results", [])
        except Exception:
            return []

        topics: List[Dict[str, str]] = []
        for r in results:
            title = str(r.get("title", "")).strip()
            summary = str(r.get("summary", "")).strip()
            if not title:
                continue
            # Trim long titles but keep them human-readable
            if len(title) > 80:
                title = title[:77] + "..."
            topics.append({
                "title": title,
                "summary": (
                    f"Use idle capacity to revisit memory arc '{title}' — "
                    f"{summary[:240]}" if summary else
                    f"Use idle capacity to revisit memory arc '{title}' and "
                    f"check whether new evidence requires follow-up."
                ),
            })
            if len(topics) >= max_topics:
                break
        return topics

    @staticmethod
    def _resolve_memory_url() -> str | None:
        """Resolve memory service URL via gateway service discovery."""
        try:
            import urllib.request, json as _json
            resp = urllib.request.urlopen(
                "http://127.0.0.1:6000/admin/services", timeout=2,
            )
            services = _json.loads(resp.read()).get("services", {})
            for svc in services.values():
                if svc.get("service_type") == "memory":
                    return svc.get("address")
        except Exception:
            pass
        return None

    def _build_shell_baseline_learning_candidate(
        self,
        *,
        stable_key: str,
        active_sessions: int,
        shell_slot_id: str,
        shell_worktree: str,
        trigger: str,
        drive_context: Dict[str, Any],
        bootstrap: bool,
        drive_judgement: Optional[Dict[str, Any]] = None,
        adaptive_policy: Optional[DriveAdaptivePolicy] = None,
    ) -> EndogenousTaskCandidate:
        summary = (
            "Use idle capacity to inspect the current shell-body codebase, "
            "map its structure, identify current weaknesses, and record evidence-backed "
            "learning notes that can guide future self-improvement."
        )
        if shell_worktree:
            summary += f" Start from shell slot {shell_slot_id} at {shell_worktree}."
        return self._build_scored_candidate(
            stable_key=stable_key,
            title="Understand the current shell body codebase",
            summary=summary,
            priority="normal",
            governance_task_type="self_learning",
            task_family="self_learning",
            execution_kind=None,
            value_tags=["creativity"],
            candidate_kind="shell_baseline_learning",
            score_inputs={
                "core_value_strength": 0.79 if bootstrap else 0.66,
                "urgency": self._idle_learning_urgency(
                    active_sessions=active_sessions,
                    topic_source=(
                        "shell_baseline_bootstrap"
                        if bootstrap
                        else "shell_baseline_fallback"
                    ),
                    governor_mode=False,
                ),
                "novelty": 0.88 if bootstrap else 0.45,
                "specificity": 0.68 if bootstrap else 0.58,
                "execution_readiness": 0.92 if shell_worktree else 0.78,
                "queue_pressure_penalty": self._queue_pressure_penalty(
                    drive_context,
                    governance_task_type="self_learning",
                    task_family="self_learning",
                ),
                "adaptive_factor": self._adaptive_factor_for_candidate(
                    candidate_kind="shell_baseline_learning",
                    adaptive_policy=adaptive_policy or self._neutral_adaptive_policy(),
                ),
            },
            metadata={
                "learning_branch": "codebase_baseline",
                "self_learning_mode": "shell_codebase_baseline",
                **({"drive_judgement": drive_judgement} if drive_judgement else {}),
            },
            evidence={
                "active_sessions": active_sessions,
                "trigger": trigger,
                "learning_topic": "",
                "topic_source": "shell_codebase_baseline",
                "learning_branch": "codebase_baseline",
                "llm_generated": False,
                "baseline_worktree_path": shell_worktree,
                "baseline_slot_id": shell_slot_id,
            },
            constraints={
                "execution_policy": "learn_shell_baseline",
                "must_not_modify_active_body": True,
                "baseline_worktree_path": shell_worktree,
                "baseline_slot_id": shell_slot_id,
            },
        )

    def _decision_for(
        self,
        family: str,
        decisions_by_family: Dict[str, Any],
        decisions_by_governance: Dict[str, Any],
    ) -> Dict[str, Any]:
        if family in decisions_by_family:
            return dict(decisions_by_family[family] or {})
        governance = "self_evolution"
        if family in {"memory_maintenance", "self_learning", "user"}:
            governance = family
        return dict(decisions_by_governance.get(governance) or {})

    def _calculate_learning_quality_score(self, idle_window: Dict[str, Any]) -> float:
        try:
            learning_tasks = idle_window.get("completed_learning_tasks", [])
            completed_count = len(learning_tasks)
            if completed_count == 0:
                return 0.0

            quality_sum = 0.0
            freshness_sum = 0.0
            now = None
            try:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
            except Exception:
                pass

            for task in learning_tasks:
                quality_sum += float(task.get("quality_score") or 0.5)
                if now and task.get("completed_at"):
                    try:
                        t = datetime.fromisoformat(str(task["completed_at"]))
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        age_days = (now - t).days
                        freshness = max(0.0, 1.0 - age_days / 90.0)
                        freshness_sum += freshness
                    except Exception:
                        freshness_sum += 0.5
                else:
                    freshness_sum += 0.5

            avg_quality = quality_sum / completed_count
            avg_freshness = freshness_sum / completed_count
            score = avg_quality * 60 + avg_freshness * 40
            return max(0.0, min(100.0, score))
        except Exception:
            return 0.0

    def _get_shell_slot_meta(self, idle_window: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            shell_slot = idle_window.get("shell_slot")
            if shell_slot and isinstance(shell_slot, dict):
                return shell_slot
        except Exception:
            pass

        try:
            import urllib.request, json as _json
            memory_url = self._resolve_memory_url()
            if not memory_url:
                return None
            resp = urllib.request.urlopen(
                f"{memory_url}/body/shell/slot",
                timeout=3,
            )
            data = _json.loads(resp.read())
            if data.get("slot_id"):
                return data
        except Exception:
            pass

        return None

    def _generate_body_improvement_direction(
        self,
        idle_window: Dict[str, Any],
        learning_quality: float,
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        activity = dict(idle_window.get("activity") or {})

        llm_direction = self._llm_generate_improvement_direction(
            activity,
            learning_quality,
            shell_slot_meta,
        )
        if llm_direction:
            llm_direction["source"] = "llm"
            return llm_direction

        history_direction = self._generate_improvement_from_history(
            idle_window,
            shell_slot_meta,
        )
        if history_direction:
            history_direction["source"] = "history"
            return history_direction

        git_direction = self._generate_improvement_from_git_diff(
            shell_slot_meta,
        )
        if git_direction:
            git_direction["source"] = "git_diff"
            return git_direction

        fallback_direction = {
            "title": "General code quality improvement",
            "summary": (
                "Apply recent learning findings to improve the shell body's code quality. "
                "Focus on fixing identified issues, improving documentation, and enhancing "
                "code maintainability within the allowed evolution boundaries."
            ),
            "diff_summary": "",
            "source": "fallback",
        }
        return fallback_direction

    def _llm_generate_improvement_direction(
        self,
        activity: Dict[str, Any],
        learning_quality: float,
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        try:
            from memai.model_config import resolve_mem_llm_client
            client, _ = resolve_mem_llm_client(role="default")
            if client is None:
                return None

            memory_context = self._fetch_memory_context()
            recent = dict(activity.get("recent_metadata") or {})
            user_req = str(recent.get("user_request", {}).get("text", ""))[:500]
            agent_resp = str(recent.get("agent_work", {}).get("summary", ""))[:500]

            prompt = (
                f"基于以下信息，为替身 Agent 的代码改进生成一个具体方向。\n\n"
                f"【学习质量评分】{learning_quality:.1f}/100\n"
                f"【替身槽位】{shell_slot_meta.get('slot_id', '?')}\n"
                f"【替身工作树路径】{shell_slot_meta.get('worktree_path', '?')}\n"
                f"【最近用户请求】{user_req if user_req else '无'}\n"
                f"【最近 Agent 响应】{agent_resp if agent_resp else '无'}\n\n"
                f"【压缩记忆上下文】\n{memory_context if memory_context else '(暂无)'}\n\n"
                f"分析学习成果和记忆中的问题，提出一个具体的代码改进方向。"
                f"改进方向应该是：\n"
                f"- 基于实际学习成果\n"
                f"- 在允许的演化边界内（agent/, skills/, tools/, presets/）\n"
                f"- 可操作且有明确目标\n"
                f"输出JSON: {{\"title\": \"...\", \"summary\": \"...\", \"diff_summary\": \"...\"}}"
            )

            result = client.complete_json(
                system_prompt=(
                    "你是代码改进专家。基于学习成果和系统状态，"
                    "为替身 Agent 提出具体、可操作的代码改进方向。"
                    "只关注 agent/、skills/、tools/、presets/ 目录内的改进。"
                ),
                user_payload={"task": prompt},
                task="scholar.revision",
            )

            if isinstance(result, dict):
                title = str(result.get("title", "")).strip()
                summary = str(result.get("summary", "")).strip()
                if title:
                    return {
                        "title": title,
                        "summary": summary or title,
                        "diff_summary": str(result.get("diff_summary", "")),
                    }
        except Exception:
            pass
        return None

    def _generate_improvement_from_history(
        self,
        idle_window: Dict[str, Any],
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        try:
            learning_tasks = idle_window.get("completed_learning_tasks", [])
            if not learning_tasks:
                return None

            recent_tasks = sorted(
                learning_tasks,
                key=lambda t: t.get("completed_at", ""),
                reverse=True,
            )[:3]

            topics = []
            for task in recent_tasks:
                title = str(task.get("title", "") or task.get("topic", ""))
                if title:
                    topics.append(title)

            if topics:
                return {
                    "title": "Apply recent learning: " + ", ".join(topics[:2]),
                    "summary": (
                        f"Apply recent learning findings to improve the shell body. "
                        f"Recent learning topics: {', '.join(topics)}. "
                        f"Focus on implementing improvements based on these research results."
                    ),
                    "diff_summary": "",
                }
        except Exception:
            pass
        return None

    def _generate_improvement_from_git_diff(
        self,
        shell_slot_meta: Dict[str, Any],
    ) -> Optional[Dict[str, str]]:
        try:
            worktree_path = shell_slot_meta.get("worktree_path")
            if not worktree_path:
                return None

            import subprocess
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                changed_count = len(result.stdout.strip().splitlines())
                return {
                    "title": f"Review {changed_count} pending changes",
                    "summary": (
                        f"The shell body worktree has {changed_count} files with pending changes. "
                        f"Review these changes and apply appropriate improvements based on learning findings."
                    ),
                    "diff_summary": result.stdout[:500],
                }
        except Exception:
            pass
        return None


def _stable_key_for_topic(topic: str) -> str:
    """Generate a stable dedup key from a learning topic string.

    Uses a short hash so that genuinely different topics get different keys,
    allowing multiple creativity candidates to coexist in the queue.
    """
    import hashlib
    normalized = topic.strip().lower()
    if not normalized:
        return "creativity:idle_learning:fallback"
    h = hashlib.md5(normalized.encode()).hexdigest()[:8]
    return f"creativity:idle_learning:{h}"
