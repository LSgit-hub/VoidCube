"""Runtime service for endogenous cognitive self-regulation."""

from __future__ import annotations

from typing import Any, Dict

from .endogenous_policy import (
    HISTORICAL_OBSERVATION_CARRYOVER_RELEASED,
    TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD,
)


class EndogenousSelfRegulationService:
    """Derive bounded regulation signals from explicit cognitive snapshots."""

    _BOOST_KEYS = (
        "dynamic_candidate_throttle_boost",
        "dynamic_observation_bias_boost",
        "dynamic_truthfulness_bias_boost",
        "dynamic_learning_expansion_suppression",
    )

    def derive(
        self,
        *,
        policy: Dict[str, Any],
        posture_profile: Dict[str, Any],
        recent_cognitive_alignment: Dict[str, Any],
        lm_reasoning_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        regulation = {
            "dynamic_candidate_throttle_boost": 0.0,
            "dynamic_observation_bias_boost": 0.0,
            "dynamic_truthfulness_bias_boost": 0.0,
            "dynamic_learning_expansion_suppression": 0.0,
            "last_reason": None,
        }
        reasons: list[str] = []
        reasoning = dict(lm_reasoning_state or {})
        proposal_drift_memory = dict(reasoning.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            reasoning.get("recent_reference_alignment") or {}
        )
        evidence_basis = dict(reasoning.get("evidence_basis") or {})

        drift_state = self._text(proposal_drift_memory.get("drift_state"))
        drift_average_score = self._clamp_ratio(
            proposal_drift_memory.get("average_score") or 0.0
        )
        posture_alignment_health = self._text(
            proposal_drift_memory.get("posture_alignment_health")
        )
        priority_basis_health = self._text(
            proposal_drift_memory.get("priority_basis_health")
        )
        missing_posture_alignment_count = max(
            0,
            int(proposal_drift_memory.get("missing_posture_alignment_count") or 0),
        )
        missing_priority_basis_count = max(
            0,
            int(proposal_drift_memory.get("missing_priority_basis_count") or 0),
        )
        dominant_posture_conflict_reason = self._text(
            proposal_drift_memory.get("dominant_posture_conflict_reason")
        )
        reference_alignment_score = self._clamp_ratio(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        reference_alignment_available = (
            bool(recent_reference_alignment.get("available"))
            or "average_alignment_score" in recent_reference_alignment
            or weak_reference_count > 0
        )
        readiness_score = self._clamp_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
        readiness_available = "self_iteration_readiness_score" in evidence_basis
        weak_or_missing_channels = self._text_items(
            evidence_basis.get("weak_or_missing_channels"), limit=6
        )
        self_understanding_gaps = self._text_items(
            evidence_basis.get("self_understanding_gaps"), limit=6
        )
        alignment_average_score = self._clamp_ratio(
            recent_cognitive_alignment.get("average_score") or 0.0
        )
        alignment_quality_counts = dict(
            recent_cognitive_alignment.get("quality_counts") or {}
        )
        weak_alignment_count = max(
            0, int(alignment_quality_counts.get("weak") or 0)
        )
        partial_alignment_count = max(
            0, int(alignment_quality_counts.get("partial") or 0)
        )
        observation_multiplier = max(
            0.0, float(posture_profile.get("observation_multiplier") or 1.0)
        )
        throttle_multiplier = max(
            0.0, float(posture_profile.get("throttle_multiplier") or 1.0)
        )
        truthfulness_multiplier = max(
            0.0, float(posture_profile.get("truthfulness_multiplier") or 1.0)
        )
        learning_suppression_multiplier = max(
            0.0,
            float(posture_profile.get("learning_suppression_multiplier") or 1.0),
        )
        explanation_missing_pressure = max(
            missing_posture_alignment_count,
            missing_priority_basis_count,
        )
        explanation_inconsistent_pressure = int(
            posture_alignment_health == "inconsistent"
        ) + int(priority_basis_health == "inconsistent")
        explanation_missing_threshold = max(
            1,
            int(policy.get("auto_explanation_repair_missing_threshold") or 2),
        )
        explanation_inconsistent_threshold = max(
            1,
            int(policy.get("auto_explanation_repair_inconsistent_threshold") or 1),
        )

        if drift_state == "drifting":
            self._add(regulation, "dynamic_candidate_throttle_boost", policy, "drift_throttle_boost", throttle_multiplier)
            self._add(regulation, "dynamic_observation_bias_boost", policy, "drift_observation_boost", observation_multiplier)
            self._add(regulation, "dynamic_learning_expansion_suppression", policy, "drift_learning_suppression_boost", learning_suppression_multiplier)
            reasons.append("proposal_drift_is_active")
        elif drift_state == "correcting":
            self._add(regulation, "dynamic_candidate_throttle_boost", policy, "correcting_throttle_boost", throttle_multiplier)
            self._add(regulation, "dynamic_observation_bias_boost", policy, "correcting_observation_boost", observation_multiplier)
            self._add(regulation, "dynamic_learning_expansion_suppression", policy, "correcting_learning_suppression_boost", learning_suppression_multiplier)
            reasons.append("proposal_drift_is_being_corrected")

        posture_trigger_delta = float(posture_profile.get("drift_trigger_delta") or 0.0)
        drift_observe_trigger_score = self._clamp_ratio(
            float(policy.get("drift_observe_trigger_score") or 0.5)
            + posture_trigger_delta
        )
        drift_strong_trigger_score = self._clamp_ratio(
            float(policy.get("drift_strong_trigger_score") or 0.45)
            + posture_trigger_delta
        )
        if drift_average_score > 0.0 and drift_average_score < drift_observe_trigger_score:
            self._add(regulation, "dynamic_candidate_throttle_boost", policy, "low_alignment_throttle_boost", throttle_multiplier)
            self._add(regulation, "dynamic_observation_bias_boost", policy, "low_alignment_observation_boost", observation_multiplier)
            reasons.append("proposal_alignment_average_is_low")

        if explanation_missing_pressure >= explanation_missing_threshold:
            self._add(regulation, "dynamic_candidate_throttle_boost", policy, "explanation_missing_throttle_boost", throttle_multiplier)
            self._add(regulation, "dynamic_observation_bias_boost", policy, "explanation_missing_observation_boost", observation_multiplier)
            reasons.append("proposal_explanation_memory_is_missing")

        if explanation_inconsistent_pressure >= explanation_inconsistent_threshold:
            self._add(regulation, "dynamic_observation_bias_boost", policy, "explanation_inconsistent_observation_boost", observation_multiplier)
            self._add(regulation, "dynamic_truthfulness_bias_boost", policy, "explanation_inconsistent_truthfulness_boost", truthfulness_multiplier)
            self._add(regulation, "dynamic_learning_expansion_suppression", policy, "explanation_inconsistent_learning_suppression_boost", learning_suppression_multiplier)
            reasons.append(
                f"proposal_explanation_conflict:{dominant_posture_conflict_reason}"
                if dominant_posture_conflict_reason
                else "proposal_explanation_is_inconsistent"
            )

        if recent_cognitive_alignment.get("available"):
            weak_alignment_count_trigger = max(
                1, int(policy.get("weak_alignment_count_trigger") or 2)
            )
            if (
                weak_alignment_count >= weak_alignment_count_trigger
                or alignment_average_score < drift_strong_trigger_score
            ):
                self._add(regulation, "dynamic_candidate_throttle_boost", policy, "weak_alignment_throttle_boost", throttle_multiplier)
                self._add(regulation, "dynamic_observation_bias_boost", policy, "weak_alignment_observation_boost", observation_multiplier)
                self._add(regulation, "dynamic_learning_expansion_suppression", policy, "weak_alignment_learning_suppression_boost", learning_suppression_multiplier)
                reasons.append("recent_cognitive_alignment_is_weak")
            elif partial_alignment_count >= 2:
                self._add(regulation, "dynamic_observation_bias_boost", policy, "partial_alignment_observation_boost", observation_multiplier)
                reasons.append("recent_cognitive_alignment_remains_partial")

        reference_alignment_min_score = self._clamp_ratio(
            float(policy.get("reference_alignment_min_score") or 0.65)
            + float(posture_profile.get("reference_alignment_delta") or 0.0)
        )
        if reference_alignment_available and reference_alignment_score < reference_alignment_min_score:
            self._add(regulation, "dynamic_observation_bias_boost", policy, "weak_reference_observation_boost", observation_multiplier)
            self._add(regulation, "dynamic_truthfulness_bias_boost", policy, "weak_reference_truthfulness_boost", truthfulness_multiplier)
            reasons.append("reference_alignment_is_not_stable")

        weak_reference_count_trigger = max(
            1, int(policy.get("weak_reference_count_trigger") or 2)
        )
        if weak_reference_count >= weak_reference_count_trigger:
            self._add(regulation, "dynamic_candidate_throttle_boost", policy, "repeated_weak_reference_throttle_boost", throttle_multiplier)
            self._add(regulation, "dynamic_truthfulness_bias_boost", policy, "repeated_weak_reference_truthfulness_boost", truthfulness_multiplier)
            reasons.append("reference_alignment_has_multiple_weak_entries")

        readiness_min_score = self._clamp_ratio(
            float(policy.get("readiness_min_score") or 0.52)
            + float(posture_profile.get("readiness_delta") or 0.0)
        )
        if readiness_available and readiness_score < readiness_min_score:
            self._add(regulation, "dynamic_candidate_throttle_boost", policy, "low_readiness_throttle_boost", throttle_multiplier)
            self._add(regulation, "dynamic_observation_bias_boost", policy, "low_readiness_observation_boost", observation_multiplier)
            self._add(regulation, "dynamic_learning_expansion_suppression", policy, "low_readiness_learning_suppression_boost", learning_suppression_multiplier)
            reasons.append("self_iteration_readiness_is_low")

        if weak_or_missing_channels:
            channel_penalty = min(
                len(weak_or_missing_channels),
                max(1, int(policy.get("weak_channel_count_observe_cap") or 3)),
            )
            regulation["dynamic_observation_bias_boost"] += (
                float(policy.get("weak_channel_observation_step") or 0.0)
                * channel_penalty
                * observation_multiplier
            )
            regulation["dynamic_truthfulness_bias_boost"] += (
                float(policy.get("weak_channel_truthfulness_step") or 0.0)
                * channel_penalty
                * truthfulness_multiplier
            )
            reasons.append("weak_evidence_channels_require_more_observation")

        if self_understanding_gaps:
            gap_penalty = min(
                len(self_understanding_gaps),
                max(1, int(policy.get("self_gap_observe_cap") or 3)),
            )
            regulation["dynamic_candidate_throttle_boost"] += (
                float(policy.get("self_gap_throttle_step") or 0.0)
                * gap_penalty
                * throttle_multiplier
            )
            regulation["dynamic_observation_bias_boost"] += (
                float(policy.get("self_gap_observation_step") or 0.0)
                * gap_penalty
                * observation_multiplier
            )
            reasons.append("self_understanding_gaps_are_active")

        for key in self._BOOST_KEYS:
            regulation[key] = round(self._clamp_ratio(regulation[key]), 4)
        if reasons:
            regulation["last_reason"] = "; ".join(reasons[:6])
        return regulation

    def release_cleared_historical_observation_carryover(
        self,
        *,
        persisted_self_regulation: Dict[str, Any],
        cognitive_self_regulation: Dict[str, Any],
        deliberation: Dict[str, Any],
        lm_reasoning_state: Dict[str, Any],
        posture_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        adjusted = dict(cognitive_self_regulation or {})
        reflection = dict(deliberation.get("reflection") or {})
        perception = dict(deliberation.get("perception") or {})

        if str(reflection.get("dominant_constraint") or "").strip().lower() != "none":
            return adjusted
        if float(
            reflection.get("api_b_judgement_blockage_pressure") or 0.0
        ) >= 0.18:
            return adjusted
        if str(reflection.get("learning_yield_state") or "").strip().lower() not in {"mixed", "strong"}:
            return adjusted
        if max(
            0,
            int(
                perception.get("api_b_judgement_count")
                or 0
            ),
        ) > 0:
            return adjusted
        if max(0, int(perception.get("stale_backlog_count") or 0)) > 0:
            return adjusted
        if max(0, int(perception.get("pending_review_count") or 0)) > 0:
            return adjusted
        if (
            max(0, int(perception.get("correction_signals") or 0))
            >= TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
        ):
            return adjusted

        if str(posture_profile.get("name") or "").strip().lower() != "observe_first":
            return adjusted

        persisted_observation = float(
            persisted_self_regulation.get("dynamic_observation_bias_boost") or 0.0
        )
        persisted_throttle = float(
            persisted_self_regulation.get("dynamic_candidate_throttle_boost") or 0.0
        )
        persisted_learning_suppression = float(
            persisted_self_regulation.get("dynamic_learning_expansion_suppression") or 0.0
        )
        if max(
            persisted_observation,
            persisted_throttle,
            persisted_learning_suppression,
        ) < 0.08:
            return adjusted

        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        evidence_basis = dict(lm_reasoning_state.get("evidence_basis") or {})

        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        drift_average_score = self._clamp_ratio(
            proposal_drift_memory.get("average_score") or 0.0
        )
        reference_alignment_score = self._clamp_ratio(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        readiness_score = self._clamp_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
        weak_channel_count = len(
            [
                str(item).strip()
                for item in list(evidence_basis.get("weak_or_missing_channels") or [])[:6]
                if str(item).strip()
            ]
        )
        if drift_state != "correcting":
            return adjusted
        if drift_average_score < 0.42:
            return adjusted
        if reference_alignment_score < 0.58:
            return adjusted
        if weak_reference_count > 1:
            return adjusted
        if readiness_score < 0.48:
            return adjusted
        if weak_channel_count > 1:
            return adjusted

        observation_boost = float(
            cognitive_self_regulation.get("dynamic_observation_bias_boost") or 0.0
        )
        throttle_boost = float(
            cognitive_self_regulation.get("dynamic_candidate_throttle_boost") or 0.0
        )
        learning_suppression = float(
            cognitive_self_regulation.get("dynamic_learning_expansion_suppression") or 0.0
        )
        if max(observation_boost, throttle_boost, learning_suppression) < 0.12:
            return adjusted

        # Historical underdelivery is already cleared here, so do not let a
        # fresh corrective pass restack observation/throttle pressure on top
        # of decaying persisted guard carryover.
        adjusted["dynamic_observation_bias_boost"] = 0.0
        adjusted["dynamic_candidate_throttle_boost"] = 0.0
        adjusted["dynamic_learning_expansion_suppression"] = 0.0
        adjusted[HISTORICAL_OBSERVATION_CARRYOVER_RELEASED] = True
        reason = str(adjusted.get("last_reason") or "").strip()
        release_reason = "cleared_historical_window_releases_composite_observation_carryover"
        adjusted["last_reason"] = (
            f"{reason}; {release_reason}" if reason else release_reason
        )
        return adjusted


    @staticmethod
    def _add(
        regulation: Dict[str, Any],
        target: str,
        policy: Dict[str, Any],
        source: str,
        multiplier: float,
    ) -> None:
        regulation[target] += float(policy.get(source) or 0.0) * multiplier

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _text_items(value: Any, *, limit: int) -> list[str]:
        return [
            str(item).strip()
            for item in list(value or [])[:limit]
            if str(item).strip()
        ]

    @staticmethod
    def _clamp_ratio(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, numeric))
