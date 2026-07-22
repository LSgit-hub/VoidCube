from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
import fnmatch
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
from systems.evolution_boundary import (
    AGENT_EVOLUTION_ALLOWED_FILES,
    AGENT_EVOLUTION_ALLOWED_PATHS,
    classify_agent_evolution_changes,
    normalize_repo_path,
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
    "backlog_pressure_penalty": 0.12,
    "repetition_penalty": 0.10,
}
_TERMINAL_QUEUE_STATUSES = {"completed", "failed", "cancelled"}
_REVIEW_BACKLOG_STATUSES = {"deferred", "paused", "awaiting_review", "retry"}
_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"
_REVIEW_API_B_JUDGEMENT_NEED = "review_api_b_judgement"
_LM_TASK_TYPES = {"observation", "review", "learning", "maintenance", "improvement"}
_LM_RISK_LEVELS = {"low", "medium", "high"}
_LM_EVIDENCE_LEVELS = {"weak", "moderate", "strong"}
_LM_EXECUTION_MODES = {"observe_only", "review_then_handoff", "guarded_execution"}
_LEGACY_LM_EXECUTION_MODE_ALIASES = {"review_then_backlog": "review_then_handoff"}
_STATIC_GOVERNANCE_CANDIDATE_COOLDOWN_HOURS = 12
TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD = 3

_BODY_STRUCTURE_PATH_RE = re.compile(
    r"(?<![\w.-])((?:(?:agent|tools|skills|presets|systems/agent)/"
    r"[A-Za-z0-9_.\-/]+|run_agent\.py))"
)
_BODY_STRUCTURE_DOMAIN_TARGETS: tuple[
    tuple[str, tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (
        "prompt_context",
        ("prompt", "context", "reasoning", "提示词", "上下文", "推理"),
        ("agent/prompt_builder.py", "agent/context_engine.py", "agent/context_compressor.py"),
    ),
    (
        "stream_display",
        ("stream", "display", "render", "输出", "展示", "流式"),
        ("agent/stream_handler.py", "agent/display.py", "agent/subagent_display.py"),
    ),
    (
        "memory_access",
        ("memory", "recall", "记忆", "召回"),
        ("agent/memory_manager.py", "agent/memory_provider.py", "tools/memory_tool.py"),
    ),
    (
        "model_routing",
        ("model", "provider", "routing", "模型", "路由", "供应商"),
        ("agent/smart_model_routing.py", "agent/model_metadata.py", "agent/auxiliary_client.py"),
    ),
    (
        "tool_execution",
        ("tool", "terminal", "scheduler", "工具", "终端", "调度"),
        ("agent/tool_scheduler.py", "tools/registry.py", "tools/terminal_tool.py"),
    ),
    (
        "delegation",
        ("delegate", "subagent", "multi-agent", "委派", "子代理", "多代理"),
        ("tools/delegate_tool.py", "tools/mixture_of_agents_tool.py", "agent/subagent_display.py"),
    ),
    (
        "skills",
        ("skill", "skills", "技能"),
        ("agent/skill_utils.py", "agent/skill_commands.py", "tools/skills_tool.py"),
    ),
    (
        "error_resilience",
        ("error", "retry", "rate limit", "错误", "重试", "限流"),
        ("agent/error_classifier.py", "agent/retry_utils.py", "agent/rate_limit_tracker.py"),
    ),
    (
        "security",
        ("security", "redact", "credential", "安全", "脱敏", "凭证"),
        ("agent/redact.py", "agent/message_sanitizer.py", "tools/approval.py"),
    ),
    (
        "browser_web",
        ("browser", "web", "crawl", "浏览器", "网页", "抓取"),
        ("tools/browser_tool.py", "tools/web_tools.py", "tools/web_tools_local.py"),
    ),
    (
        "file_operations",
        ("file", "path", "filesystem", "文件", "路径"),
        ("tools/file_tools.py", "tools/file_operations.py", "tools/path_security.py"),
    ),
)


@dataclass(frozen=True, slots=True)
class EndogenousTaskCandidate:
    """Compatibility task projection emitted by the cognition core for API-B review."""

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
            object.__setattr__(
                self,
                "api_a_handoff_count",
                self.api_a_ready_count,
            )


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
                "api_b_judgement_blockage_pressure": round(self.reflection.api_b_judgement_blockage_pressure, 4),
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


class EndogenousDriveEngine:
    """Supervisor drive loop — deterministic core + optional LLM intelligence.

    The drive engine does not execute work. It turns system facts, core values,
    and (when available) LLM-analyzed memory context into auditable
    API-B judgement projections that still pass through supervisor review.

    Without LLM: uses deterministic text extraction (first 80 chars).
    With LLM: reads compressed memory context to generate intelligent,
    context-aware learning topics.
    """

    def __init__(self, config: Any | None = None) -> None:
        self.config = config
        self._latest_lm_task_generation_context: Dict[str, Any] = {}
        self._latest_lm_task_generation_proposals: List[Dict[str, Any]] = []

    def get_latest_lm_task_generation_context(self) -> Dict[str, Any]:
        return dict(self._latest_lm_task_generation_context or {})

    def get_latest_lm_task_generation_proposals(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self._latest_lm_task_generation_proposals]

    def _resolve_drive_input(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if isinstance(drive_input, dict):
            return dict(drive_input)
        return {}

    def resolve_cognitive_posture_state(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
        deliberation_dict: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_input = self._resolve_drive_input(
            drive_input=drive_input,
        )
        drive_context = self._build_drive_context(drive_input)
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
        shell_slot = dict(self._get_shell_slot_meta(drive_input) or {})
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
        drive_input: Optional[Dict[str, Any]] = None,
        existing_drive_keys: Iterable[str],
        max_candidates: int = 3,
        deliberation_report: DriveDeliberationReport | None = None,
        lm_proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EndogenousTaskCandidate]:
        drive_input = self._resolve_drive_input(
            drive_input=drive_input,
        )
        existing_keys = set(existing_drive_keys)
        candidates = self._candidate_stream(
            drive_input,
            existing_keys=existing_keys,
            deliberation_report=deliberation_report,
            lm_proposals_override=lm_proposals_override,
        )
        candidates.sort(key=lambda candidate: candidate.utility, reverse=True)
        return candidates[:max(max_candidates, 0)]

    def build_deliberation_report(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
    ) -> DriveDeliberationReport:
        drive_input = self._resolve_drive_input(
            drive_input=drive_input,
        )
        activity = dict(drive_input.get("activity") or {})
        drive_context = self._build_drive_context(drive_input)
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
        decisions_by_family = dict(drive_input.get("task_family_decisions") or {})
        decisions_by_governance = dict(drive_input.get("governance_task_type_decisions") or {})
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
        autonomous_improvement_plan = self._decision_for(
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
        pre_decayed = drive_input.get("correction_signals")
        if pre_decayed is not None:
            try:
                correction_signals = max(0, int(pre_decayed))
            except (TypeError, ValueError):
                correction_signals = recent_errors + uncertainty_count
        else:
            correction_signals = recent_errors + uncertainty_count
        shell_slot_meta = self._get_shell_slot_meta(drive_input) or {}
        perception = self._perceive_drive_state(
            drive_input=drive_input,
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
            autonomous_improvement_plan=autonomous_improvement_plan,
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
        drive_input: Dict[str, Any],
        activity: Dict[str, Any],
        drive_context: Dict[str, Any],
        counts: Dict[str, Any],
        correction_signals: int,
        shell_slot_meta: Optional[Dict[str, Any]] = None,
    ) -> DrivePerceptionSnapshot:
        checks = dict(drive_input.get("checks") or {})
        idle_seconds = dict(drive_input.get("idle_seconds") or {})
        user_chain_signal = dict(drive_input.get("user_chain_signal") or {})
        autonomous_chain_gate_active = bool(drive_input.get("autonomous_chain_gate_active", False))
        active_sessions = int(activity.get("active_sessions") or 0)
        learning_backlog_count = len(list(drive_context.get("learning_backlog_titles") or []))
        body_improvement_backlog_count = len(
            list(drive_context.get("body_improvement_backlog_titles") or [])
        )
        stale_backlog_count = int(drive_context.get("stale_backlog_count") or 0)
        pending_review_count = int(drive_context.get("pending_review_count") or 0)
        api_b_judgement_count = int(
            drive_context.get("api_b_judgement_count")
            or 0
        )
        api_a_handoff_count = int(
            drive_context.get("api_a_handoff_count")
            if drive_context.get("api_a_handoff_count") is not None
            else drive_context.get("api_a_ready_count")
            or 0
        )
        api_a_ready_count = api_a_handoff_count
        api_a_running_count = int(drive_context.get("api_a_running_count") or 0)
        learning_quality = self._calculate_learning_quality_score(drive_input)
        recent_errors = int(counts.get("error_count") or counts.get("recent_errors") or 0)
        uncertainty_count = int(
            counts.get("uncertainty_high_count")
            or counts.get("high_uncertainty")
            or 0
        )
        shell_slot_id = str((shell_slot_meta or {}).get("slot_id") or "").strip()
        shell_slot_present = bool(shell_slot_id or (shell_slot_meta or {}).get("worktree_path"))
        user_chain_quiet = bool(
            user_chain_signal.get("is_quiet", active_sessions <= 0)
        )

        user_mode = "serving_user"
        if autonomous_chain_gate_active:
            user_mode = "autonomous_chain_gate"
        elif user_chain_quiet:
            user_mode = "user_chain_quiet"

        system_posture = "stable"
        if active_sessions > 0:
            system_posture = "serving_user"
        elif correction_signals >= 4:
            system_posture = "strained"
        elif pending_review_count > 0 or stale_backlog_count > 1:
            system_posture = "degrading"
        elif learning_quality >= 60.0 and shell_slot_present:
            system_posture = "growth_window"

        return DrivePerceptionSnapshot(
            user_mode=user_mode,
            autonomous_chain_gate_active=autonomous_chain_gate_active,
            system_posture=system_posture,
            active_sessions=active_sessions,
            recent_errors=recent_errors,
            uncertainty_count=uncertainty_count,
            correction_signals=max(0, correction_signals),
            learning_quality=learning_quality,
            has_learning_history=bool(drive_input.get("completed_learning_tasks") or []),
            shell_slot_present=shell_slot_present,
            shell_slot_id=shell_slot_id,
            api_b_judgement_count=api_b_judgement_count,
            learning_backlog_count=learning_backlog_count,
            body_improvement_backlog_count=body_improvement_backlog_count,
            stale_backlog_count=stale_backlog_count,
            pending_review_count=pending_review_count,
            api_a_ready_count=api_a_ready_count,
            api_a_handoff_count=api_a_handoff_count,
            api_a_running_count=api_a_running_count,
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
            - min(perception.learning_backlog_count, 3) * 0.08
        )
        body_upgrade_readiness = self._clamp01(
            (perception.learning_quality / 100.0) * 0.7
            + (0.15 if perception.shell_slot_present else 0.0)
            - min(perception.body_improvement_backlog_count, 2) * 0.2
        )
        backlog_strain = min(
            perception.api_b_judgement_count * 0.08
            + perception.stale_backlog_count * 0.12
            + perception.pending_review_count * 0.1,
            1.0,
        )
        memory_pressure = self._clamp01(
            0.25
            + min(perception.stale_backlog_count, 3) * 0.08
        )
        self_confidence = self._clamp01(
            0.55
            + (0.08 if perception.autonomous_chain_gate_active else 0.0)
            - min(perception.active_sessions, 3) * 0.08
            - min(perception.pending_review_count, 3) * 0.04
        )
        governance_load_state = "clear"
        if backlog_strain >= 0.55:
            governance_load_state = "strained"
        elif backlog_strain >= 0.3:
            governance_load_state = "busy"

        return DriveWorldModel(
            user_mode=perception.user_mode,
            system_posture=perception.system_posture,
            truthfulness_pressure=truthfulness_pressure,
            learning_momentum=learning_momentum,
            body_upgrade_readiness=body_upgrade_readiness,
            governance_load_state=governance_load_state,
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
        api_b_judgement_tasks = list(drive_context.get("autonomous_chain_live_tasks") or [])
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
        for task in api_b_judgement_tasks:
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

        api_b_judgement_blockage_pressure = self._clamp01(
            blocked_status_count * 0.18
            + perception.stale_backlog_count * 0.16
            + max(0, perception.api_b_judgement_count - 2) * 0.05
        )
        if world_model.governance_load_state == "strained":
            api_b_judgement_blockage_pressure = self._clamp01(api_b_judgement_blockage_pressure + 0.2)
        elif world_model.governance_load_state == "busy":
            api_b_judgement_blockage_pressure = self._clamp01(api_b_judgement_blockage_pressure + 0.08)

        api_b_judgement_blockage_state = "clear"
        if api_b_judgement_blockage_pressure >= 0.6:
            api_b_judgement_blockage_state = "blocked"
        elif api_b_judgement_blockage_pressure >= 0.32:
            api_b_judgement_blockage_state = "dragging"

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
            + (0.14 if api_b_judgement_blockage_state != "clear" else 0.0)
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
            - api_b_judgement_blockage_pressure * 0.24
            - repeated_drive_pressure * 0.12
            - historical_drag_ratio * 0.16
            - recent_relapse_drag_ratio * 0.06
            - (0.08 if body_growth_blocked else 0.0)
        )
        historical_underdelivery_active = bool(
            historical_pressure.get("underdelivery_active")
        )

        dominant_constraint = "none"
        if api_b_judgement_blockage_pressure >= 0.55:
            dominant_constraint = _API_B_JUDGEMENT_BLOCKAGE
        elif body_growth_blocked:
            dominant_constraint = "body_growth_cooldown"
        elif historical_underdelivery_active:
            dominant_constraint = "historical_underdelivery"
        elif recent_learning_quality < 0.4 and recent_learning_count > 0:
            dominant_constraint = "weak_learning_yield"
        elif perception.active_sessions > 0 and perception.user_mode == "serving_user":
            dominant_constraint = "user_service_priority"

        rationale_parts = [
            f"近期学习收益状态为 {learning_yield_state}",
            f"API-B 判断在途阻塞状态为 {api_b_judgement_blockage_state}",
        ]
        if historical_total > 0:
            rationale_parts.append(
                f"历史 {historical_scope} 成功比率为 {historical_success_ratio:.2f}"
            )
        if body_growth_blocked:
            rationale_parts.append("近期 shell 改进活动暂时阻断了替身成长")
        if dominant_constraint != "none":
            rationale_parts.append(f"当前主约束是 {dominant_constraint}")

        return DriveReflection(
            recent_learning_count=recent_learning_count,
            recent_learning_quality=recent_learning_quality,
            learning_yield_state=learning_yield_state,
            api_b_judgement_blockage_pressure=api_b_judgement_blockage_pressure,
            api_b_judgement_blockage_state=api_b_judgement_blockage_state,
            body_growth_blocked=body_growth_blocked,
            repeated_drive_pressure=repeated_drive_pressure,
            autonomy_readiness=autonomy_readiness,
            dominant_constraint=dominant_constraint,
            rationale="; ".join(rationale_parts) + ".",
            source_evidence=[
                f"recent_learning_count={recent_learning_count}",
                f"recent_learning_quality={recent_learning_quality:.2f}",
                f"blocked_status_count={blocked_status_count}",
                f"stale_backlog_count={perception.stale_backlog_count}",
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
            elif status in {"approved", "deferred", "paused", "awaiting_review", "awaiting_user_consent", "retry"}:
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
        scoped_historical_total = int(historical_pressure["total"] or 0)
        scoped_historical_drag_ratio = float(historical_pressure["drag_ratio"] or 0.0)
        historical_has_temporal_markers = bool(
            historical_pressure.get("has_temporal_markers")
        )
        recent_relapse_drag_count = int(historical_pressure["recent_relapse_drag_count"] or 0)
        recent_relapse_drag_ratio = float(historical_pressure["recent_relapse_drag_ratio"] or 0.0)

        learning_success = _family_success(["self_learning"], default=0.55)
        backlog_success = _family_success(["general_self_evolution", "self_evolution"], default=0.45)
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
            "governance_hygiene": _focus_effectiveness("governance_hygiene", default=0.48),
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
            - reflection.api_b_judgement_blockage_pressure * 0.18
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
        governance_hygiene_bias = self._clamp01(
            0.44
            + reflection.api_b_judgement_blockage_pressure * 0.34
            + max(0.0, 0.5 - backlog_success) * 0.22
            + reflection.repeated_drive_pressure * 0.1
            + (focus_effectiveness["governance_hygiene"] - 0.45) * 0.16
            + min(
                0.16,
                sum(
                    (
                        self._clamp01(stats.get("last_risk") or 0.0) * 0.08
                        + max(0, int(stats.get("stalled") or 0)) * 0.03
                    )
                    for target, stats in observation_target_stats.items()
                    if target == _API_B_JUDGEMENT_BLOCKAGE
                    and isinstance(stats, dict)
                ),
            )
            + agenda_drag_pressure * 0.08
        )
        body_growth_bias = self._clamp01(
            0.42
            + (body_success - 0.45) * 0.28
            + world_model.body_upgrade_readiness * 0.16
            + reflection.recent_learning_quality * 0.16
            - (0.18 if reflection.body_growth_blocked else 0.0)
            - reflection.api_b_judgement_blockage_pressure * 0.12
            + (focus_effectiveness["body_growth"] - 0.42) * 0.14
            - unresolved_observation_pressure * 0.08
        )
        historical_observation_pressure = 0.0
        historical_order_uncertain = (
            reflection.dominant_constraint == "historical_underdelivery"
            and not historical_has_temporal_markers
            and scoped_historical_total >= 7
            and scoped_historical_drag_ratio >= 0.6
        )
        if reflection.dominant_constraint == "historical_underdelivery":
            historical_observation_pressure = self._clamp01(
                0.1
                + max(0.0, scoped_historical_drag_ratio - 0.55) * 0.45
                + max(0.0, 0.42 - reflection.autonomy_readiness) * 0.4
                + (
                    0.06
                    if recent_relapse_drag_count >= 2
                    and recent_relapse_drag_ratio >= 0.66
                    else 0.0
                )
                + (0.08 if historical_order_uncertain else 0.0)
            )
        observation_bias = self._clamp01(
            0.3
            + reflection.api_b_judgement_blockage_pressure * 0.28
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
            + historical_observation_pressure
        )
        candidate_throttle = self._clamp01(
            0.18
            + reflection.api_b_judgement_blockage_pressure * 0.32
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
        api_a_execution_flow_pressure = self._clamp01(
            perception.api_a_handoff_count * 0.14
            + perception.api_a_running_count * 0.24
        )
        if api_a_execution_flow_pressure > 0.0:
            learning_expansion_bias = self._clamp01(
                learning_expansion_bias - api_a_execution_flow_pressure * 0.08
            )
            body_growth_bias = self._clamp01(
                body_growth_bias - api_a_execution_flow_pressure * 0.22
            )
            observation_bias = self._clamp01(
                observation_bias + api_a_execution_flow_pressure * 0.06
            )
            candidate_throttle = self._clamp01(
                candidate_throttle + api_a_execution_flow_pressure * 0.18
            )

        focus_candidates = {
            "truthfulness": truthfulness_bias,
            "memory_continuity": memory_continuity_bias,
            "learning_expansion": learning_expansion_bias,
            "governance_hygiene": governance_hygiene_bias,
            "body_growth": body_growth_bias,
            "observation": observation_bias,
        }
        preferred_focus = max(focus_candidates.items(), key=lambda item: item[1])[0]
        if (
            reflection.dominant_constraint == "none"
            and 0 < perception.correction_signals < TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
            and truthfulness_bias >= memory_continuity_bias - 0.02
            and observation_bias < 0.68
            and candidate_throttle < 0.65
        ):
            preferred_focus = "truthfulness"
        if (
            reflection.dominant_constraint == "historical_underdelivery"
            and preferred_focus == "truthfulness"
            and observation_bias >= truthfulness_bias - 0.12
        ):
            preferred_focus = "observation"
        if (
            reflection.dominant_constraint == "historical_underdelivery"
            and preferred_focus == "memory_continuity"
            and not self._has_truthfulness_review_signal(perception)
            and reflection.autonomy_readiness < 0.4
            and observation_bias >= 0.56
            and memory_continuity_bias <= observation_bias + 0.1
        ):
            preferred_focus = "observation"
        if (
            self._has_memory_backlog_recovery_window(
                perception=perception,
                reflection=reflection,
            )
            and preferred_focus == "observation"
            and memory_continuity_bias >= max(0.6, truthfulness_bias - 0.02)
            and observation_bias <= memory_continuity_bias + 0.05
        ):
            preferred_focus = "memory_continuity"
        if (
            reflection.dominant_constraint == "historical_underdelivery"
            and observation_bias >= 0.72
            and preferred_focus == "memory_continuity"
        ):
            preferred_focus = "observation"
        if (
            historical_order_uncertain
            and preferred_focus == "memory_continuity"
            and observation_bias >= 0.64
        ):
            preferred_focus = "observation"
        if self._has_truthfulness_review_signal(perception):
            preferred_focus = "truthfulness"
        if (
            scoped_historical_drag_ratio >= 0.66
            and (
                preferred_focus == "observation"
                or reflection.autonomy_readiness <= 0.18
                or observation_bias >= 0.58
            )
        ):
            candidate_budget = 1
        elif historical_order_uncertain:
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
        elif candidate_throttle >= 0.4 or preferred_focus == "governance_hygiene":
            exploratory_learning_quota = 1
        else:
            exploratory_learning_quota = 2
        if perception.api_a_running_count > 0:
            exploratory_learning_quota = 0
        elif perception.api_a_handoff_count > 0:
            exploratory_learning_quota = min(exploratory_learning_quota, 1)
        body_growth_quota = (
            1
            if (
                body_growth_bias >= 0.58
                and candidate_throttle < 0.62
                and preferred_focus in {"body_growth", "learning_expansion"}
            )
            else 0
        )
        if perception.api_a_handoff_count > 0 or perception.api_a_running_count > 0:
            body_growth_quota = 0

        rationale_parts = [
            f"preferred focus is {preferred_focus}",
            f"candidate throttle is {candidate_throttle:.2f}",
            f"candidate budget is {candidate_budget}",
            f"learning bias is {learning_expansion_bias:.2f}",
            f"governance hygiene bias is {governance_hygiene_bias:.2f}",
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
        if api_a_execution_flow_pressure > 0.0:
            rationale_parts.append("API-A 执行窗口仍在流动，因此先等待回流沉淀再扩大自主产出")

        return DriveAdaptivePolicy(
            learning_expansion_bias=learning_expansion_bias,
            truthfulness_bias=truthfulness_bias,
            memory_continuity_bias=memory_continuity_bias,
            governance_hygiene_bias=governance_hygiene_bias,
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
                f"backlog_success={backlog_success:.2f}",
                f"body_success={body_success:.2f}",
                f"memory_success={memory_success:.2f}",
                f"historical_drag_scope={scoped_historical_scope}",
                f"historical_drag_ratio={historical_drag_ratio:.2f}",
                f"historical_has_temporal_markers={historical_has_temporal_markers}",
                f"historical_order_uncertain={historical_order_uncertain}",
                f"scoped_historical_drag_ratio={scoped_historical_drag_ratio:.2f}",
                f"recent_relapse_drag_count={recent_relapse_drag_count}",
                f"recent_relapse_drag_ratio={recent_relapse_drag_ratio:.2f}",
                f"api_b_judgement_blockage_pressure={reflection.api_b_judgement_blockage_pressure:.2f}",
                f"autonomy_readiness={reflection.autonomy_readiness:.2f}",
                f"context_key={context_key}",
                f"observation_recovery_advantage={observation_recovery_advantage:.2f}",
                f"unresolved_observation_pressure={unresolved_observation_pressure:.2f}",
                f"observation_recovery_signal={observation_recovery_signal:.2f}",
                f"historical_observation_pressure={historical_observation_pressure:.2f}",
                f"agenda_drag_pressure={agenda_drag_pressure:.2f}",
                f"agenda_resolution_signal={agenda_resolution_signal:.2f}",
                f"dynamic_candidate_throttle_boost={float(policy.get('dynamic_candidate_throttle_boost') or 0.0):.2f}",
                f"dynamic_observation_bias_boost={float(policy.get('dynamic_observation_bias_boost') or 0.0):.2f}",
                f"dynamic_truthfulness_bias_boost={float(policy.get('dynamic_truthfulness_bias_boost') or 0.0):.2f}",
                f"dynamic_learning_expansion_suppression={float(policy.get('dynamic_learning_expansion_suppression') or 0.0):.2f}",
                f"api_a_execution_flow_pressure={api_a_execution_flow_pressure:.2f}",
                f"api_a_handoff_count={perception.api_a_handoff_count}",
                f"api_a_running_count={perception.api_a_running_count}",
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

    def _has_memory_backlog_recovery_window(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        reflection: DriveReflection,
    ) -> bool:
        return (
            reflection.dominant_constraint == "none"
            and not self._has_truthfulness_review_signal(perception)
            and perception.pending_review_count > 0
            and perception.stale_backlog_count <= 0
            and perception.api_b_judgement_count <= 1
            and reflection.api_b_judgement_blockage_pressure <= 0.22
            and reflection.learning_yield_state in {"mixed", "strong"}
        )

    def _has_truthfulness_review_signal(
        self,
        perception: DrivePerceptionSnapshot,
    ) -> bool:
        return perception.correction_signals >= TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD

    def _has_governance_hygiene_review_signal(
        self,
        perception: DrivePerceptionSnapshot,
    ) -> bool:
        return (
            perception.pending_review_count > 0
            or perception.stale_backlog_count > 0
            or perception.api_b_judgement_count > 3
        )

    def _has_historical_governance_hygiene_review_signal(
        self,
        drive_context: Dict[str, Any],
    ) -> bool:
        drive_history = dict(drive_context.get("drive_history") or {})
        dragging = 0
        for item in list(drive_history.get("outcomes") or [])[:12]:
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
            if status in {"approved", "deferred", "paused", "awaiting_review", "awaiting_user_consent", "retry"}:
                dragging += 1
            if dragging >= 2:
                return True
        return False

    def _detect_needs(
        self,
        *,
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        autonomous_improvement_plan: Dict[str, Any],
    ) -> List[DriveNeed]:
        needs: List[DriveNeed] = []
        truthfulness_review_active = (
            self_learning_plan.get("eligible_for_planning")
            and self._has_truthfulness_review_signal(perception)
        )
        memory_backlog_recovery_window = self._has_memory_backlog_recovery_window(
            perception=perception,
            reflection=reflection,
        )
        if memory_plan.get("eligible_for_planning"):
            memory_constraint_penalty = 0.0
            memory_recovery_bonus = 0.0
            if reflection.dominant_constraint == "historical_underdelivery":
                memory_constraint_penalty += 0.08
            if adaptive_policy.preferred_focus == "observation":
                memory_constraint_penalty += 0.06
            if (
                reflection.dominant_constraint == "none"
                and adaptive_policy.preferred_focus == "memory_continuity"
                and perception.pending_review_count <= 0
                and perception.stale_backlog_count <= 0
                and perception.api_b_judgement_count <= 0
                and not self._has_truthfulness_review_signal(perception)
                and reflection.learning_yield_state in {"mixed", "strong"}
            ):
                memory_constraint_penalty += 0.05
            if memory_backlog_recovery_window:
                memory_recovery_bonus += 0.12
            needs.append(
                DriveNeed(
                    need_type="stabilize_memory_continuity",
                    severity=self._clamp01(
                        world_model.memory_pressure
                        + 0.08
                        + adaptive_policy.memory_continuity_bias * 0.22
                        - memory_constraint_penalty
                        + memory_recovery_bonus
                    ),
                    urgency=self._clamp01(
                        world_model.memory_pressure
                        + 0.1
                        + adaptive_policy.memory_continuity_bias * 0.18
                        - memory_constraint_penalty * 0.82
                        + memory_recovery_bonus * 0.84
                    ),
                    confidence=self._clamp01(
                        0.68
                        + adaptive_policy.memory_continuity_bias * 0.22
                        - memory_constraint_penalty * 0.32
                        + memory_recovery_bonus * 0.18
                    ),
                    rationale="在全天候运行语义下，记忆连续性维护始终是监督者的常驻职责。",
                    source_evidence=[
                        f"memory_idle={perception.checks.get('has_memory_idle', False)}",
                        f"memory_continuity_bias={adaptive_policy.memory_continuity_bias:.2f}",
                        f"memory_recovery_bonus={memory_recovery_bonus:.2f}",
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
                    rationale="近期错误与高不确定性信号说明真实性债务正在累积，应该尽快浮出并进入复核。",
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
            if memory_backlog_recovery_window:
                learning_constraint_penalty += 0.14
            learning_constraint_penalty += min(
                0.22,
                perception.api_a_handoff_count * 0.06
                + perception.api_a_running_count * 0.14,
            )
            needs.append(
                DriveNeed(
                    need_type="expand_learning_frontier",
                    severity=self._clamp01(
                        world_model.learning_momentum
                        - 0.02
                        + reflection.autonomy_readiness * 0.16
                        + adaptive_policy.learning_expansion_bias * 0.2
                        - reflection.api_b_judgement_blockage_pressure * 0.12
                        - learning_constraint_penalty
                    ),
                    urgency=self._clamp01(
                        world_model.learning_momentum
                        + reflection.recent_learning_quality * 0.15
                        + adaptive_policy.learning_expansion_bias * 0.1
                        - reflection.api_b_judgement_blockage_pressure * 0.08
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
                        "当近期证据仍有增益时，学习应继续扩展；"
                        "但如果 API-B 判断在途阻塞已说明继续产出只会加压，就应主动降温。"
                    ),
                    source_evidence=[
                        f"learning_quality={perception.learning_quality:.2f}",
                        f"learning_backlog_count={perception.learning_backlog_count}",
                        f"has_learning_history={perception.has_learning_history}",
                        f"learning_yield_state={reflection.learning_yield_state}",
                        f"api_b_judgement_blockage_state={reflection.api_b_judgement_blockage_state}",
                        f"learning_expansion_bias={adaptive_policy.learning_expansion_bias:.2f}",
                        f"candidate_throttle={adaptive_policy.candidate_throttle:.2f}",
                        f"learning_constraint_penalty={learning_constraint_penalty:.2f}",
                        f"api_a_handoff_count={perception.api_a_handoff_count}",
                        f"api_a_running_count={perception.api_a_running_count}",
                    ],
                )
            )
        if (
            autonomous_improvement_plan.get("eligible_for_planning")
            and perception.shell_slot_present
            and perception.learning_quality >= 60.0
            and not reflection.body_growth_blocked
            and perception.api_a_handoff_count <= 0
            and perception.api_a_running_count <= 0
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
                    rationale="只有当近期学习确实产出有效收益，且替身改进没有被近期输出压力卡住时，才应准备自主改进。",
                    source_evidence=[
                        f"learning_quality={perception.learning_quality:.2f}",
                        f"shell_slot_present={perception.shell_slot_present}",
                        f"body_improvement_backlog_count={perception.body_improvement_backlog_count}",
                        f"body_growth_blocked={reflection.body_growth_blocked}",
                        f"body_growth_bias={adaptive_policy.body_growth_bias:.2f}",
                        f"api_a_handoff_count={perception.api_a_handoff_count}",
                        f"api_a_running_count={perception.api_a_running_count}",
                    ],
                )
            )
        if autonomous_improvement_plan.get("eligible_for_planning"):
            governance_review_active = self._has_governance_hygiene_review_signal(perception)
            backlog_need_score = self._clamp01(
                0.2
                + (min(perception.api_b_judgement_count, 5) * 0.08 if governance_review_active else 0.0)
                + min(perception.stale_backlog_count + perception.pending_review_count, 4) * 0.08
                + reflection.api_b_judgement_blockage_pressure * 0.18
                + adaptive_policy.governance_hygiene_bias * 0.16
            )
            needs.append(
                DriveNeed(
                    need_type=_REVIEW_API_B_JUDGEMENT_NEED,
                    severity=backlog_need_score,
                    urgency=self._clamp01(
                        backlog_need_score
                        - 0.02
                        + reflection.repeated_drive_pressure * 0.08
                        + adaptive_policy.governance_hygiene_bias * 0.12
                    ),
                    confidence=self._clamp01(
                        0.56
                        + reflection.api_b_judgement_blockage_pressure * 0.16
                        + adaptive_policy.governance_hygiene_bias * 0.22
                    ),
                    rationale="当内生输出反复出现却没有真正闭环、治理压力持续累积时，治理卫生复核就应被抬高优先级。",
                    source_evidence=[
                        f"api_b_judgement_count={perception.api_b_judgement_count}",
                        f"stale_backlog_count={perception.stale_backlog_count}",
                        f"pending_review_count={perception.pending_review_count}",
                        f"repeated_drive_pressure={reflection.repeated_drive_pressure:.2f}",
                        f"governance_hygiene_bias={adaptive_policy.governance_hygiene_bias:.2f}",
                    ],
                )
            )
        if (
            reflection.api_b_judgement_blockage_pressure >= 0.45
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
            observation_release_penalty = 0.0
            if (
                memory_backlog_recovery_window
                and adaptive_policy.memory_continuity_bias
                >= max(0.58, adaptive_policy.truthfulness_bias - 0.02)
            ):
                observation_release_penalty += 0.08
                if adaptive_policy.preferred_focus == "observation":
                    observation_release_penalty += 0.04
                if (
                    adaptive_policy.observation_bias
                    <= adaptive_policy.memory_continuity_bias + 0.04
                ):
                    observation_release_penalty += 0.04
            needs.append(
                DriveNeed(
                    need_type="observe_before_acting",
                    severity=self._clamp01(
                        0.34
                        + reflection.api_b_judgement_blockage_pressure * 0.32
                        + max(0.0, 0.5 - reflection.autonomy_readiness) * 0.45
                        + adaptive_policy.observation_bias * 0.18
                        + observation_constraint_bonus
                        - observation_release_penalty
                    ),
                    urgency=self._clamp01(
                        0.28
                        + reflection.api_b_judgement_blockage_pressure * 0.28
                        + max(0.0, 0.45 - reflection.autonomy_readiness) * 0.4
                        + adaptive_policy.observation_bias * 0.14
                        + observation_constraint_bonus * 0.85
                        - observation_release_penalty * 0.82
                    ),
                    confidence=self._clamp01(
                        0.62
                        + adaptive_policy.observation_bias * 0.28
                        - observation_release_penalty * 0.32
                    ),
                    rationale="当重复产出持续撞上阻塞，或自主就绪度还不够稳时，内生驱动应主动放慢并先补观察。",
                    source_evidence=[
                        f"api_b_judgement_blockage_pressure={reflection.api_b_judgement_blockage_pressure:.2f}",
                        f"autonomy_readiness={reflection.autonomy_readiness:.2f}",
                        f"dominant_constraint={reflection.dominant_constraint}",
                        f"observation_bias={adaptive_policy.observation_bias:.2f}",
                        f"observation_release_penalty={observation_release_penalty:.2f}",
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
            elif need.need_type == _REVIEW_API_B_JUDGEMENT_NEED:
                priority = self._clamp01(priority + adaptive_policy.governance_hygiene_bias * 0.08)
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
            elif need.need_type == _REVIEW_API_B_JUDGEMENT_NEED:
                intents.append(
                    DriveIntent(
                        intent_type="review_governance_hygiene",
                        priority=priority,
                        rationale=need.rationale,
                        target_horizon="near_term",
                        output_channel="task_candidate",
                        source_needs=[need.need_type],
                        candidate_family="general_self_evolution",
                        candidate_kind="governance_hygiene_review",
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
                            if reflection.api_b_judgement_blockage_pressure >= 0.55
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

        if world_model.governance_load_state in {"busy", "strained"}:
            backlog_need = need_lookup.get(_REVIEW_API_B_JUDGEMENT_NEED)
            backlog_intent = intent_lookup.get("review_governance_hygiene")
            signals.append(
                DriveSignal(
                    signal_type="governance_review_suggestion",
                    priority=self._clamp01(
                        (backlog_need.severity if backlog_need else 0.45)
                        + (0.08 if world_model.governance_load_state == "strained" else 0.0)
                    ),
                    message="API-B 判断在途提示：在继续累积更多自主工作前，应先观察并复核判断段。",
                    rationale=(
                        backlog_need.rationale
                        if backlog_need is not None
                        else "API-B 判断在途压力与复核债务都提示当前应先检查判断段。"
                    ),
                    source_needs=(
                        [backlog_need.need_type]
                        if backlog_need is not None
                        else [_REVIEW_API_B_JUDGEMENT_NEED]
                    ),
                    related_intent=backlog_intent.intent_type if backlog_intent is not None else None,
                    payload={
                        "governance_load_state": world_model.governance_load_state,
                        "api_b_judgement_count": perception.api_b_judgement_count,
                        "stale_backlog_count": perception.stale_backlog_count,
                        "pending_review_count": perception.pending_review_count,
                    },
                )
            )
        else:
            backlog_need = need_lookup.get(_REVIEW_API_B_JUDGEMENT_NEED)
            backlog_intent = intent_lookup.get("review_governance_hygiene")
            if (
                backlog_need is not None
                and (
                    perception.pending_review_count > 0
                    or perception.stale_backlog_count > 0
                    or perception.api_b_judgement_count > 0
                )
            ):
                signals.append(
                    DriveSignal(
                        signal_type="governance_review_suggestion",
                        priority=self._clamp01(backlog_need.severity + 0.06),
                        message="即便尚未出现完整阻塞，只要已经出现复核债务或陈旧治理项，也建议先做治理复核。",
                        rationale=backlog_need.rationale,
                        source_needs=[backlog_need.need_type],
                        related_intent=backlog_intent.intent_type if backlog_intent is not None else None,
                        payload={
                            "governance_load_state": world_model.governance_load_state,
                            "api_b_judgement_count": perception.api_b_judgement_count,
                            "stale_backlog_count": perception.stale_backlog_count,
                            "pending_review_count": perception.pending_review_count,
                            "trigger": "early_review_debt",
                        },
                    )
                )

        truthfulness_need = need_lookup.get("repair_truthfulness")
        truthfulness_intent = intent_lookup.get("review_truthfulness_signals")
        if (
            truthfulness_need is not None
            and self._has_truthfulness_review_signal(perception)
        ):
            signals.append(
                DriveSignal(
                    signal_type="observation_signal",
                    priority=self._clamp01(
                        truthfulness_need.severity
                        + 0.08
                        + adaptive_policy.truthfulness_bias * 0.1
                    ),
                    message=(
                        "当前建议把观察焦点落在真实性侧，因为修正压力正在上升，"
                        "即使整体内生驱动也在放缓。"
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
                    message="在继续扩大自主输出前，建议先补观察，因为当前内生驱动正遭遇阻塞或准备度偏弱。",
                    rationale=observe_need.rationale,
                    source_needs=[observe_need.need_type],
                    related_intent=observe_intent.intent_type if observe_intent is not None else None,
                    payload={
                        "observation_target": reflection.dominant_constraint,
                        "api_b_judgement_blockage_state": reflection.api_b_judgement_blockage_state,
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
                    message="在继续推出更多候选工作前，应先对齐并收紧自主输出节奏。",
                    rationale=(
                        f"{reflection.rationale} {adaptive_policy.rationale}"
                    ),
                    source_needs=[observe_need.need_type],
                    related_intent=observe_intent.intent_type if observe_intent is not None else None,
                    payload={
                        "dominant_constraint": reflection.dominant_constraint,
                        "learning_yield_state": reflection.learning_yield_state,
                        "api_b_judgement_blockage_state": reflection.api_b_judgement_blockage_state,
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
                        "在继续推进自主动作前，建议先补观察，因为修正压力正在上升。"
                        if observation_target == "truthfulness"
                        else "当前建议先补观察，因为学习质量显示可能正在形成新的成长窗口。"
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
                message="本轮内生驱动已经为当前治理姿态与候选预算完成选择。",
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
            "intents": [intent_dict] if intent_dict else [],
            "needs": linked_needs,
        }

    def _drive_judgement_metadata(
        self,
        *,
        intent: Optional[DriveIntent],
        candidate_kind: str,
        all_intents: List[DriveIntent],
        needs: List[DriveNeed],
        perception: DrivePerceptionSnapshot,
        world_model: DriveWorldModel,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
    ) -> Dict[str, Any]:
        if intent is not None:
            return self._intent_metadata(
                intent=intent,
                needs=needs,
                perception=perception,
                world_model=world_model,
                reflection=reflection,
                adaptive_policy=adaptive_policy,
            )

        matching_intents = [
            item
            for item in all_intents
            if str(item.candidate_kind or "").strip() == candidate_kind
        ]
        selected_intents = matching_intents or list(all_intents[:3])
        report = DriveDeliberationReport(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            needs=needs,
            intents=selected_intents,
        ).to_dict()
        source_need_types = {
            need_type
            for intent_row in report["intents"]
            for need_type in list(intent_row.get("source_needs") or [])
            if str(need_type).strip()
        }
        linked_needs = [
            need
            for need in report["needs"]
            if not source_need_types or need["need_type"] in source_need_types
        ][:4]
        return {
            "perception": report["perception"],
            "world_model": report["world_model"],
            "reflection": report["reflection"],
            "adaptive_policy": report["adaptive_policy"],
            "intent": report["intents"][0] if report["intents"] else {},
            "intents": list(report["intents"]),
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
        backlog_pressure_penalty: float = 0.0,
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
            "backlog_pressure_penalty": round(self._clamp01(backlog_pressure_penalty), 4),
            "repetition_penalty": round(self._clamp01(repetition_penalty), 4),
        }
        raw_score = (
            dimensions["core_value_strength"] * _SCORE_WEIGHTS["core_value_strength"]
            + dimensions["urgency"] * _SCORE_WEIGHTS["urgency"]
            + dimensions["novelty"] * _SCORE_WEIGHTS["novelty"]
            + dimensions["specificity"] * _SCORE_WEIGHTS["specificity"]
            + dimensions["execution_readiness"] * _SCORE_WEIGHTS["execution_readiness"]
            - penalties["backlog_pressure_penalty"] * _SCORE_WEIGHTS["backlog_pressure_penalty"]
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

    def _neutral_adaptive_policy(self) -> DriveAdaptivePolicy:
        return DriveAdaptivePolicy(
            learning_expansion_bias=0.5,
            truthfulness_bias=0.5,
            memory_continuity_bias=0.5,
            governance_hygiene_bias=0.5,
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

    def _active_api_b_judgement_candidate_kinds(
        self,
        drive_context: Dict[str, Any],
    ) -> set[str]:
        kinds: set[str] = set()
        for task in list(drive_context.get("api_b_judgement_tasks") or []):
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "").strip().lower()
            if status in _TERMINAL_QUEUE_STATUSES:
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

    def _adaptive_group_for_candidate(self, candidate: EndogenousTaskCandidate) -> Optional[str]:
        candidate_kind = self._candidate_kind_of(candidate)
        if candidate_kind == "exploratory_learning":
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
            "governance_hygiene": {"governance_hygiene_review"},
            "memory_continuity": {"memory_maintenance"},
            "observation": {"truthfulness_review", "governance_hygiene_review"},
        }
        observation_tie_break = {
            "truthfulness_review": 0,
            "governance_hygiene_review": 1,
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
                "governance_hygiene_review",
                "shell_baseline_learning",
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
        self,
        drive_input: Dict[str, Any],
        *,
        existing_keys: set[str] = None,
        deliberation_report: DriveDeliberationReport | None = None,
        lm_proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EndogenousTaskCandidate]:
        if existing_keys is None:
            existing_keys = set()
        activity = dict(drive_input.get("activity") or {})
        drive_context = self._build_drive_context(drive_input)
        policy = drive_context["policy"]
        shell_slot_meta = self._get_shell_slot_meta(drive_input) or {}
        decisions_by_family = dict(drive_input.get("task_family_decisions") or {})
        decisions_by_governance = dict(drive_input.get("governance_task_type_decisions") or {})

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
        autonomous_improvement_plan = self._decision_for(
            "general_self_evolution",
            decisions_by_family,
            decisions_by_governance,
        )
        deliberation = deliberation_report or self.build_deliberation_report(
            drive_input=drive_input
        )
        perception = deliberation.perception
        world_model = deliberation.world_model
        reflection = deliberation.reflection
        adaptive_policy = deliberation.adaptive_policy
        needs = list(deliberation.needs)
        intents = list(deliberation.intents)
        active_candidate_kinds = self._active_api_b_judgement_candidate_kinds(drive_context)
        intents_by_kind = {
            str(intent.candidate_kind or ""): intent
            for intent in intents
            if intent.candidate_kind
        }

        lm_candidates = self._llm_task_proposals(
            drive_input=drive_input,
            existing_keys=existing_keys,
            deliberation=deliberation,
            drive_context=drive_context,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            autonomous_improvement_plan=autonomous_improvement_plan,
            proposals_override=lm_proposals_override,
        )
        candidates: List[EndogenousTaskCandidate] = []
        if (
            memory_plan.get("eligible_for_planning")
            and "memory_maintenance" not in active_candidate_kinds
            and "continuity:memory_maintenance_sweep" not in existing_keys
            and not self._has_recent_static_governance_completion(
                drive_context,
                stable_key="continuity:memory_maintenance_sweep",
            )
        ):
            memory_intent = intents_by_kind.get("memory_maintenance")
            candidates.append(
                self._build_scored_candidate(
                    stable_key="continuity:memory_maintenance_sweep",
                    title="维持长期记忆连续性",
                    summary=(
                        "在当前观测周期内检查记忆维护需求，"
                        "让长期身份、摘要与治理轨迹保持可用。"
                    ),
                    priority="high",
                    governance_task_type="memory_maintenance",
                    task_family="memory_maintenance",
                    execution_kind="memory_maintenance",
                    value_tags=["continuity"],
                    candidate_kind="memory_maintenance",
                    score_inputs={
                        "core_value_strength": 1.0,
                        "urgency": self._memory_maintenance_urgency(drive_input),
                        "novelty": 0.58,
                        "specificity": 0.78,
                        "execution_readiness": 1.0,
                        "backlog_pressure_penalty": self._backlog_pressure_penalty(
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
                    metadata={
                        "drive_judgement": self._drive_judgement_metadata(
                            intent=memory_intent,
                            candidate_kind="memory_maintenance",
                            all_intents=intents,
                            needs=needs,
                            perception=perception,
                            world_model=world_model,
                            reflection=reflection,
                            adaptive_policy=adaptive_policy,
                        )
                    },
                    evidence={
                        "observation_checks": dict(drive_input.get("checks") or {}),
                        "idle_seconds": dict(drive_input.get("idle_seconds") or {}),
                    },
                )
            )

        recent_errors = perception.recent_errors
        uncertainty_count = perception.uncertainty_count
        if (
            self._has_truthfulness_review_signal(perception)
            and self_learning_plan.get("eligible_for_planning")
            and "truthfulness_review" not in active_candidate_kinds
            and "truthfulness:review_correction_signals" not in existing_keys
        ):
            truth_intent = intents_by_kind.get("truthfulness_review")
            candidates.append(
                self._build_scored_candidate(
                    stable_key="truthfulness:review_correction_signals",
                    title="复核近期不确定性与纠偏信号",
                    summary=(
                        "把近期错误或高不确定性回答收成有边界的自学习跟进，"
                        "而不是继续让它们停留在不可见状态。"
                    ),
                    priority="high",
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
                        "novelty": 0.72 if drive_input.get("correction_signals") is not None else 0.68,
                        "specificity": self._clamp01(
                            0.55 + min(perception.correction_signals, 5) * 0.08
                        ),
                        "execution_readiness": 0.92,
                        "backlog_pressure_penalty": self._backlog_pressure_penalty(
                            drive_context,
                            governance_task_type="self_learning",
                            task_family="self_learning",
                        ),
                        "adaptive_factor": self._adaptive_factor_for_candidate(
                            candidate_kind="truthfulness_review",
                            adaptive_policy=adaptive_policy,
                        ),
                    },
                    metadata={
                        "drive_judgement": self._drive_judgement_metadata(
                            intent=truth_intent,
                            candidate_kind="truthfulness_review",
                            all_intents=intents,
                            needs=needs,
                            perception=perception,
                            world_model=world_model,
                            reflection=reflection,
                            adaptive_policy=adaptive_policy,
                        )
                    },
                    evidence={
                        "recent_errors": recent_errors,
                        "uncertainty_high_count": uncertainty_count,
                        "correction_signals": perception.correction_signals,
                        "signal_source": (
                            "runtime_observation_snapshot"
                            if drive_input.get("correction_signals") is not None
                            else "raw_counts"
                        ),
                    },
                )
            )

        active_sessions = perception.active_sessions
        if self_learning_plan.get("eligible_for_planning"):
            shell_slot_id = str(shell_slot_meta.get("slot_id") or "shell").strip()
            shell_worktree = str(shell_slot_meta.get("worktree_path") or "").strip()
            baseline_key = f"creativity:self_learning:shell_baseline:{shell_slot_id or 'shell'}"
            has_learning_history = perception.has_learning_history
            learning_intent = intents_by_kind.get("exploratory_learning")
            shell_baseline_intent = intents_by_kind.get("shell_baseline_learning")
            cognitive_assessment_memory = self._build_cognitive_assessment_memory(drive_context)
            self_iteration_trend_memory = self._build_self_iteration_trend_memory(drive_context)

            topics: list[dict] = []

            autonomous_chain_gate_active = drive_input.get("autonomous_chain_gate_active", False)
            mechanical_topic = self._extract_learning_topic(activity)
            if mechanical_topic:
                topics = [{"title": mechanical_topic, "summary": (
                    f"Use autonomous-chain capacity to research '{mechanical_topic}' — the most recent "
                    f"user-discussed topic that may benefit from deeper investigation."
                )}]

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
                and "shell_baseline_learning" not in active_candidate_kinds
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
                        drive_judgement=self._drive_judgement_metadata(
                            intent=shell_baseline_intent,
                            candidate_kind="shell_baseline_learning",
                            all_intents=intents,
                            needs=needs,
                            perception=perception,
                            world_model=world_model,
                            reflection=reflection,
                            adaptive_policy=adaptive_policy,
                        ),
                        adaptive_policy=adaptive_policy,
                    )
                )
                existing_keys.add(baseline_key)

            generated_count = 0
            for topic in topics:
                topic_key = _stable_key_for_topic(topic["title"])
                if topic_key in existing_keys:
                    continue  # Skip duplicate topic
                if "exploratory_learning" in active_candidate_kinds:
                    continue
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
                            "core_value_strength": 0.64,
                            "urgency": self._idle_learning_urgency(
                                active_sessions=active_sessions,
                                topic_source="activity_metadata",
                                autonomous_chain_gate=autonomous_chain_gate_active,
                            ),
                            "novelty": float(topic.get("novelty_score") or 0.6),
                            "specificity": float(topic.get("specificity_score") or 0.55),
                            "execution_readiness": 0.66,
                            "backlog_pressure_penalty": self._backlog_pressure_penalty(
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
                            "drive_judgement": self._drive_judgement_metadata(
                                intent=learning_intent,
                                candidate_kind="exploratory_learning",
                                all_intents=intents,
                                needs=needs,
                                perception=perception,
                                world_model=world_model,
                                reflection=reflection,
                                adaptive_policy=adaptive_policy,
                            ),
                        },
                        evidence={
                            "active_sessions": active_sessions,
                            "trigger": "idle_capacity",
                            "learning_topic": topic["title"],
                            "topic_source": "activity_metadata",
                            "learning_branch": "exploratory",
                            "llm_generated": False,
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
                review_key = f"creativity:self_learning:cognitive_review:{_stable_key_for_topic(target or judgement)}"
                if review_key not in existing_keys and "exploratory_learning" not in active_candidate_kinds:
                    review_summary = (
                        "Review the latest endogenous cognitive-assessment memory, "
                        "extract what changed, and record evidence-backed learning notes "
                        "for the next autonomous planning cycle."
                    )
                    if judgement:
                        review_summary += f" Current judgement: {judgement}."
                    candidates.append(
                        self._build_scored_candidate(
                            stable_key=review_key,
                            title=f"Review endogenous cognition: {target or 'current judgement'}",
                            summary=review_summary,
                            priority="normal",
                            governance_task_type="self_learning",
                            task_family="self_learning",
                            execution_kind=None,
                            value_tags=["creativity", "truthfulness"],
                            candidate_kind="exploratory_learning",
                            score_inputs={
                                "core_value_strength": 0.72,
                                "urgency": self._clamp01(
                                    0.42
                                    + float(cognitive_assessment_memory.get("why_not_improvement_now_count") or 0) * 0.08
                                    + (
                                        0.08
                                        if adaptive_policy.preferred_focus in {"truthfulness", "observation"}
                                        else 0.0
                                    )
                                ),
                                "novelty": 0.52,
                                "specificity": 0.66,
                                "execution_readiness": 0.72,
                                "backlog_pressure_penalty": self._backlog_pressure_penalty(
                                    drive_context,
                                    governance_task_type="self_learning",
                                    task_family="self_learning",
                                ),
                                "adaptive_factor": self._adaptive_factor_for_candidate(
                                    candidate_kind="exploratory_learning",
                                    adaptive_policy=adaptive_policy,
                                ),
                            },
                            metadata={
                                "learning_branch": "cognitive_assessment_review",
                                "self_learning_mode": "endogenous_cognition_review",
                                "cognitive_assessment_target": target,
                                "llm_cognitive_assessment": dict(cognitive_assessment_memory),
                                "drive_judgement": self._drive_judgement_metadata(
                                    intent=learning_intent,
                                    candidate_kind="exploratory_learning",
                                    all_intents=intents,
                                    needs=needs,
                                    perception=perception,
                                    world_model=world_model,
                                    reflection=reflection,
                                    adaptive_policy=adaptive_policy,
                                ),
                            },
                            evidence={
                                "active_sessions": active_sessions,
                                "trigger": "canonical_cognitive_assessment_memory",
                                "learning_topic": target,
                                "topic_source": "cognitive_assessment_memory",
                                "learning_branch": "cognitive_assessment_review",
                                "llm_generated": False,
                                "cognitive_assessment_memory": dict(cognitive_assessment_memory),
                            },
                            constraints={
                                "execution_policy": "learn_only",
                                "must_not_modify_active_body": True,
                            },
                        )
                    )
                    existing_keys.add(review_key)

        if (
            autonomous_improvement_plan.get("eligible_for_planning")
            and "governance_hygiene_review" not in active_candidate_kinds
            and "continuity:governance_hygiene_review" not in existing_keys
            and not self._has_recent_static_governance_completion(
                drive_context,
                stable_key="continuity:governance_hygiene_review",
            )
            and (
                self._has_governance_hygiene_review_signal(perception)
                or self._has_historical_governance_hygiene_review_signal(drive_context)
            )
        ):
            backlog_intent = intents_by_kind.get("governance_hygiene_review")
            candidates.append(
                self._build_scored_candidate(
                    stable_key="continuity:governance_hygiene_review",
                    title="观察 API-B 判断积压",
                    summary=(
                        "检查已规划、已延后或已暂停的 API-B 判断在途工作是否仍具备"
                        "足够证据、责任归属和回滚约束。"
                    ),
                    priority="normal",
                    governance_task_type="self_evolution",
                    task_family="general_self_evolution",
                    execution_kind="general_self_evolution",
                    value_tags=["continuity", "truthfulness"],
                    candidate_kind="governance_hygiene_review",
                    score_inputs={
                        "core_value_strength": 0.62,
                        "urgency": self._governance_hygiene_urgency(drive_context),
                        "novelty": 0.38,
                        "specificity": self._clamp01(
                            0.46 + min(int(drive_context.get("api_b_judgement_count") or 0), 4) * 0.05
                        ),
                        "execution_readiness": 0.85,
                        "adaptive_factor": self._adaptive_factor_for_candidate(
                            candidate_kind="governance_hygiene_review",
                            adaptive_policy=adaptive_policy,
                        ),
                    },
                    metadata={
                        "drive_judgement": self._drive_judgement_metadata(
                            intent=backlog_intent,
                            candidate_kind="governance_hygiene_review",
                            all_intents=intents,
                            needs=needs,
                            perception=perception,
                            world_model=world_model,
                            reflection=reflection,
                            adaptive_policy=adaptive_policy,
                        )
                    },
                    evidence={
                        "trigger": "supervisor_backlog_governance",
                    },
                    constraints={
                        "must_not_execute_without_review": True,
                    },
                )
            )

        body_projection = self._build_body_improvement_projection(
            drive_context=drive_context,
            shell_slot_meta=shell_slot_meta,
        )
        if (
            autonomous_improvement_plan.get("eligible_for_planning")
            and "body_improvement" not in active_candidate_kinds
            and body_projection.get("available")
            and not reflection.body_growth_blocked
            and adaptive_policy.body_growth_quota > 0
        ):
            body_intent = intents_by_kind.get("body_improvement")
            target_paths = list(body_projection.get("target_paths") or [])
            domains = list(body_projection.get("structure_domains") or [])
            learning_quality = float(
                body_projection.get("learning_quality_score") or 0.0
            )
            stable_key = (
                "creativity:body_improvement:"
                f"{body_projection['mapping_key']}"
            )
            if stable_key not in existing_keys:
                candidates.append(
                    self._build_scored_candidate(
                        stable_key=stable_key,
                        title=(
                            "定向改进替身："
                            + (domains[0] if domains else target_paths[0])
                        ),
                        summary=(
                            "依据已完成学习结论，定向检查并改进 shell 替身中的 "
                            f"{', '.join(target_paths)}。只允许修改映射出的安全节点，"
                            "提交 Git 变更后由 Supervisor 独立复核。"
                        ),
                        priority="high" if learning_quality >= 80.0 else "normal",
                        governance_task_type="self_evolution",
                        task_family="body_upgrade",
                        execution_kind="body_improvement",
                        value_tags=["creativity", "continuity"],
                        candidate_kind="body_improvement",
                        score_inputs={
                            "core_value_strength": 0.78,
                            "urgency": self._clamp01(learning_quality / 100.0),
                            "novelty": self._clamp01(
                                0.5 + len(domains) * 0.06
                            ),
                            "specificity": self._clamp01(
                                0.62 + len(target_paths) * 0.06
                            ),
                            "execution_readiness": 0.88,
                            "backlog_pressure_penalty": self._backlog_pressure_penalty(
                                drive_context,
                                governance_task_type="self_evolution",
                                task_family="body_upgrade",
                                execution_kind="body_improvement",
                            ),
                            "adaptive_factor": self._adaptive_factor_for_candidate(
                                candidate_kind="body_improvement",
                                adaptive_policy=adaptive_policy,
                            ),
                        },
                        metadata={
                            "improvement_direction_source": body_projection.get(
                                "mapping_source"
                            ),
                            "target_paths": target_paths,
                            "structure_domains": domains,
                            "learning_task_ids": [
                                ref.get("mem_id")
                                for ref in list(body_projection.get("learning_refs") or [])
                            ],
                            "learning_quality_score": learning_quality,
                            "drive_judgement": self._drive_judgement_metadata(
                                intent=body_intent,
                                candidate_kind="body_improvement",
                                all_intents=intents,
                                needs=needs,
                                perception=perception,
                                world_model=world_model,
                                reflection=reflection,
                                adaptive_policy=adaptive_policy,
                            ),
                        },
                        evidence={
                            "trigger": "completed_learning_structure_mapping",
                            "learning_quality_score": learning_quality,
                            "learning_refs": list(
                                body_projection.get("learning_refs") or []
                            ),
                            "evidence_summary": list(
                                body_projection.get("evidence_summary") or []
                            ),
                            "structure_mapping": {
                                "source": body_projection.get("mapping_source"),
                                "domains": domains,
                                "target_paths": target_paths,
                            },
                        },
                        constraints=self._body_improvement_constraints(
                            body_projection
                        ),
                    )
                )
                existing_keys.add(stable_key)

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
        canonical_shell_baselines = [
            candidate
            for candidate in heuristic_candidates
            if self._candidate_kind_of(candidate) == "shell_baseline_learning"
        ]
        if canonical_shell_baselines:
            lm_candidates = [
                candidate
                for candidate in lm_candidates
                if self._candidate_kind_of(candidate) != "shell_baseline_learning"
            ]
        if not lm_candidates:
            return list(heuristic_candidates or [])
        if not heuristic_candidates:
            return list(lm_candidates or [])

        merged: List[EndogenousTaskCandidate] = [
            *canonical_shell_baselines,
            *lm_candidates,
        ]
        seen_signatures = {
            self._candidate_semantic_signature(candidate)
            for candidate in merged
        }
        lm_kinds = {
            self._candidate_kind_of(candidate)
            for candidate in lm_candidates
            if self._candidate_kind_of(candidate)
        }

        complement_budget = 1
        if adaptive_policy.preferred_focus in {"memory_continuity", "governance_hygiene", "truthfulness"}:
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
            if candidate_kind == "shell_baseline_learning":
                continue
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
        drive_input: Optional[Dict[str, Any]] = None,
        existing_keys: set[str],
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        autonomous_improvement_plan: Dict[str, Any],
        proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[EndogenousTaskCandidate]:
        drive_input = self._resolve_drive_input(
            drive_input=drive_input,
        )
        service_runtime = getattr(self.config, "service_runtime", None)
        if service_runtime is None:
            return []
        if not bool(getattr(service_runtime, "endogenous_drive_lm_task_generation_enabled", False)):
            return []

        evidence_packet = self._build_lm_evidence_packet(
            drive_input=drive_input,
            deliberation=deliberation,
            drive_context=drive_context,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            autonomous_improvement_plan=autonomous_improvement_plan,
        )
        if proposals_override is None:
            proposals = self._generate_lm_task_proposals(evidence_packet=evidence_packet)
        else:
            proposals = [dict(item) for item in proposals_override if isinstance(item, dict)]
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
        drive_input: Optional[Dict[str, Any]] = None,
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        memory_plan: Dict[str, Any],
        self_learning_plan: Dict[str, Any],
        autonomous_improvement_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        drive_input = self._resolve_drive_input(
            drive_input=drive_input,
        )
        cognition_charter = self._resolve_endogenous_cognition_charter(
            getattr(self.config, "service_runtime", None)
        )
        deliberation_dict = deliberation.to_dict()
        perception = deliberation_dict.get("perception", {})
        world_model = deliberation_dict.get("world_model", {})
        reflection = deliberation_dict.get("reflection", {})
        adaptive_policy = deliberation_dict.get("adaptive_policy", {})
        shell_slot = dict(self._get_shell_slot_meta(drive_input) or {})
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
            drive_input=drive_input,
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
            "grounding_gaps": self._reference_alignment_gap_labels(
                recent_reference_alignment
            )[:6],
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
        api_b_judgement_snapshot = self._build_api_b_judgement_snapshot(drive_context)
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
            api_b_judgement_snapshot=api_b_judgement_snapshot,
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            evidence_channels=evidence_channels,
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
            "recent_learning_titles": list(drive_context.get("recent_learning_titles") or [])[:8],
            "recent_learning_evidence": recent_learning_evidence,
            "external_research_evidence": external_research_evidence,
            "learning_backlog_titles": list(drive_context.get("learning_backlog_titles") or [])[:8],
            "body_improvement_backlog_titles": list(drive_context.get("body_improvement_backlog_titles") or [])[:8],
            "api_b_judgement_tasks": list(drive_context.get("api_b_judgement_tasks") or [])[:12],
            "checks": dict(drive_input.get("checks") or {}),
            "idle_seconds": dict(drive_input.get("idle_seconds") or {}),
            "shell_slot": shell_slot,
            "shell_body_profile": shell_body_profile,
        }

    def _build_api_b_judgement_snapshot(
        self,
        drive_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        api_b_judgement_tasks = [
            dict(item)
            for item in list(drive_context.get("api_b_judgement_tasks") or [])[:8]
            if isinstance(item, dict)
        ]
        learning_backlog_titles = [
            str(item).strip()
            for item in list(drive_context.get("learning_backlog_titles") or [])[:5]
            if str(item).strip()
        ]
        body_improvement_backlog_titles = [
            str(item).strip()
            for item in list(drive_context.get("body_improvement_backlog_titles") or [])[:4]
            if str(item).strip()
        ]
        if not api_b_judgement_tasks and not learning_backlog_titles and not body_improvement_backlog_titles:
            return {}

        recent_titles = [
            str(item.get("title") or "").strip()
            for item in api_b_judgement_tasks[:4]
            if str(item.get("title") or "").strip()
        ]
        recent_statuses = [
            str(item.get("status") or "").strip()
            for item in api_b_judgement_tasks[:4]
            if str(item.get("status") or "").strip()
        ]
        return {
            "api_b_judgement_task_count": len(api_b_judgement_tasks),
            "learning_backlog_count": len(learning_backlog_titles),
            "body_improvement_backlog_count": len(body_improvement_backlog_titles),
            "recent_titles": recent_titles,
            "recent_statuses": recent_statuses,
            "summary": (
                f"API-B 判断在途 {len(api_b_judgement_tasks)} 项，"
                f"学习 {len(learning_backlog_titles)} 项，"
                f"替身改进 {len(body_improvement_backlog_titles)} 项；"
                f"最近：{', '.join(recent_titles[:3]) or '无'}。"
            ),
            "guidance": (
                "除非新证据明显更强，否则不要重复提出与现有 API-B 判断在途等价的工作。"
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
        api_b_judgement_snapshot: Dict[str, Any],
        recent_learning_evidence: List[Dict[str, Any]],
        external_research_evidence: List[Dict[str, Any]],
        evidence_channels: Dict[str, Any],
        recent_learning_titles: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        layering_policy = self._resolve_cognitive_context_layering_policy(
            cognition_charter
        )

        decision_core = {
            "current_judgement": str(
                meta_cognition_profile.get("current_judgement")
                or cognitive_assessment_memory.get("current_judgement")
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
            "secondary_task_shape_hint": str(
                task_type_priors.get("top_priority_task_type")
                or ""
            ).strip(),
            "secondary_task_shape_score": round(
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
                for item in list(grounding_focus.get("primary_evidence_nodes") or [])[:3]
                if str(item).strip()
            ],
            "primary_agenda_nodes": [
                str(item).strip()
                for item in list(grounding_focus.get("primary_agenda_nodes") or [])[:3]
                if str(item).strip()
            ],
            "api_b_judgement_summary": str(api_b_judgement_snapshot.get("summary") or "").strip(),
            "cognitive_posture": {
                "name": str(cognitive_posture.get("name") or "").strip(),
                "selection_reason": str(
                    cognitive_posture.get("selection_reason") or ""
                ).strip(),
            },
            "summary": (
                "判断核心："
                f"当前判断={str(meta_cognition_profile.get('current_judgement') or 'unknown').strip() or 'unknown'}；"
                f"主约束={str(meta_cognition_profile.get('dominant_constraint') or 'unknown').strip() or 'unknown'}；"
                f"治理姿态={str(meta_cognition_profile.get('governance_posture') or meta_cognition_profile.get('recommended_task_posture') or 'unknown').strip() or 'unknown'}；"
                f"任务形态提示={str(task_type_priors.get('top_priority_task_type') or 'unknown').strip() or 'unknown'}；"
                f"首要自我迭代域={str(meta_cognition_profile.get('top_self_iteration_domain') or 'unknown').strip() or 'unknown'}。"
            ),
        }

        readiness = dict(self_model_snapshot.get("readiness") or {})
        supporting_detail = {
            "grounding_gaps": [
                str(item).strip()
                for item in list(grounding_focus.get("grounding_gaps") or [])[:4]
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
                item
                for item in [
                    str(cognitive_assessment_memory.get("why_not_improvement_now") or "").strip()
                ]
                if item
            ][:4],
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
                for item in list(recent_learning_evidence or [])[:2]
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ],
            "external_research_titles": [
                str(item.get("title") or "").strip()
                for item in list(external_research_evidence or [])[:3]
                if isinstance(item, dict) and str(item.get("title") or "").strip()
            ],
            "evidence_channels": channel_rows,
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
            "secondary_task_shape_hint": decision_core.get("secondary_task_shape_hint"),
            "secondary_task_shape_score": decision_core.get("secondary_task_shape_score"),
            "top_self_iteration_domain": decision_core.get("top_self_iteration_domain"),
            "top_self_iteration_hypothesis": decision_core.get("top_self_iteration_hypothesis"),
            "primary_evidence_nodes": decision_core.get("primary_evidence_nodes"),
            "primary_agenda_nodes": decision_core.get("primary_agenda_nodes"),
            "api_b_judgement_summary": decision_core.get("api_b_judgement_summary"),
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

    def _reference_alignment_gap_labels(
        self,
        recent_reference_alignment: Dict[str, Any],
    ) -> List[str]:
        labels: List[str] = []
        primary_evidence = str(
            recent_reference_alignment.get("primary_missing_evidence_node") or ""
        ).strip()
        primary_agenda = str(
            recent_reference_alignment.get("primary_missing_agenda_node") or ""
        ).strip()
        if primary_evidence:
            labels.append(f"missing_evidence:{primary_evidence}")
        if primary_agenda:
            labels.append(f"missing_agenda:{primary_agenda}")
        for entry in list(recent_reference_alignment.get("recent_entries") or [])[:3]:
            if not isinstance(entry, dict):
                continue
            for node in list(entry.get("missing_evidence_nodes") or [])[:2]:
                value = str(node).strip()
                label = f"missing_evidence:{value}" if value else ""
                if label and label not in labels:
                    labels.append(label)
            for node in list(entry.get("missing_agenda_nodes") or [])[:2]:
                value = str(node).strip()
                label = f"missing_agenda:{value}" if value else ""
                if label and label not in labels:
                    labels.append(label)
        return labels

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
                "secondary_task_shape_hint",
                "secondary_task_shape_score",
                "top_self_iteration_domain",
                "top_self_iteration_hypothesis",
                "primary_evidence_nodes",
                "primary_agenda_nodes",
                "api_b_judgement_summary",
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
            self._latest_lm_task_generation_proposals = []
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="disabled",
                proposal_count=0,
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
                self._latest_lm_task_generation_proposals = []
                self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                    evidence_packet=evidence_packet,
                    cognition_charter=cognition_charter,
                    role=role,
                    max_candidates=max_candidates,
                    status="llm_unavailable",
                    proposal_count=0,
                    error="llm_client_unavailable",
                )
                return []
        except Exception as exc:
            self._latest_lm_task_generation_proposals = []
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="llm_unavailable",
                proposal_count=0,
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
            self._latest_lm_task_generation_proposals = []
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="generation_error",
                proposal_count=0,
                error=str(exc),
            )
            return []
        if not isinstance(result, dict):
            self._latest_lm_task_generation_proposals = []
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="invalid_response",
                proposal_count=0,
                error="non_dict_response",
            )
            return []
        cognitive_assessment = self._normalize_lm_cognitive_assessment(
            result.get("cognitive_assessment")
        )
        proposals = result.get("proposals")
        if not isinstance(proposals, list):
            self._latest_lm_task_generation_proposals = []
            self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
                evidence_packet=evidence_packet,
                cognition_charter=cognition_charter,
                role=role,
                max_candidates=max_candidates,
                status="invalid_response",
                proposal_count=0,
                cognitive_assessment=cognitive_assessment,
                error="missing_proposals_list",
            )
            return []
        normalized_proposals = [dict(item) for item in proposals if isinstance(item, dict)]
        self._latest_lm_task_generation_proposals = [dict(item) for item in normalized_proposals]
        self._latest_lm_task_generation_context = self._build_lm_task_generation_context_snapshot(
            evidence_packet=evidence_packet,
            cognition_charter=cognition_charter,
            role=role,
            max_candidates=max_candidates,
            status="completed",
            proposal_count=len(normalized_proposals),
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
                "secondary_task_shape_hint",
                "secondary_task_shape_score",
                "top_self_iteration_domain",
                "top_self_iteration_hypothesis",
                "primary_evidence_nodes",
                "primary_agenda_nodes",
                "api_b_judgement_summary",
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
                "api_b_judgement_snapshot",
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
                "learning_backlog_titles",
                "body_improvement_backlog_titles",
                "api_b_judgement_tasks",
                "shell_slot",
            ]
        if not list(prompt_attention_policy.get("structure_keys") or []):
            prompt_attention_policy["structure_keys"] = [
                "decision_core",
                "supporting_detail",
                "long_tail_context",
                "api_b_judgement_snapshot",
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
                int(
                    policy.get("auto_truthfulness_correction_signal_threshold")
                    or TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
                ),
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
                or dominant_constraint
                in {
                    _API_B_JUDGEMENT_BLOCKAGE,
                    "historical_underdelivery",
                }
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
        entry_count = 0
        score_total = 0.0
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
            score_total += float(cognitive_alignment.get("score") or 0.0)
            entry_count += 1
            if entry_count >= 4:
                break
        if not entry_count:
            return {
                "available": False,
                "average_score": 0.0,
                "quality_counts": {},
            }
        average_score = score_total / entry_count
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
        cognitive_assessment: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        self_model_snapshot = dict(evidence_packet.get("self_model_snapshot") or {})
        readiness = dict(self_model_snapshot.get("readiness") or {})
        evidence_credibility_summary = dict(
            evidence_packet.get("evidence_credibility_summary") or {}
        )
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
        summary = (
            f"LM 认知状态={status}；"
            f"提案漂移={str(proposal_drift_memory.get('drift_state') or 'unknown').strip() or 'unknown'}。"
        )
        if error:
            summary += f" 异常={error}。"

        def _texts(values: Any, *, limit: int = 4) -> List[str]:
            raw_values = [values] if isinstance(values, str) else list(values or [])
            return [
                str(item).strip()
                for item in raw_values[:limit]
                if str(item).strip()
            ]

        def _text_count(values: Any) -> int:
            raw_values = [values] if isinstance(values, str) else list(values or [])
            return sum(1 for item in raw_values if str(item).strip())

        def _dominant_text(
            mapping: Dict[str, Any],
            primary_key: str | tuple[str, ...],
            *,
            limit: int = 4,
        ) -> str:
            del limit
            primary_keys = (primary_key,) if isinstance(primary_key, str) else primary_key
            for key in primary_keys:
                primary = str(mapping.get(key) or "").strip()
                if primary:
                    return primary
            return ""

        def _signal_count(
            mapping: Dict[str, Any],
            primary_keys: tuple[str, ...],
        ) -> int:
            for key in primary_keys:
                value = mapping.get(key)
                if value is None:
                    continue
                try:
                    return max(0, int(value or 0))
                except (TypeError, ValueError):
                    continue
            return 0

        def _stored_count(mapping: Dict[str, Any], key: str) -> int:
            try:
                return max(0, int(mapping.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        self_iteration_hypothesis_rows = [
            dict(item)
            for item in list(self_iteration_hypotheses.get("hypotheses") or [])
            if isinstance(item, dict) and str(item.get("hypothesis") or "").strip()
        ]
        dominant_hypothesis_row = (
            self_iteration_hypothesis_rows[0] if self_iteration_hypothesis_rows else {}
        )
        dominant_trend_hypothesis = _dominant_text(
            self_iteration_trend_memory,
            "dominant_hypothesis",
        )
        dominant_stay_or_switch = _dominant_text(
            self_iteration_trend_memory,
            ("dominant_stay_or_switch", "stay_or_switch"),
            limit=2,
        )
        dominant_switch_reason = _dominant_text(
            self_iteration_trend_memory,
            ("dominant_switch_reason", "switch_reason"),
        )
        current_judgement = _dominant_text(
            cognitive_assessment_memory,
            "current_judgement",
        )
        why_not_improvement_now = _dominant_text(
            cognitive_assessment_memory,
            "why_not_improvement_now",
        )
        self_iteration_target = _dominant_text(
            cognitive_assessment_memory,
            "self_iteration_target",
        )
        self_iteration_assessment_hypothesis = _dominant_text(
            cognitive_assessment_memory,
            "self_iteration_hypothesis",
        )
        return {
            "status": status,
            "model_role": role,
            "max_candidates": max(0, int(max_candidates)),
            "proposal_count": max(0, int(proposal_count)),
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
                "priority_signals": [
                    str(item).strip()
                    for item in list(meta_cognition_profile.get("priority_signals") or [])[:6]
                    if str(item).strip()
                ],
            },
            "self_iteration_hypotheses": {
                "available": bool(self_iteration_hypotheses.get("available")),
                "dominant_hypothesis": str(
                    self_iteration_hypotheses.get("dominant_hypothesis")
                    or dominant_hypothesis_row.get("hypothesis")
                    or ""
                ).strip(),
                "top_target_domain": str(
                    self_iteration_hypotheses.get("top_target_domain")
                    or dominant_hypothesis_row.get("target_domain")
                    or ""
                ).strip(),
                "hypothesis_count": max(
                    (
                        _stored_count(self_iteration_hypotheses, "hypothesis_count")
                        if self_iteration_hypotheses.get("hypothesis_count") is not None
                        else len(self_iteration_hypothesis_rows)
                    ),
                    1
                    if str(
                        self_iteration_hypotheses.get("dominant_hypothesis")
                        or dominant_hypothesis_row.get("hypothesis")
                        or ""
                    ).strip()
                    else 0,
                ),
                "top_priority": round(
                    self._clamp01(
                        self_iteration_hypotheses.get("top_priority")
                        or dominant_hypothesis_row.get("priority")
                        or 0.0
                    ),
                    4,
                ),
                "suggested_task_types": _texts(
                    self_iteration_hypotheses.get("suggested_task_types")
                    or dominant_hypothesis_row.get("suggested_task_types"),
                    limit=3,
                ),
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
                "dominant_hypothesis": dominant_trend_hypothesis,
                "dominant_stay_or_switch": dominant_stay_or_switch,
                "dominant_switch_reason": dominant_switch_reason,
                "target_count": _signal_count(
                    self_iteration_trend_memory,
                    ("target_count", "target_signal_count"),
                ),
                "hypothesis_count": _signal_count(
                    self_iteration_trend_memory,
                    ("hypothesis_count", "hypothesis_signal_count"),
                ),
                "stay_or_switch_count": _signal_count(
                    self_iteration_trend_memory,
                    ("stay_or_switch_count", "stay_or_switch_signal_count"),
                ),
                "switch_reason_count": _signal_count(
                    self_iteration_trend_memory,
                    ("switch_reason_count", "switch_reason_signal_count"),
                ),
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
                "current_judgement": current_judgement,
                "why_not_improvement_now": why_not_improvement_now,
                "self_iteration_target": self_iteration_target,
                "self_iteration_hypothesis": self_iteration_assessment_hypothesis,
                "current_judgement_count": _signal_count(
                    cognitive_assessment_memory,
                    ("current_judgement_count",),
                ),
                "why_not_improvement_now_count": _signal_count(
                    cognitive_assessment_memory,
                    ("why_not_improvement_now_count",),
                ),
                "self_iteration_target_count": _signal_count(
                    cognitive_assessment_memory,
                    ("self_iteration_target_count", "target_count"),
                ),
                "self_iteration_hypothesis_count": _signal_count(
                    cognitive_assessment_memory,
                    ("self_iteration_hypothesis_count", "hypothesis_count"),
                ),
            },
            "proposal_drift_memory": {
                "available": bool(proposal_drift_memory.get("available")),
                "average_score": round(
                    self._clamp01(proposal_drift_memory.get("average_score") or 0.0),
                    4,
                ),
                "drift_state": str(proposal_drift_memory.get("drift_state") or "").strip(),
                "quality_counts": dict(proposal_drift_memory.get("quality_counts") or {}),
                "posture_alignment_signal_count": max(
                    0,
                    int(proposal_drift_memory.get("posture_alignment_signal_count") or 0),
                ),
                "priority_basis_signal_count": max(
                    0,
                    int(proposal_drift_memory.get("priority_basis_signal_count") or 0),
                ),
                "missing_posture_alignment_count": max(
                    0,
                    int(proposal_drift_memory.get("missing_posture_alignment_count") or 0),
                ),
                "missing_priority_basis_count": max(
                    0,
                    int(proposal_drift_memory.get("missing_priority_basis_count") or 0),
                ),
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
                "entry_count": max(
                    0,
                    int(recent_reference_alignment.get("entry_count") or 0),
                ),
                "primary_missing_evidence_node": str(
                    recent_reference_alignment.get("primary_missing_evidence_node") or ""
                ).strip(),
                "primary_missing_agenda_node": str(
                    recent_reference_alignment.get("primary_missing_agenda_node") or ""
                ).strip(),
                "missing_evidence_node_count": max(
                    0,
                    int(recent_reference_alignment.get("missing_evidence_node_count") or 0),
                ),
                "missing_agenda_node_count": max(
                    0,
                    int(recent_reference_alignment.get("missing_agenda_node_count") or 0),
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
            cognitive_assessment_memory.get("current_judgement") or ""
        ).strip()
        dominant_constraint = str(
            cognitive_assessment_memory.get("dominant_constraint") or ""
        ).strip()
        lm_self_iteration_target = str(
            cognitive_assessment_memory.get("self_iteration_target") or ""
        ).strip()
        lm_self_iteration_hypothesis = str(
            cognitive_assessment_memory.get("self_iteration_hypothesis") or ""
        ).strip()
        top_self_iteration_domain = str(
            lm_self_iteration_target
            or self_iteration_trend_memory.get("dominant_target")
            or self_iteration_hypotheses.get("top_target_domain")
            or ""
        ).strip()
        top_self_iteration_hypothesis = str(
            lm_self_iteration_hypothesis
            or self_iteration_trend_memory.get("dominant_hypothesis")
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
            self_iteration_trend_memory.get("dominant_stay_or_switch")
            or switch_self_regulation_memory.get("preferred_switch_bias")
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
        why_not_improvement_now = str(
            cognitive_assessment_memory.get("why_not_improvement_now") or ""
        ).strip()
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
        elif why_not_improvement_now:
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
        priority_signals = [item for item in priority_signals if item]

        if not has_substantive_profile:
            return {
                "available": False,
                "summary": "当前还没有可用的统一元认知画像。",
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
        body_projection = self._build_body_improvement_projection(
            drive_context=drive_context,
            shell_slot_meta=dict(evidence_packet.get("shell_slot") or {}),
        )
        self_evolution_plan = dict(
            dict(evidence_packet.get("plans") or {}).get("self_evolution") or {}
        )
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
            "governance_hygiene_review": {
                "stable_prefix": "lm:continuity:governance_hygiene",
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
        active_candidate_kinds = self._active_api_b_judgement_candidate_kinds(drive_context)

        for item in proposals:
            candidate_kind = str(item.get("candidate_kind") or "").strip()
            mapping = kind_map.get(candidate_kind)
            if mapping is None:
                continue
            if candidate_kind in active_candidate_kinds:
                continue
            if candidate_kind == "body_improvement" and (
                not self_evolution_plan.get("eligible_for_planning")
                or not body_projection.get("available")
                or adaptive_policy.body_growth_quota <= 0
            ):
                continue
            if (
                candidate_kind == "governance_hygiene_review"
                and not self._has_governance_hygiene_review_signal(perception)
                and not self._has_historical_governance_hygiene_review_signal(drive_context)
            ):
                continue
            title = str(item.get("title") or "").strip()
            summary = str(item.get("summary") or "").strip()
            if not title or not summary:
                continue
            stable_key = f"{mapping['stable_prefix']}:{_stable_key_for_topic(title)}"
            if candidate_kind == "body_improvement":
                stable_key = (
                    f"{mapping['stable_prefix']}:"
                    f"{body_projection['mapping_key']}"
                )
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
            body_metadata: Dict[str, Any] = {}
            body_evidence: Dict[str, Any] = {}
            if candidate_kind == "body_improvement":
                constraints.update(
                    self._body_improvement_constraints(body_projection)
                )
                body_metadata = {
                    "improvement_direction_source": body_projection.get(
                        "mapping_source"
                    ),
                    "target_paths": list(body_projection.get("target_paths") or []),
                    "structure_domains": list(
                        body_projection.get("structure_domains") or []
                    ),
                    "learning_task_ids": [
                        ref.get("mem_id")
                        for ref in list(body_projection.get("learning_refs") or [])
                    ],
                    "learning_quality_score": body_projection.get(
                        "learning_quality_score"
                    ),
                }
                body_evidence = {
                    "learning_quality_score": body_projection.get(
                        "learning_quality_score"
                    ),
                    "learning_refs": list(
                        body_projection.get("learning_refs") or []
                    ),
                    "structure_mapping": {
                        "source": body_projection.get("mapping_source"),
                        "domains": list(
                            body_projection.get("structure_domains") or []
                        ),
                        "target_paths": list(
                            body_projection.get("target_paths") or []
                        ),
                    },
                }
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
                        "backlog_pressure_penalty": self._backlog_pressure_penalty(
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
                        **body_metadata,
                        "drive_judgement": self._drive_judgement_metadata(
                            intent=intent,
                            candidate_kind=candidate_kind,
                            all_intents=list(deliberation.intents),
                            needs=list(deliberation.needs),
                            perception=deliberation.perception,
                            world_model=deliberation.world_model,
                            reflection=deliberation.reflection,
                            adaptive_policy=deliberation.adaptive_policy,
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
                        **body_evidence,
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
        if candidate_kind == "governance_hygiene_review":
            return {"must_not_execute_without_review": True}
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
            "governance_hygiene_review": "review",
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
            "governance_hygiene_review": "medium",
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
                normalized = candidate_kind in {"truthfulness_review", "governance_hygiene_review"}
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
        normalized = _LEGACY_LM_EXECUTION_MODE_ALIASES.get(normalized, normalized)
        if normalized not in _LM_EXECUTION_MODES:
            defaults = {
                "memory_maintenance": "guarded_execution",
                "truthfulness_review": "observe_only",
                "exploratory_learning": "review_then_handoff",
                "shell_baseline_learning": "review_then_handoff",
                "governance_hygiene_review": "review_then_handoff",
                "body_improvement": "guarded_execution",
            }
            normalized = defaults.get(candidate_kind, "review_then_handoff")
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
        alignment_quality = str(alignment.get("alignment_quality") or "").strip().lower()
        missing_primary_evidence_nodes = list(alignment.get("missing_primary_evidence_nodes") or [])
        missing_primary_agenda_nodes = list(alignment.get("missing_primary_agenda_nodes") or [])
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
        if recommended_observation_required and recommended_execution_mode == "guarded_execution":
            recommended_execution_mode = "review_then_handoff"
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

        manifest_path = worktree.parent / "worktree-origin.json"
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
        entry_count = 0
        score_total = 0.0
        weak_count = 0
        missing_evidence_counts: Dict[str, int] = {}
        missing_agenda_counts: Dict[str, int] = {}
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
            entry_count += 1
            score_total += self._clamp01(alignment.get("alignment_score") or 0.0)
            quality = str(alignment.get("alignment_quality") or "").strip().lower()
            if quality in {"weak", "partial", "drifted"}:
                weak_count += 1
            for node in list(alignment.get("missing_evidence_nodes") or [])[:4]:
                node_name = str(node).strip()
                if node_name:
                    missing_evidence_counts[node_name] = missing_evidence_counts.get(node_name, 0) + 1
            for node in list(alignment.get("missing_agenda_nodes") or [])[:4]:
                node_name = str(node).strip()
                if node_name:
                    missing_agenda_counts[node_name] = missing_agenda_counts.get(node_name, 0) + 1
            if entry_count >= 4:
                break

        if entry_count <= 0:
            return {
                "available": False,
                "summary": "No recent reference-alignment feedback is available yet.",
            }

        def _dominant_key(counts: Dict[str, int]) -> str:
            if not counts:
                return ""
            return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

        avg_score = score_total / entry_count
        missing_evidence_node_count = sum(missing_evidence_counts.values())
        missing_agenda_node_count = sum(missing_agenda_counts.values())
        return {
            "available": True,
            "entry_count": entry_count,
            "average_alignment_score": round(self._clamp01(avg_score), 4),
            "weak_or_partial_count": weak_count,
            "primary_missing_evidence_node": _dominant_key(missing_evidence_counts) or None,
            "primary_missing_agenda_node": _dominant_key(missing_agenda_counts) or None,
            "missing_evidence_node_count": missing_evidence_node_count,
            "missing_agenda_node_count": missing_agenda_node_count,
            "summary": (
                f"Recent proposals show average reference alignment {self._clamp01(avg_score):.2f}; "
                f"{weak_count} entries were weak/partial/drifted; "
                f"missing_evidence={missing_evidence_node_count}; "
                f"missing_agenda={missing_agenda_node_count}."
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
        entry_count = 0

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
            entry_count += 1
            if entry_count >= 4:
                break

        if not entry_count:
            return {
                "available": False,
                "summary": "当前还没有可用的近期 LM 认知评估记忆。",
            }

        def _dominant(counts: Dict[str, int]) -> str:
            if not counts:
                return ""
            return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

        current_judgement = _dominant(current_judgement_counts)
        why_not_improvement_now = _dominant(why_not_improvement_counts)
        self_iteration_target = _dominant(self_iteration_target_counts)
        self_iteration_hypothesis = _dominant(self_iteration_hypothesis_counts)
        dominant_constraint = ""
        if dominant_constraint_counts:
            dominant_constraint = sorted(
                dominant_constraint_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[0][0]
        primary_grounding_gap = _dominant(gap_counts)
        return {
            "available": True,
            "dominant_constraint": dominant_constraint or None,
            "current_judgement": current_judgement or None,
            "current_judgement_count": len(current_judgement_counts),
            "why_not_improvement_now": why_not_improvement_now or None,
            "why_not_improvement_now_count": len(why_not_improvement_counts),
            "self_iteration_target": self_iteration_target or None,
            "self_iteration_target_count": len(self_iteration_target_counts),
            "self_iteration_hypothesis": self_iteration_hypothesis or None,
            "self_iteration_hypothesis_count": len(self_iteration_hypothesis_counts),
            "primary_grounding_gap": primary_grounding_gap or None,
            "grounding_gap_count": len(gap_counts),
            "entry_count": entry_count,
            "summary": (
                "近期 LM 认知评估反复指向 "
                f"{current_judgement or '当前状态仍未稳定'}；"
                f"主约束={dominant_constraint or '未知'}。"
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
        entry_count = 0

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
            entry_count += 1
            if entry_count >= 6:
                break

        if not entry_count:
            return {
                "available": False,
                "summary": "No long-horizon self-iteration trend memory is available yet.",
            }

        ranked_targets = [
            item
            for item, _count in sorted(
                target_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        ranked_hypotheses = [
            item
            for item, _count in sorted(
                hypothesis_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        ranked_stay_or_switch = [
            item
            for item, _count in sorted(
                stay_switch_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:2]
        ]
        ranked_switch_reasons = [
            item
            for item, _count in sorted(
                switch_reason_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        dominant_target = ranked_targets[0] if ranked_targets else ""
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
            "dominant_target": dominant_target or None,
            "dominant_hypothesis": ranked_hypotheses[0] if ranked_hypotheses else None,
            "trend_state": trend_state,
            "target_stability": target_stability,
            "target_count": len(target_counts),
            "hypothesis_count": len(hypothesis_counts),
            "dominant_stay_or_switch": (
                ranked_stay_or_switch[0] if ranked_stay_or_switch else None
            ),
            "stay_or_switch_count": len(stay_switch_counts),
            "dominant_switch_reason": (
                ranked_switch_reasons[0] if ranked_switch_reasons else None
            ),
            "switch_reason_count": len(switch_reason_counts),
            "entry_count": entry_count,
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
        entry_count = 0

        for outcome in outcomes[:16]:
            event_type = str(outcome.get("event_type") or "").strip().lower()
            if event_type in {"", "planned"}:
                continue
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
            entry_count += 1
            if entry_count >= 6:
                break

        if not entry_count:
            return {
                "available": False,
                "summary": "当前还没有可用的任务后效记忆。",
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
            "entry_count": entry_count,
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
        why_not_improvement_now = str(
            cognitive_assessment_memory.get("why_not_improvement_now") or ""
        ).strip()
        why_not_improvement_evidence = (
            [why_not_improvement_now]
            if why_not_improvement_now
            else []
        )
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
                        "先扩展自我理解，再升级到不可逆的身体或策略变化"
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
                        f"posture_alignment_health:{proposal_drift_memory.get('posture_alignment_health') or 'unknown'}",
                        f"priority_basis_health:{proposal_drift_memory.get('priority_basis_health') or 'unknown'}",
                    ]
                    + (
                        [
                            "dominant_conflict:"
                            + str(
                                proposal_drift_memory.get(
                                    "dominant_posture_conflict_reason"
                                )
                                or ""
                            ).strip()
                        ]
                        if str(
                            proposal_drift_memory.get(
                                "dominant_posture_conflict_reason"
                            )
                            or ""
                        ).strip()
                        else []
                    ),
                    "suggested_task_types": ["review", "observation"],
                }
            )
        if why_not_improvement_evidence:
            hypotheses.append(
                {
                    "target_domain": "improvement_readiness",
                    "hypothesis": (
                        "clarify why improvement is being deferred so future self-iteration can become more decisive"
                    ),
                    "priority": self._clamp01(
                        0.46 + min(len(why_not_improvement_evidence), 4) * 0.04
                    ),
                    "evidence": why_not_improvement_evidence[:4],
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
                f"current task-type hint={top_priority_task_type or 'unknown'} ({top_priority_score:.2f})."
            ),
        }

    def _build_proposal_drift_memory(self, drive_context: Dict[str, Any]) -> Dict[str, Any]:
        drive_history = dict(drive_context.get("drive_history") or {})
        outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        entry_count = 0
        score_total = 0.0
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
            score_total += float(cognitive_alignment.get("score") or 0.0)
            entry_count += 1
            if entry_count >= 4:
                break

        if not entry_count:
            return {
                "available": False,
                "average_score": 0.0,
                "quality_counts": {},
                "drift_state": "unknown",
                "posture_alignment_signal_count": 0,
                "priority_basis_signal_count": 0,
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "posture_alignment_health": "unknown",
                "priority_basis_health": "unknown",
                "dominant_posture_conflict_reason": None,
                "summary": "No recent proposal-drift memory is available yet.",
            }

        avg_score = score_total / entry_count
        weak_or_partial = quality_counts["weak"] + quality_counts["partial"]
        drift_state = "stable"
        if quality_counts["weak"] >= 2 or avg_score < 0.45:
            drift_state = "drifting"
        elif (quality_counts["weak"] >= 1 and quality_counts["strong"] >= 1) or weak_or_partial >= 2:
            drift_state = "correcting"
        posture_alignment_signals = [
            item
            for item, _count in sorted(
                posture_alignment_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        priority_basis_signals = [
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
        elif quality_counts["weak"] >= 1 or not priority_basis_signals:
            priority_basis_health = "inconsistent"
        return {
            "available": True,
            "average_score": round(self._clamp01(avg_score), 4),
            "quality_counts": quality_counts,
            "drift_state": drift_state,
            "posture_alignment_signal_count": len(posture_alignment_signals),
            "priority_basis_signal_count": len(priority_basis_signals),
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
        governance_load_state = str(world_model.get("governance_load_state") or "unknown").strip()
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
            f"当前自我模型看到：主约束={dominant_constraint}，"
            f"偏好焦点={preferred_focus}，身体状态={body_profile_status}，"
            f"学习状态={learning_state}，治理健康={governance_load_state}。"
        )
        if self_understanding_gaps:
            summary += " 当前自我理解缺口包括：" + "，".join(self_understanding_gaps[:4]) + "。"

        return {
            "identity_view": {
                "role": "endogenous_supervisory_core",
                "responsibility": "先自我理解，再推进自我迭代",
                "execution_scope": "governance_only",
            },
            "current_state": {
                "user_mode": perception.get("user_mode"),
                "system_posture": perception.get("system_posture"),
                "dominant_constraint": dominant_constraint,
                "preferred_focus": preferred_focus,
                "governance_load_state": governance_load_state,
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
        if dominant_constraint == _API_B_JUDGEMENT_BLOCKAGE:
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
                f"as the safest task-type hint (score={float(top['score']):.2f}); "
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
            if dominant_constraint in {
                _API_B_JUDGEMENT_BLOCKAGE,
                "historical_underdelivery",
            }:
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
            if dominant_constraint == _API_B_JUDGEMENT_BLOCKAGE:
                reasons.append("maintenance_can_reduce_api_b_judgement_pressure")
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
            _REVIEW_API_B_JUDGEMENT_NEED: "learning_trace",
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

    def _build_drive_context(self, drive_input: Dict[str, Any]) -> Dict[str, Any]:
        policy = dict(drive_input.get("endogenous_drive_policy") or {})
        drive_history = dict(drive_input.get("drive_history") or {})
        api_b_judgement_tasks = list(drive_input.get("api_b_judgement_tasks") or [])
        api_a_execution_lane_tasks = list(drive_input.get("api_a_execution_lane_tasks") or [])
        autonomous_chain_live_tasks = list(
            drive_input.get("autonomous_chain_live_tasks")
            or [*api_b_judgement_tasks, *api_a_execution_lane_tasks]
        )
        completed_learning_tasks = list(drive_input.get("completed_learning_tasks") or [])

        recent_learning_titles = [
            str(task.get("title") or "").strip()
            for task in completed_learning_tasks
            if str(task.get("title") or "").strip()
        ]
        learning_backlog_titles = []
        body_improvement_backlog_titles = []
        signatures: list[set[str]] = []
        active_backlog_tasks: list[Dict[str, Any]] = []
        active_backlog_by_governance: dict[str, int] = {}
        active_backlog_by_family: dict[str, int] = {}
        active_backlog_by_execution_kind: dict[str, int] = {}
        stale_backlog_count = 0
        pending_review_count = 0
        api_a_handoff_count = 0
        api_a_running_count = 0
        now = datetime.now(timezone.utc)

        for title in recent_learning_titles:
            signatures.append(self._topic_signature(title))

        for task in autonomous_chain_live_tasks:
            title = str(task.get("title") or "").strip()
            if not title:
                continue
            status = str(task.get("status") or "").strip().lower()
            execution_kind = str(task.get("execution_kind") or "").strip().lower()
            governance_task_type = str(task.get("governance_task_type") or "").strip().lower()
            task_family = str(task.get("task_family") or "").strip().lower()
            if task_family == "self_learning" and status not in {"completed", "failed", "cancelled"}:
                learning_backlog_titles.append(title)
                signatures.append(self._topic_signature(title))
            if execution_kind == "body_improvement":
                body_improvement_backlog_titles.append(title)
            if status not in _TERMINAL_QUEUE_STATUSES and task in api_b_judgement_tasks:
                active_backlog_tasks.append(task)
                if governance_task_type:
                    active_backlog_by_governance[governance_task_type] = (
                        active_backlog_by_governance.get(governance_task_type, 0) + 1
                    )
                if task_family:
                    active_backlog_by_family[task_family] = (
                        active_backlog_by_family.get(task_family, 0) + 1
                    )
                if execution_kind:
                    active_backlog_by_execution_kind[execution_kind] = (
                        active_backlog_by_execution_kind.get(execution_kind, 0) + 1
                    )
                if status in _REVIEW_BACKLOG_STATUSES:
                    pending_review_count += 1
                updated_at = self._parse_timestamp(task.get("updated_at") or task.get("created_at"))
                if updated_at is not None and now - updated_at >= timedelta(hours=24):
                    stale_backlog_count += 1

        for task in api_a_execution_lane_tasks:
            status = str(task.get("status") or "").strip().lower()
            if status in {"approved", "retry"}:
                api_a_handoff_count += 1
            elif status == "running":
                api_a_running_count += 1

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
            "api_b_judgement_tasks": api_b_judgement_tasks,
            "api_a_execution_lane_tasks": api_a_execution_lane_tasks,
            "autonomous_chain_live_tasks": autonomous_chain_live_tasks,
            "completed_learning_tasks": completed_learning_tasks,
            "recent_learning_titles": recent_learning_titles,
            "learning_backlog_titles": learning_backlog_titles,
            "body_improvement_backlog_titles": body_improvement_backlog_titles,
            "recent_learning_signatures": signatures,
            "api_b_judgement_count": len(active_backlog_tasks),
            "active_backlog_by_governance": active_backlog_by_governance,
            "active_backlog_by_family": active_backlog_by_family,
            "active_backlog_by_execution_kind": active_backlog_by_execution_kind,
            "stale_backlog_count": stale_backlog_count,
            "pending_review_count": pending_review_count,
            "api_a_handoff_count": api_a_handoff_count,
            "api_a_ready_count": api_a_handoff_count,
            "api_a_running_count": api_a_running_count,
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
            elif status in {"approved", "deferred", "paused", "awaiting_review", "awaiting_user_consent", "retry"}:
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
                    "awaiting_user_consent",
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

    def _backlog_pressure_penalty(
        self,
        drive_context: Dict[str, Any],
        *,
        governance_task_type: Optional[str] = None,
        task_family: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ) -> float:
        total_active = int(drive_context.get("api_b_judgement_count") or 0)
        related = 0
        if governance_task_type:
            related += int(
                dict(drive_context.get("active_backlog_by_governance") or {}).get(
                    governance_task_type,
                    0,
                )
            )
        if task_family:
            related += int(
                dict(drive_context.get("active_backlog_by_family") or {}).get(
                    task_family,
                    0,
                )
            )
        if execution_kind:
            related += int(
                dict(drive_context.get("active_backlog_by_execution_kind") or {}).get(
                    execution_kind,
                    0,
                )
            )
        penalty = 0.02 * max(total_active - 1, 0) + 0.03 * related
        return round(min(penalty, 0.28), 4)

    def _memory_maintenance_urgency(self, drive_input: Dict[str, Any]) -> float:
        idle_seconds = dict(drive_input.get("idle_seconds") or {})
        api_a_execution_idle = idle_seconds.get("api_a_execution")
        coverage = [
            self._clamp01(float(value or 0) / 900.0)
            for value in (
                idle_seconds.get("user"),
                api_a_execution_idle,
                idle_seconds.get("memory"),
            )
        ]
        avg_idle_coverage = sum(coverage) / len(coverage) if coverage else 0.0
        # Whole-day execution (baseline §6): no time-of-day window bonus anymore.
        return round(self._clamp01(0.72 + avg_idle_coverage * 0.18), 4)

    def _idle_learning_urgency(
        self,
        *,
        active_sessions: int,
        topic_source: str,
        autonomous_chain_gate: bool,
    ) -> float:
        base = {
            "activity_metadata": 0.42,
            "shell_baseline_bootstrap": 0.55,
            "shell_baseline_fallback": 0.4,
        }.get(topic_source, 0.4)
        session_penalty = min(max(active_sessions, 0), 3) * 0.05
        autonomous_gate_bonus = 0.05 if autonomous_chain_gate else 0.0
        return round(self._clamp01(base - session_penalty + autonomous_gate_bonus), 4)

    def _governance_hygiene_urgency(self, drive_context: Dict[str, Any]) -> float:
        api_b_judgement_count = int(drive_context.get("api_b_judgement_count") or 0)
        stale_backlog_count = int(drive_context.get("stale_backlog_count") or 0)
        pending_review_count = int(drive_context.get("pending_review_count") or 0)
        urgency = (
            0.24
            + min(api_b_judgement_count, 5) * 0.08
            + min(stale_backlog_count + pending_review_count, 3) * 0.08
        )
        return round(self._clamp01(urgency), 4)

    def _has_recent_static_governance_completion(
        self,
        drive_context: Dict[str, Any],
        *,
        stable_key: str,
    ) -> bool:
        key = str(stable_key or "").strip()
        if not key:
            return False
        now = datetime.now(timezone.utc)
        for task in list(drive_context.get("api_b_judgement_tasks") or []):
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "").strip().lower()
            if status != "completed":
                continue
            metadata = dict(task.get("metadata") or {})
            evidence = dict(task.get("evidence") or {})
            task_key = str(
                metadata.get("endogenous_drive_key")
                or evidence.get("endogenous_drive_key")
                or ""
            ).strip()
            if task_key != key:
                continue
            completed_at = (
                metadata.get("completed_at")
                or task.get("updated_at")
                or task.get("created_at")
            )
            if self._within_cooldown(
                completed_at,
                now=now,
                cooldown_hours=_STATIC_GOVERNANCE_CANDIDATE_COOLDOWN_HOURS,
            ):
                return True
        return False

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
        api_b_judgement_tasks = list(drive_context.get("autonomous_chain_live_tasks") or [])

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
                api_b_judgement_tasks=api_b_judgement_tasks,
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
        api_b_judgement_tasks: List[Dict[str, Any]],
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

        for task in api_b_judgement_tasks:
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
        api_b_judgement_tasks = list(drive_context.get("api_b_judgement_tasks") or [])

        for task in api_b_judgement_tasks:
            execution_kind = str(task.get("execution_kind") or "").strip().lower()
            if execution_kind != "body_improvement":
                continue
            status = str(task.get("status") or "").strip().lower()
            if status not in {
                "planned", "approved", "running", "awaiting_user_consent", "paused", "deferred", "awaiting_review", "retry",
            }:
                continue
            target_slot_id = str(task.get("constraints", {}).get("target_slot_id") or "").strip()
            if not slot_id or not target_slot_id or slot_id == target_slot_id:
                return True

        for task in api_b_judgement_tasks:
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

    @staticmethod
    def _learning_evidence_freshness(completed_at: Any) -> float:
        text = str(completed_at or "").strip()
        if not text:
            return 0.0
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age_days = max(
                0.0,
                (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0,
            )
            return max(0.0, 1.0 - age_days / 90.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    @staticmethod
    def _canonical_body_editable_roots(policy: Dict[str, Any]) -> List[str]:
        configured = policy.get("body_improvement_editable_dirs")
        raw_roots = list(configured or AGENT_EVOLUTION_ALLOWED_PATHS)
        canonical_roots = [normalize_repo_path(path) for path in AGENT_EVOLUTION_ALLOWED_PATHS]
        canonical_files = {normalize_repo_path(path) for path in AGENT_EVOLUTION_ALLOWED_FILES}
        roots: List[str] = []
        for raw_root in raw_roots:
            root = normalize_repo_path(str(raw_root))
            if not root:
                continue
            if root in canonical_files:
                normalized = root
            else:
                normalized = root.rstrip("/") + "/"
                if not any(
                    normalized == canonical
                    or normalized.startswith(canonical)
                    for canonical in canonical_roots
                ):
                    continue
            if normalized not in roots:
                roots.append(normalized)
        return roots

    @staticmethod
    def _path_within_body_editable_roots(
        path: str,
        editable_roots: List[str],
        forbidden_patterns: List[str],
    ) -> bool:
        normalized = normalize_repo_path(path)
        if not normalized or not classify_agent_evolution_changes([normalized]).ok:
            return False
        if any(
            fnmatch.fnmatch(normalized, str(pattern).replace("\\", "/"))
            for pattern in forbidden_patterns
            if str(pattern).strip()
        ):
            return False
        return any(
            normalized == root.rstrip("/")
            if not root.endswith("/")
            else normalized.startswith(root)
            for root in editable_roots
        )

    @staticmethod
    def _body_structure_keyword_matches(text: str, keyword: str) -> bool:
        if keyword.isascii():
            return bool(
                re.search(
                    rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])",
                    text,
                )
            )
        return keyword in text

    def _build_body_improvement_projection(
        self,
        *,
        drive_context: Dict[str, Any],
        shell_slot_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        policy = dict(drive_context.get("policy") or {})
        completed_learning_tasks = [
            dict(task)
            for task in list(drive_context.get("completed_learning_tasks") or [])
            if isinstance(task, dict)
        ]
        shell_slot_id = str(shell_slot_meta.get("slot_id") or "").strip()
        shell_worktree = str(shell_slot_meta.get("worktree_path") or "").strip()
        if not shell_slot_id or not shell_worktree:
            return {"available": False, "reason": "shell_slot_unavailable"}
        if not completed_learning_tasks:
            return {"available": False, "reason": "learning_evidence_unavailable"}

        learning_quality_score = self._calculate_learning_quality_score(
            {"completed_learning_tasks": completed_learning_tasks}
        )
        min_quality = float(policy.get("body_improvement_min_quality") or 60.0)
        if learning_quality_score < min_quality:
            return {
                "available": False,
                "reason": "learning_quality_below_threshold",
                "learning_quality_score": round(learning_quality_score, 4),
                "required_learning_quality": round(min_quality, 4),
            }
        if self._has_recent_body_improvement(
            drive_context,
            shell_slot_meta=shell_slot_meta,
            cooldown_hours=int(policy.get("body_improvement_cooldown_hours") or 12),
        ):
            return {"available": False, "reason": "body_improvement_cooldown"}

        editable_roots = self._canonical_body_editable_roots(policy)
        if not editable_roots:
            return {"available": False, "reason": "no_canonical_editable_roots"}
        max_files = max(1, min(5, int(policy.get("body_improvement_max_files") or 5)))
        forbidden_patterns = [
            str(pattern).strip()
            for pattern in list(policy.get("body_improvement_forbidden_patterns") or [])
            if str(pattern).strip()
        ]

        ranked_learning: List[tuple[float, Dict[str, Any]]] = []
        for task in completed_learning_tasks:
            freshness = self._learning_evidence_freshness(task.get("completed_at"))
            if freshness <= 0.0:
                continue
            try:
                quality = float(task.get("quality_score"))
            except (TypeError, ValueError):
                quality = 0.5
            if quality > 1.0:
                quality /= 100.0
            quality = self._clamp01(quality)
            relevance = self._clamp01(quality * 0.65 + freshness * 0.35)
            ranked_learning.append((relevance, task))
        ranked_learning.sort(key=lambda item: item[0], reverse=True)

        target_paths: List[str] = []
        structure_domains: List[str] = []
        learning_refs: List[Dict[str, Any]] = []
        evidence_summary: List[str] = []

        for relevance, task in ranked_learning[:5]:
            learning_task_id = str(task.get("task_id") or "").strip()
            if not learning_task_id:
                continue
            text_parts = [
                str(task.get("title") or ""),
                str(task.get("summary") or ""),
                str(task.get("conclusion") or ""),
                *[
                    str(item)
                    for item in list(task.get("evidence_summary") or [])
                ],
            ]
            learning_text = "\n".join(part for part in text_parts if part.strip())
            normalized_text = learning_text.replace("\\", "/")
            task_targets: List[str] = []
            for match in _BODY_STRUCTURE_PATH_RE.findall(normalized_text):
                path = normalize_repo_path(match).rstrip(".,:;)]}")
                if self._path_within_body_editable_roots(
                    path,
                    editable_roots,
                    forbidden_patterns,
                ):
                    task_targets.append(path)
                    if "explicit_code_reference" not in structure_domains:
                        structure_domains.append("explicit_code_reference")

            if not task_targets:
                lowered = learning_text.lower()
                for domain, keywords, domain_targets in _BODY_STRUCTURE_DOMAIN_TARGETS:
                    if not any(
                        self._body_structure_keyword_matches(lowered, keyword)
                        for keyword in keywords
                    ):
                        continue
                    added_for_domain = False
                    for path in domain_targets:
                        if self._path_within_body_editable_roots(
                            path,
                            editable_roots,
                            forbidden_patterns,
                        ):
                            task_targets.append(path)
                            added_for_domain = True
                    if added_for_domain and domain not in structure_domains:
                        structure_domains.append(domain)

            task_targets = list(dict.fromkeys(task_targets))
            if not task_targets:
                continue
            for path in task_targets:
                if path not in target_paths and len(target_paths) < max_files:
                    target_paths.append(path)
            learning_refs.append(
                {
                    "mem_id": learning_task_id,
                    "timestamp": str(task.get("completed_at") or ""),
                    "relevance": round(relevance, 4),
                    "title": str(task.get("title") or "")[:200],
                    "target_paths": task_targets[:max_files],
                }
            )
            conclusion = str(task.get("conclusion") or task.get("summary") or "").strip()
            if conclusion:
                evidence_summary.append(conclusion[:400])
            if len(target_paths) >= max_files:
                break

        if not target_paths or not learning_refs:
            return {
                "available": False,
                "reason": "learning_evidence_has_no_safe_structure_mapping",
                "learning_quality_score": round(learning_quality_score, 4),
                "editable_roots": editable_roots,
            }

        mapping_key = _stable_key_for_topic(
            "|".join(
                [shell_slot_id, *target_paths, *[ref["mem_id"] for ref in learning_refs]]
            )
        ).rsplit(":", 1)[-1]
        return {
            "available": True,
            "mapping_key": mapping_key,
            "mapping_source": "learning_evidence_structure_projection_v1",
            "target_slot_id": shell_slot_id,
            "worktree_path": shell_worktree,
            "target_paths": target_paths,
            "structure_domains": structure_domains[:6],
            "editable_dirs": editable_roots,
            "forbidden_patterns": forbidden_patterns,
            "max_files_changed": max_files,
            "learning_quality_score": round(learning_quality_score, 4),
            "learning_refs": learning_refs,
            "evidence_summary": evidence_summary[:5],
        }

    @staticmethod
    def _body_improvement_constraints(projection: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "execution_policy": "improve_shell_body",
            "target_slot": "shell",
            "target_slot_id": projection["target_slot_id"],
            "worktree_path": projection["worktree_path"],
            "target_paths": list(projection.get("target_paths") or []),
            "editable_dirs": list(projection.get("editable_dirs") or []),
            "forbidden_patterns": list(projection.get("forbidden_patterns") or []),
            "max_files_changed": int(projection.get("max_files_changed") or 5),
            "must_commit": True,
            "evolution_boundary_check": True,
            "structure_mapping_source": projection.get("mapping_source"),
        }

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
        user_text = str(
            user_req.get("text")
            or user_req.get("query")
            or user_req.get("topic")
            or user_req.get("title")
            or ""
        )
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
                    autonomous_chain_gate=False,
                ),
                "novelty": 0.88 if bootstrap else 0.45,
                "specificity": 0.68 if bootstrap else 0.58,
                "execution_readiness": 0.92 if shell_worktree else 0.78,
                "backlog_pressure_penalty": self._backlog_pressure_penalty(
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

    def _calculate_learning_quality_score(self, drive_input: Dict[str, Any]) -> float:
        try:
            learning_tasks = drive_input.get("completed_learning_tasks", [])
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

    def _get_shell_slot_meta(self, drive_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            shell_slot = drive_input.get("shell_slot")
            if shell_slot and isinstance(shell_slot, dict):
                return shell_slot
        except Exception:
            pass
        return None

def _stable_key_for_topic(topic: str) -> str:
    """Generate a stable dedup key from a learning topic string.

    Uses a short hash so that genuinely different topics get different keys,
    allowing multiple creativity candidates to coexist in API-B judgement.
    """
    import hashlib
    normalized = topic.strip().lower()
    if not normalized:
        return "creativity:idle_learning:fallback"
    h = hashlib.md5(normalized.encode()).hexdigest()[:8]
    return f"creativity:idle_learning:{h}"

