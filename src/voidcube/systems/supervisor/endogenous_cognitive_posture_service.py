"""Runtime owner for cognitive posture and alignment projections."""

from __future__ import annotations

from typing import Any, Dict, Optional


class EndogenousCognitivePostureService:
    """Resolve posture and compact recent alignment from explicit snapshots."""

    def __init__(self, runtime_config: Any) -> None:
        self._runtime_config = runtime_config

    def current_policy(self) -> Dict[str, Any]:
        charter_model = getattr(
            self._runtime_config,
            "endogenous_drive_cognition_charter",
            None,
        )
        policy_model = getattr(charter_model, "cognitive_control_policy", None)
        if hasattr(policy_model, "model_dump"):
            return policy_model.model_dump(mode="json")
        return dict(policy_model or {})

    def resolve_profile(
        self,
        policy: Dict[str, Any],
        *,
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
        drive_history: Optional[Dict[str, Any]] = None,
        deliberation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selection_mode = str(policy.get("posture_selection_mode") or "auto").strip().lower()
        profile_name = str(policy.get("active_posture_profile") or "balanced").strip().lower()
        profiles = dict(policy.get("posture_profiles") or {})
        auto_selection_reason = "manual_selection"
        if selection_mode != "manual":
            profile_name, auto_selection_reason = self._select_profile_name(
                policy=policy,
                lm_reasoning_state=lm_reasoning_state or {},
                drive_history=drive_history or {},
                deliberation=deliberation or {},
            )
        selected = profiles.get(profile_name)
        if not isinstance(selected, dict):
            profile_name = "balanced"
            selected = profiles.get(profile_name) or {}
        resolved = dict(selected or {})
        resolved["name"] = profile_name
        resolved["selection_mode"] = selection_mode
        resolved["selection_reason"] = auto_selection_reason
        return resolved

    def _select_profile_name(
        self,
        *,
        policy: Dict[str, Any],
        lm_reasoning_state: Dict[str, Any],
        drive_history: Dict[str, Any],
        deliberation: Dict[str, Any],
    ) -> tuple[str, str]:
        perception = dict(deliberation.get("perception") or {})
        reflection = dict(deliberation.get("reflection") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        evidence_basis = dict(lm_reasoning_state.get("evidence_basis") or {})
        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_cognitive_alignment = self.recent_alignment(
            history_snapshot=drive_history,
        )

        correction_signals = max(0, int(perception.get("correction_signals") or 0))
        active_sessions = max(0, int(perception.get("active_sessions") or 0))
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        weak_channels = [
            str(item).strip()
            for item in list(evidence_basis.get("weak_or_missing_channels") or [])[:6]
            if str(item).strip()
        ]
        self_gaps = [
            str(item).strip()
            for item in list(evidence_basis.get("self_understanding_gaps") or [])[:6]
            if str(item).strip()
        ]
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
        readiness_score = self._clamp_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
        alignment_average_score = self._clamp_ratio(
            recent_cognitive_alignment.get("average_score") or 0.0
        )
        dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()

        service_active_sessions_threshold = max(
            0,
            int(policy.get("auto_service_active_sessions_threshold") or 1),
        )
        truthfulness_signal_threshold = max(
            1,
            int(
                policy.get("auto_truthfulness_correction_signal_threshold")
                or TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
            ),
        )
        evidence_repair_signal_threshold = max(
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

        if active_sessions >= service_active_sessions_threshold:
            return "conservative", "service_pressure_requires_conservative_posture"
        if correction_signals >= truthfulness_signal_threshold:
            return "truthfulness_first", "truthfulness_signals_are_elevated"
        if (
            explanation_missing_pressure >= explanation_missing_threshold
            and explanation_inconsistent_pressure >= explanation_inconsistent_threshold
        ):
            return "evidence_repair_first", "explanation_quality_requires_evidence_repair"
        if explanation_missing_pressure >= explanation_missing_threshold:
            return "observe_first", "missing_explanation_memory_requires_observation"
        if explanation_inconsistent_pressure >= explanation_inconsistent_threshold:
            if "truthfulness" in dominant_posture_conflict_reason or "reference_alignment" in dominant_posture_conflict_reason:
                return "truthfulness_first", "explanation_conflict_requires_truthfulness_repair"
            return "observe_first", "explanation_conflict_requires_observation"
        if (
            weak_reference_count >= evidence_repair_signal_threshold
            or len(weak_channels) >= evidence_repair_signal_threshold
            or "reference_alignment_is_unstable" in self_gaps
        ):
            return "evidence_repair_first", "evidence_repair_pressure_is_elevated"
        if (
            drift_state in {"drifting", "correcting"}
            or alignment_average_score < self._clamp_ratio(
                policy.get("drift_observe_trigger_score") or 0.5
            )
            or readiness_score < self._clamp_ratio(
                policy.get("readiness_min_score") or 0.52
            )
            or dominant_constraint in {"api_b_judgement_blockage", "historical_underdelivery"}
        ):
            return "observe_first", "drift_or_readiness_requires_observation"
        return "balanced", "balanced_posture_is_sufficient"

    def active_profile(
        self,
        *,
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
        history_snapshot: Optional[Dict[str, Any]] = None,
        deliberation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        policy = self.current_policy()
        return self.resolve_profile(
            policy,
            lm_reasoning_state=lm_reasoning_state,
            drive_history=history_snapshot,
            deliberation=deliberation,
        )

    def recent_alignment(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        entry_count = 0
        score_total = 0.0
        quality_counts: Dict[str, int] = {"strong": 0, "partial": 0, "weak": 0}
        reason_counts: Dict[str, int] = {}
        top_priority_counts: Dict[str, int] = {}
        posture_alignment_counts: Dict[str, int] = {}
        priority_basis_counts: Dict[str, int] = {}
        missing_posture_alignment_count = 0
        missing_priority_basis_count = 0

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
            suggested_task_shape = str(
                cognitive_alignment.get("top_priority_task_type") or ""
            ).strip().lower()
            if suggested_task_shape:
                top_priority_counts[suggested_task_shape] = (
                    top_priority_counts.get(suggested_task_shape, 0) + 1
                )
            reasons = [
                str(reason).strip()
                for reason in list(cognitive_alignment.get("reasons") or [])[:4]
                if str(reason).strip()
            ]
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
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            score_total += round(
                self._clamp_ratio(cognitive_alignment.get("score") or 0.0),
                4,
            )
            entry_count += 1
            if entry_count >= 4:
                break

        if not entry_count:
            return {
                "available": False,
                "average_score": 0.0,
                "quality_counts": {},
                "dominant_task_shape": None,
                "summary": "No recent cognitive alignment feedback is available yet.",
                "reason_count": 0,
                "posture_alignment_signal_count": 0,
                "priority_basis_signal_count": 0,
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "entry_count": 0,
            }

        average_score = score_total / entry_count
        dominant_task_shape = None
        if top_priority_counts:
            dominant_task_shape = max(
                top_priority_counts.items(),
                key=lambda item: (item[1], item[0]),
            )[0]
        summary = (
            f"Recent cognitive alignment average={average_score:.2f}; "
            f"dominant quality="
            f"{max(quality_counts.items(), key=lambda item: (item[1], item[0]))[0]}."
        )
        return {
            "available": True,
            "average_score": round(self._clamp_ratio(average_score), 4),
            "quality_counts": quality_counts,
            "dominant_task_shape": dominant_task_shape,
            "summary": summary,
            "reason_count": len(reason_counts),
            "posture_alignment_signal_count": len(posture_alignment_counts),
            "priority_basis_signal_count": len(priority_basis_counts),
            "missing_posture_alignment_count": missing_posture_alignment_count,
            "missing_priority_basis_count": missing_priority_basis_count,
            "entry_count": entry_count,
        }

    @staticmethod
    def _clamp_ratio(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, numeric))
