from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from systems.supervisor.config_models import (
    EndogenousDriveCognitionCharterConfig,
    EndogenousDriveCognitiveControlPolicyConfig,
)
from systems.supervisor.endogenous_candidate_pipeline import (
    EndogenousTaskCandidate as _EndogenousTaskCandidate,
)
from systems.supervisor.endogenous_materialization import (
    has_governance_hygiene_review_signal,
    has_historical_governance_hygiene_review_signal,
    materialize_lm_proposals,
    resolve_candidate_eligibility_plan,
    resolve_lm_candidate_eligibility,
)
from systems.supervisor.endogenous_adaptive_policy import (
    build_adaptive_policy_projection,
    strategy_context_key,
)
from systems.supervisor.endogenous_body_projection import (
    build_body_improvement_projection,
)
from systems.supervisor.endogenous_candidate_eligibility import (
    resolve_candidate_stream_eligibility,
)
from systems.supervisor.endogenous_candidate_stream import build_candidate_stream
from systems.supervisor.endogenous_agenda import build_agenda_graph
from systems.supervisor.endogenous_drive_models import (
    DriveAdaptivePolicy,
    DriveDeliberationReport,
    DriveIntent,
    DrivePerceptionSnapshot,
    DriveReflection,
    DriveSignal,
    DriveWorldModel,
)
from systems.supervisor.endogenous_needs import DriveNeed, detect_needs
from systems.supervisor.endogenous_drive_context import (
    build_drive_context,
    normalize_strategy_memory,
)
from systems.supervisor.endogenous_drive_state import (
    build_drive_perception_projection,
    build_drive_world_model_projection,
)
from systems.supervisor.endogenous_history import (
    normalize_historical_outcomes,
    summarize_historical_pressure,
)
from systems.supervisor.endogenous_self_iteration import (
    build_self_iteration_hypotheses,
)
from systems.supervisor.endogenous_task_priors import build_task_type_priors
from systems.supervisor.endogenous_intent_signal import (
    emit_drive_signal_projections,
    synthesize_intent_projections,
)
from systems.supervisor.endogenous_policy import (
    has_memory_backlog_recovery_window,
    has_truthfulness_review_signal,
)
from systems.supervisor.endogenous_lm_evidence import (
    assemble_lm_evidence_packet,
    build_grounding_focus,
)
from systems.supervisor.endogenous_reflection import build_reflection_projection
from systems.supervisor.endogenous_cognitive_posture import (
    resolve_cognitive_posture_from_policy,
)
from systems.supervisor.endogenous_meta_cognition import (
    build_meta_cognition_profile,
    build_proposal_drift_memory,
    build_recent_cognitive_alignment_summary,
)
from systems.supervisor.endogenous_cognitive_memory import (
    build_cognitive_assessment_memory,
    build_post_task_effect_memory,
    build_self_iteration_trend_memory,
    build_switch_self_regulation_memory,
)
from systems.supervisor.endogenous_cognition_charter import resolve_cognition_charter
from systems.supervisor.endogenous_self_model import (
    build_evidence_credibility_summary,
    build_recent_reference_alignment,
    build_self_model_snapshot,
)
from systems.supervisor.endogenous_api_b_snapshot import build_api_b_judgement_snapshot
from systems.supervisor.endogenous_research import build_external_research_evidence
from systems.supervisor.endogenous_shell_profile import build_shell_body_profile
from systems.supervisor.endogenous_pressure import (
    backlog_pressure_penalty,
    build_backlog_pressure_penalties,
    governance_hygiene_urgency,
    memory_maintenance_urgency,
)
from systems.supervisor.endogenous_generation_snapshot import (
    build_lm_task_generation_context_snapshot,
)
from systems.supervisor.endogenous_evidence import (
    build_evidence_channels,
    channel_confidence_from_body,
    channel_confidence_from_learning,
    channel_confidence_from_research,
    channel_strength_from_learning,
    channel_strength_from_research,
    normalize_recent_learning_evidence,
    research_freshness_hint,
)
from systems.supervisor.endogenous_proposals import (
    generate_lm_task_proposals,
    normalize_lm_cognitive_assessment,
)
_API_B_JUDGEMENT_BLOCKAGE = "api_b_judgement_blockage"
_REVIEW_API_B_JUDGEMENT_NEED = "review_api_b_judgement"


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
        drive_context = build_drive_context(drive_input)
        runtime_config = getattr(self.config, "service_runtime", None)
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        policy_model = getattr(charter_model, "cognitive_control_policy", None)
        if hasattr(policy_model, "model_dump"):
            policy = policy_model.model_dump(mode="json")
        else:
            policy = dict(policy_model or {})

        recent_reference_alignment = build_recent_reference_alignment(drive_context)
        proposal_drift_memory = build_proposal_drift_memory(drive_context)
        recent_learning_evidence = normalize_recent_learning_evidence(
            list(drive_context.get("completed_learning_tasks") or [])
        )
        service_runtime = getattr(self.config, "service_runtime", None)
        execution_config = getattr(self.config, "execution", None)
        external_research_evidence = build_external_research_evidence(
            enabled=bool(
                getattr(service_runtime, "endogenous_drive_external_research_enabled", False)
            ),
            entries=list(
                getattr(service_runtime, "endogenous_drive_external_research_entries", [])
                or []
            ),
            file_entries=list(
                getattr(service_runtime, "endogenous_drive_external_research_files", [])
                or []
            ),
            repo_root=getattr(execution_config, "git_repo_path", "./") or "./",
        )
        shell_slot = dict(self._get_shell_slot_meta(drive_input) or {})
        shell_body_profile = build_shell_body_profile(shell_slot)
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
        return resolve_cognitive_posture_from_policy(
            policy=policy,
            deliberation_dict=deliberation_dict,
            self_model_snapshot=self_model_snapshot,
            evidence_credibility_summary=evidence_credibility_summary,
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
            recent_cognitive_alignment=build_recent_cognitive_alignment_summary(
                drive_context.get("drive_history") or {}
            ),
        )

    def generate_candidates(
        self,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
        existing_drive_keys: Iterable[str],
        max_candidates: int = 3,
        deliberation_report: DriveDeliberationReport | None = None,
        lm_proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[_EndogenousTaskCandidate]:
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
        drive_context = build_drive_context(drive_input)
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
        memory_plan = resolve_candidate_eligibility_plan(
            "memory_maintenance",
            decisions_by_family,
            decisions_by_governance,
        )
        self_learning_plan = resolve_candidate_eligibility_plan(
            "self_learning",
            decisions_by_family,
            decisions_by_governance,
        )
        autonomous_improvement_plan = resolve_candidate_eligibility_plan(
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
        perception = DrivePerceptionSnapshot(
            **build_drive_perception_projection(
                drive_input=drive_input,
                activity=activity,
                drive_context=drive_context,
                counts=counts,
                correction_signals=correction_signals,
                shell_slot_meta=shell_slot_meta,
            )
        )
        world_model = DriveWorldModel(
            **build_drive_world_model_projection(perception)
        )
        reflection = DriveReflection(
            **build_reflection_projection(
                perception=perception,
                world_model=world_model,
                drive_context=drive_context,
                shell_slot_meta=shell_slot_meta,
            )
        )
        adaptive_policy = self._build_adaptive_policy(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            drive_context=drive_context,
        )
        needs = detect_needs(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            memory_plan=memory_plan,
            self_learning_plan=self_learning_plan,
            autonomous_improvement_plan=autonomous_improvement_plan,
            governance_review_need_type=_REVIEW_API_B_JUDGEMENT_NEED,
        )
        intents = self._synthesize_intents(
            needs=needs,
            perception=perception,
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
        strategy_memory = normalize_strategy_memory(
            drive_history.get("strategy_memory")
        )
        historical_outcomes = [
            dict(item)
            for item in list(drive_history.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        historical_outcomes = normalize_historical_outcomes(historical_outcomes)
        recent_historical_outcomes = historical_outcomes[:12]
        recent_self_learning_outcomes = [
            item
            for item in historical_outcomes
            if str(
                item.get("task_family")
                or item.get("governance_task_type")
                or ""
            ).strip().lower()
            == "self_learning"
        ][:12]
        historical_pressure = summarize_historical_pressure(
            recent_historical_outcomes=recent_historical_outcomes,
            recent_self_learning_outcomes=recent_self_learning_outcomes,
        )
        context_key = strategy_context_key(
            user_mode=perception.user_mode,
            system_posture=perception.system_posture,
            dominant_constraint=reflection.dominant_constraint,
        )
        projection = build_adaptive_policy_projection(
            perception=perception,
            world_model=world_model,
            reflection=reflection,
            policy=policy,
            strategy_memory=strategy_memory,
            historical_outcomes=historical_outcomes,
            historical_pressure=historical_pressure,
            context_key=context_key,
        )
        return DriveAdaptivePolicy(**projection)


    def _synthesize_intents(
        self,
        *,
        needs: List[DriveNeed],
        perception: DrivePerceptionSnapshot,
        reflection: DriveReflection,
        adaptive_policy: DriveAdaptivePolicy,
    ) -> List[DriveIntent]:
        return [
            DriveIntent(**projection)
            for projection in synthesize_intent_projections(
                needs=needs,
                perception=perception,
                reflection=reflection,
                adaptive_policy=adaptive_policy,
            )
        ]

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
        return [
            DriveSignal(**projection)
            for projection in emit_drive_signal_projections(
                perception=perception,
                world_model=world_model,
                reflection=reflection,
                adaptive_policy=adaptive_policy,
                needs=needs,
                intents=intents,
            )
        ]

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

    def _candidate_stream(
        self,
        drive_input: Dict[str, Any],
        *,
        existing_keys: set[str] = None,
        deliberation_report: DriveDeliberationReport | None = None,
        lm_proposals_override: Optional[List[Dict[str, Any]]] = None,
    ) -> List[_EndogenousTaskCandidate]:
        if existing_keys is None:
            existing_keys = set()
        activity = dict(drive_input.get("activity") or {})
        drive_context = build_drive_context(drive_input)
        policy = drive_context["policy"]
        shell_slot_meta = self._get_shell_slot_meta(drive_input) or {}
        decisions_by_family = dict(drive_input.get("task_family_decisions") or {})
        decisions_by_governance = dict(drive_input.get("governance_task_type_decisions") or {})
        memory_plan = resolve_candidate_eligibility_plan(
            "memory_maintenance", decisions_by_family, decisions_by_governance
        )
        self_learning_plan = resolve_candidate_eligibility_plan(
            "self_learning", decisions_by_family, decisions_by_governance
        )
        autonomous_improvement_plan = resolve_candidate_eligibility_plan(
            "general_self_evolution", decisions_by_family, decisions_by_governance
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
        body_projection = build_body_improvement_projection(
            drive_context=drive_context,
            shell_slot_meta=shell_slot_meta,
        )
        governance_signal_present = has_governance_hygiene_review_signal(
            perception.pending_review_count,
            perception.stale_backlog_count,
            perception.api_b_judgement_count,
        ) or has_historical_governance_hygiene_review_signal(
            list(dict(drive_context.get("drive_history") or {}).get("outcomes") or [])
        )
        eligibility = resolve_candidate_stream_eligibility(
            api_b_judgement_tasks=list(drive_context.get("api_b_judgement_tasks") or []),
            existing_keys=existing_keys,
            memory_planning_eligible=bool(memory_plan.get("eligible_for_planning")),
            self_learning_planning_eligible=bool(self_learning_plan.get("eligible_for_planning")),
            autonomous_improvement_planning_eligible=bool(
                autonomous_improvement_plan.get("eligible_for_planning")
            ),
            truthfulness_signal_present=has_truthfulness_review_signal(perception),
            shell_slot_id=str(shell_slot_meta.get("slot_id") or "shell"),
            shell_worktree=str(shell_slot_meta.get("worktree_path") or ""),
            has_learning_history=perception.has_learning_history,
            governance_signal_present=governance_signal_present,
            body_projection_available=bool(body_projection.get("available")),
            body_growth_blocked=reflection.body_growth_blocked,
            body_growth_quota=adaptive_policy.body_growth_quota,
        )
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
        backlog_pressure_penalties = build_backlog_pressure_penalties(drive_context)
        intents_by_kind = {
            str(intent.candidate_kind or ""): intent
            for intent in intents
            if intent.candidate_kind
        }
        drive_judgements = {
            candidate_kind: self._drive_judgement_metadata(
                intent=intents_by_kind.get(candidate_kind),
                candidate_kind=candidate_kind,
                all_intents=intents,
                needs=needs,
                perception=perception,
                world_model=world_model,
                reflection=reflection,
                adaptive_policy=adaptive_policy,
            )
            for candidate_kind in (
                "memory_maintenance",
                "truthfulness_review",
                "shell_baseline_learning",
                "exploratory_learning",
                "governance_hygiene_review",
                "body_improvement",
            )
        }
        if eligibility.shell_baseline_learning or eligibility.exploratory_learning:
            cognitive_assessment_memory = build_cognitive_assessment_memory(drive_context)
            self_iteration_trend_memory = build_self_iteration_trend_memory(drive_context)
        else:
            cognitive_assessment_memory = {}
            self_iteration_trend_memory = {}
        return build_candidate_stream(
            drive_input=drive_input,
            activity=activity,
            drive_context=drive_context,
            policy=policy,
            shell_slot_meta=shell_slot_meta,
            existing_keys=existing_keys,
            perception=perception,
            adaptive_policy=adaptive_policy,
            eligibility=eligibility,
            body_projection=body_projection,
            lm_candidates=lm_candidates,
            cognitive_assessment_memory=cognitive_assessment_memory,
            self_iteration_trend_memory=self_iteration_trend_memory,
            backlog_pressure_penalties=backlog_pressure_penalties,
            memory_maintenance_urgency=memory_maintenance_urgency(drive_input),
            governance_hygiene_urgency=governance_hygiene_urgency(drive_context),
            drive_judgements=drive_judgements,
        )

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
    ) -> List[_EndogenousTaskCandidate]:
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
        runtime_config = getattr(self.config, "service_runtime", None)
        cognition_charter = resolve_cognition_charter(
            charter_model=getattr(runtime_config, "endogenous_drive_cognition_charter", None),
            core_mission=getattr(runtime_config, "endogenous_drive_core_mission_prompt", ""),
            task_generation_principles=getattr(
                runtime_config,
                "endogenous_drive_task_generation_principles",
                [],
            ),
        )
        deliberation_dict = deliberation.to_dict()
        perception = deliberation_dict.get("perception", {})
        world_model = deliberation_dict.get("world_model", {})
        reflection = deliberation_dict.get("reflection", {})
        adaptive_policy = deliberation_dict.get("adaptive_policy", {})
        shell_slot = dict(self._get_shell_slot_meta(drive_input) or {})
        recent_learning_evidence = normalize_recent_learning_evidence(
            list(drive_context.get("completed_learning_tasks") or [])
        )
        service_runtime = getattr(self.config, "service_runtime", None)
        execution_config = getattr(self.config, "execution", None)
        external_research_evidence = build_external_research_evidence(
            enabled=bool(
                getattr(service_runtime, "endogenous_drive_external_research_enabled", False)
            ),
            entries=list(
                getattr(service_runtime, "endogenous_drive_external_research_entries", [])
                or []
            ),
            file_entries=list(
                getattr(service_runtime, "endogenous_drive_external_research_files", [])
                or []
            ),
            repo_root=getattr(execution_config, "git_repo_path", "./") or "./",
        )
        shell_body_profile = build_shell_body_profile(shell_slot)
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
        recent_reference_alignment = build_recent_reference_alignment(drive_context)
        proposal_drift_memory = build_proposal_drift_memory(drive_context)
        cognitive_assessment_memory = build_cognitive_assessment_memory(drive_context)
        self_iteration_trend_memory = build_self_iteration_trend_memory(
            drive_context
        )
        switch_self_regulation_memory = build_switch_self_regulation_memory(
            drive_context
        )
        post_task_effect_memory = build_post_task_effect_memory(drive_context)
        self_model_snapshot = build_self_model_snapshot(
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
        evidence_credibility_summary = build_evidence_credibility_summary(
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            shell_body_profile=shell_body_profile,
            evidence_channels=evidence_channels,
            recent_reference_alignment=recent_reference_alignment,
        )
        task_type_priors = build_task_type_priors(
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
        api_b_judgement_snapshot = build_api_b_judgement_snapshot(drive_context)
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
            cognitive_posture=cognitive_posture,
            grounding_focus=grounding_focus,
            self_iteration_hypotheses=self_iteration_hypotheses,
            meta_cognition_profile=meta_cognition_profile,
            api_b_judgement_snapshot=api_b_judgement_snapshot,
            self_model_snapshot=self_model_snapshot,
            evidence_credibility_summary=evidence_credibility_summary,
            task_type_priors=task_type_priors,
            evidence_channels=evidence_channels,
            evidence_graph=evidence_graph,
            agenda_graph=agenda_graph,
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
            cognitive_assessment_memory=cognitive_assessment_memory,
            self_iteration_trend_memory=self_iteration_trend_memory,
            switch_self_regulation_memory=switch_self_regulation_memory,
            post_task_effect_memory=post_task_effect_memory,
            recent_learning_titles=list(drive_context.get("recent_learning_titles") or []),
            recent_learning_evidence=recent_learning_evidence,
            external_research_evidence=external_research_evidence,
            learning_backlog_titles=list(drive_context.get("learning_backlog_titles") or []),
            body_improvement_backlog_titles=list(drive_context.get("body_improvement_backlog_titles") or []),
            api_b_judgement_tasks=list(drive_context.get("api_b_judgement_tasks") or []),
            checks=dict(drive_input.get("checks") or {}),
            idle_seconds=dict(drive_input.get("idle_seconds") or {}),
            shell_slot=shell_slot,
            shell_body_profile=shell_body_profile,
        )

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
        cognition_charter = resolve_cognition_charter(
            charter_model=getattr(runtime_config, "endogenous_drive_cognition_charter", None),
            core_mission=getattr(runtime_config, "endogenous_drive_core_mission_prompt", ""),
            task_generation_principles=getattr(
                runtime_config,
                "endogenous_drive_task_generation_principles",
                [],
            ),
        )
        max_candidates = max(
            0,
            int(getattr(runtime_config, "endogenous_drive_lm_task_max_candidates", 3) or 3),
        )
        result = generate_lm_task_proposals(
            evidence_packet=evidence_packet,
            cognition_charter=cognition_charter,
            role=role,
            max_candidates=max_candidates,
        )
        proposals = [dict(item) for item in result.proposals]
        self._latest_lm_task_generation_proposals = proposals
        self._latest_lm_task_generation_context = build_lm_task_generation_context_snapshot(
            evidence_packet=evidence_packet,
            cognition_charter=cognition_charter,
            role=role,
            max_candidates=max_candidates,
            status=result.status,
            proposal_count=len(proposals),
            cognitive_assessment=result.cognitive_assessment,
            error=result.error,
        )
        return proposals

    def _materialize_lm_task_proposals(
        self,
        *,
        proposals: List[Dict[str, Any]],
        existing_keys: set[str],
        deliberation: DriveDeliberationReport,
        drive_context: Dict[str, Any],
        evidence_packet: Dict[str, Any],
    ) -> List[_EndogenousTaskCandidate]:
        perception = deliberation.perception
        adaptive_policy = deliberation.adaptive_policy
        evidence_graph = dict(evidence_packet.get("evidence_graph") or {})
        agenda_graph = dict(evidence_packet.get("agenda_graph") or {})
        batch_cognitive_assessment = normalize_lm_cognitive_assessment(
            self._latest_lm_task_generation_context.get("cognitive_assessment")
        )
        intent_by_kind = {
            str(intent.candidate_kind or "").strip(): intent
            for intent in deliberation.intents
            if intent.candidate_kind
        }
        body_projection = build_body_improvement_projection(
            drive_context=drive_context,
            shell_slot_meta=dict(evidence_packet.get("shell_slot") or {}),
        )
        self_evolution_plan = dict(
            dict(evidence_packet.get("plans") or {}).get("self_evolution") or {}
        )
        eligible_kinds = resolve_lm_candidate_eligibility(
            api_b_judgement_tasks=list(drive_context.get("api_b_judgement_tasks") or []),
            self_evolution_eligible=bool(self_evolution_plan.get("eligible_for_planning")),
            body_projection_available=bool(body_projection.get("available")),
            body_growth_quota=adaptive_policy.body_growth_quota,
            pending_review_count=perception.pending_review_count, stale_backlog_count=perception.stale_backlog_count, api_b_judgement_count=perception.api_b_judgement_count,
            historical_outcomes=list(dict(drive_context.get("drive_history") or {}).get("outcomes") or []),
        )

        def backlog_pressure(
            governance_task_type: str,
            task_family: str,
            execution_kind: Optional[str],
        ) -> float:
            return backlog_pressure_penalty(
                drive_context,
                governance_task_type=governance_task_type,
                task_family=task_family,
                execution_kind=execution_kind,
            )

        def drive_judgement(candidate_kind: str) -> Dict[str, Any]:
            return self._drive_judgement_metadata(
                intent=intent_by_kind.get(candidate_kind),
                candidate_kind=candidate_kind,
                all_intents=list(deliberation.intents),
                needs=list(deliberation.needs),
                perception=perception,
                world_model=deliberation.world_model,
                reflection=deliberation.reflection,
                adaptive_policy=adaptive_policy,
            )

        return materialize_lm_proposals(
            proposals=proposals,
            existing_keys=existing_keys,
            evidence_graph=evidence_graph,
            agenda_graph=agenda_graph,
            evidence_packet=evidence_packet,
            batch_cognitive_assessment=batch_cognitive_assessment,
            adaptive_policy=adaptive_policy,
            body_projection=body_projection,
            eligible_candidate_kinds=eligible_kinds,
            active_sessions=perception.active_sessions,
            backlog_pressure=backlog_pressure,
            drive_judgement=drive_judgement,
        )

    def _get_shell_slot_meta(self, drive_input: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            shell_slot = drive_input.get("shell_slot")
            if shell_slot and isinstance(shell_slot, dict):
                return shell_slot
        except Exception:
            pass
        return None

