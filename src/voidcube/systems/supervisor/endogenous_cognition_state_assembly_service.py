"""Assembly service for endogenous cognition state projections."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .endogenous_cognition_state import (
    build_cognition_state_projection,
    build_judgement_core_projection,
)
from .endogenous_proposal_cognition import (
    compact_proposal_memory,
    build_proposal_cognition_projection,
)
from .endogenous_strategy_projection import (
    build_attention_agenda_projection,
)
from .endogenous_uncertainty_projection import (
    build_uncertainty_ledger_projection,
)
from .endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)
from .endogenous_state_projection import derive_corrective_mode


class EndogenousCognitionStateAssemblyService:
    """Combine explicit projections into cognition state and proposal read models."""

    def __init__(
        self,
        *,
        load_drive_history: Callable[[], Dict[str, Any]],
        enabled: Callable[[], bool],
        drive_posture_from_deliberation: Callable[[Dict[str, Any]], Dict[str, Any]],
        derive_context_key: Callable[..., str],
        build_observation_program: Callable[..., Dict[str, Any]],
        build_meta_governance: Callable[..., Dict[str, Any]],
        load_reasoning_state: Callable[[], Dict[str, Any]],
        posture_service: Any,
        history_summary_service: Any,
    ) -> None:
        self._load_drive_history = load_drive_history
        self._enabled = enabled
        self._drive_posture_from_deliberation = drive_posture_from_deliberation
        self._derive_context_key = derive_context_key
        self._build_observation_program = build_observation_program
        self._build_meta_governance = build_meta_governance
        self._load_reasoning_state = load_reasoning_state
        self._posture_service = posture_service
        self._history_summary_service = history_summary_service


    def build(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        governance_event_stream: Dict[str, Any],
        self_regulation: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        history_snapshot = self._load_drive_history()
        perception = dict(deliberation.get("perception") or {})
        world_model = dict(deliberation.get("world_model") or {})
        reflection = dict(deliberation.get("reflection") or {})
        adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
        drive_posture = self._drive_posture_from_deliberation(deliberation)
        context_key = self._derive_context_key(deliberation=deliberation)
        strategy_memory = normalize_endogenous_strategy_memory(
            history_snapshot.get("strategy_memory")
        )
        corrective_mode = derive_corrective_mode(self_regulation)
        attention_agenda = build_attention_agenda_projection(
            deliberation=deliberation,
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
        )
        uncertainty_ledger = build_uncertainty_ledger_projection(
            deliberation=deliberation,
            governance_channels=governance_channels,
            self_regulation=self_regulation,
        )
        observation_program = self._build_observation_program(
            uncertainty_ledger=uncertainty_ledger,
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
            history=history_snapshot,
            context_key=context_key,
        )
        strategy_memory = normalize_endogenous_strategy_memory(
            history_snapshot.get("strategy_memory")
        )
        meta_governance = self._build_meta_governance(
            cognition_state_seed={
                "perception": perception,
                "world_model": world_model,
                "reflection": reflection,
                "adaptive_policy": adaptive_policy,
                "corrective_mode": corrective_mode,
                "attention_agenda": attention_agenda,
                "uncertainty_ledger": uncertainty_ledger,
                "observation_program": observation_program,
            },
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
            context_key=context_key,
            self_regulation=self_regulation,
            history=history_snapshot,
        )
        judgement_core = build_judgement_core_projection(
            deliberation=deliberation,
            governance_channels=governance_channels,
            attention_agenda=attention_agenda,
            uncertainty_ledger=uncertainty_ledger,
            observation_program=observation_program,
            meta_governance=meta_governance,
        )
        proposal_cognition = self._build_proposal_cognition(
            history_snapshot=history_snapshot,
            candidate_items=candidate_items,
            deliberation=deliberation,
            lm_reasoning_state=lm_reasoning_state,
        )
        return build_cognition_state_projection(
            enabled=bool(self._enabled()),
            deliberation=deliberation,
            governance_channels=governance_channels,
            governance_event_stream=governance_event_stream,
            self_regulation=self_regulation,
            drive_posture=drive_posture,
            context_key=context_key,
            strategy_memory=strategy_memory,
            corrective_mode=corrective_mode,
            attention_agenda=attention_agenda,
            uncertainty_ledger=uncertainty_ledger,
            observation_program=observation_program,
            meta_governance=meta_governance,
            judgement_core=judgement_core,
            proposal_cognition=proposal_cognition,
        )

    def _build_proposal_cognition(
        self,
        *,
        history_snapshot: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
        deliberation: Optional[Dict[str, Any]] = None,
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # This block is an auxiliary observation/tracking layer.  The main drive
        # judgement stays in judgement_core, attention_agenda, and meta_governance.
        if lm_reasoning_state is None:
            lm_reasoning_state = self._load_reasoning_state()
        else:
            lm_reasoning_state = dict(lm_reasoning_state)

        task_type_priors = dict(lm_reasoning_state.get("task_type_priors") or {})
        if not task_type_priors:
            task_shape_hint = dict(lm_reasoning_state.get("task_shape_hint") or {})
            shape = str(task_shape_hint.get("shape") or "").strip()
            alternatives = [
                {
                    "task_type": str(item.get("task_type") or item.get("shape") or "").strip(),
                    "score": item.get("score"),
                    "reasons": list(item.get("reasons") or [])[:3],
                }
                for item in list(task_shape_hint.get("alternatives") or [])[:5]
                if isinstance(item, dict)
                and str(item.get("task_type") or item.get("shape") or "").strip()
            ]
            if shape or alternatives:
                task_type_priors = {
                    "top_priority_task_type": shape,
                    "top_priority_score": task_shape_hint.get("score"),
                    "priors": alternatives,
                }
        meta_cognition_profile = dict(
            lm_reasoning_state.get("meta_cognition_profile") or {}
        )
        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        cognitive_assessment_memory = dict(
            lm_reasoning_state.get("cognitive_assessment_memory") or {}
        )
        if not cognitive_assessment_memory:
            cognitive_assessment_memory = self._history_summary_service._build_recent_lm_cognitive_assessment_summary(
                history_snapshot=history_snapshot,
            )
        if not recent_reference_alignment:
            recent_reference_alignment = self._history_summary_service._build_recent_reference_alignment_summary(
                history_snapshot=history_snapshot,
            )
        self_iteration_trend_memory = self._history_summary_service._build_recent_self_iteration_trend_summary(
            history_snapshot=history_snapshot,
        )
        switch_self_regulation_memory = self._history_summary_service._build_recent_switch_self_regulation_summary(
            history_snapshot=history_snapshot,
        )
        post_task_effect_memory = self._history_summary_service._build_recent_post_task_effect_summary(
            history_snapshot=history_snapshot,
        )
        if not meta_cognition_profile:
            meta_cognition_profile = self._history_summary_service._build_recent_meta_cognition_profile_summary(
                cognitive_assessment_memory=cognitive_assessment_memory,
                self_iteration_trend_memory=self_iteration_trend_memory,
                switch_self_regulation_memory=switch_self_regulation_memory,
                post_task_effect_memory=post_task_effect_memory,
                proposal_drift_memory=proposal_drift_memory,
                task_type_priors=task_type_priors,
                recent_reference_alignment=recent_reference_alignment,
            )
        self_iteration_hypotheses = dict(
            lm_reasoning_state.get("self_iteration_hypotheses") or {}
        )
        if not self_iteration_hypotheses:
            dominant_hypothesis = str(
                cognitive_assessment_memory.get("self_iteration_hypothesis")
                or self_iteration_trend_memory.get("dominant_hypothesis")
                or ""
            ).strip()
            hypothesis_count = max(
                1 if dominant_hypothesis else 0,
                max(0, int(cognitive_assessment_memory.get("self_iteration_hypothesis_count") or 0)),
                max(0, int(self_iteration_trend_memory.get("hypothesis_count") or 0)),
            )
            self_iteration_hypotheses = {
                "available": bool(dominant_hypothesis),
                "dominant_hypothesis": dominant_hypothesis,
                "top_target_domain": str(
                    meta_cognition_profile.get("top_self_iteration_domain")
                    or self_iteration_trend_memory.get("dominant_target")
                    or cognitive_assessment_memory.get("self_iteration_target")
                    or ""
                ).strip(),
                "hypothesis_count": hypothesis_count,
            }
        recent_cognitive_alignment = self._posture_service.recent_alignment(
            history_snapshot=history_snapshot,
        )
        current_candidates = self._history_summary_service._build_current_candidate_cognition_summary(
            candidate_items=candidate_items,
        )
        active_cognitive_posture_profile = self._posture_service.active_profile(
            lm_reasoning_state=lm_reasoning_state,
            history_snapshot=history_snapshot,
            deliberation=deliberation,
        )
        compact_memory = compact_proposal_memory(
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
            recent_cognitive_alignment=recent_cognitive_alignment,
            cognitive_assessment_memory=cognitive_assessment_memory,
            self_iteration_hypotheses=self_iteration_hypotheses,
            self_iteration_trend_memory=self_iteration_trend_memory,
            switch_self_regulation_memory=switch_self_regulation_memory,
            post_task_effect_memory=post_task_effect_memory,
        )

        return build_proposal_cognition_projection(
            lm_reasoning_state=lm_reasoning_state,
            cognitive_control_policy=(
                self._posture_service.current_policy()
            ),
            active_cognitive_posture_profile=active_cognitive_posture_profile,
            meta_cognition_profile=meta_cognition_profile,
            cognitive_assessment_memory=cognitive_assessment_memory,
            compact_memory=compact_memory,
            current_candidates=current_candidates,
        )

__all__ = ["EndogenousCognitionStateAssemblyService"]
