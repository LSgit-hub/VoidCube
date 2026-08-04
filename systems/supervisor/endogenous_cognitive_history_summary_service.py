"""History-only cognitive summaries used by endogenous projections."""

from __future__ import annotations

from typing import Any, Dict


class EndogenousCognitiveHistorySummaryService:
    """Project bounded cognitive feedback summaries without runtime side effects."""

    @staticmethod
    def _clamp_endogenous_ratio(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


    def _build_recent_reference_alignment_summary(
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
        weak_or_partial_count = 0
        missing_evidence_counts: Dict[str, int] = {}
        missing_agenda_counts: Dict[str, int] = {}

        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            reference_alignment = outcome.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")
            if not isinstance(reference_alignment, dict) or not reference_alignment:
                continue
            entry_count += 1
            score_total += self._clamp_endogenous_ratio(
                reference_alignment.get("alignment_score") or 0.0
            )
            quality = str(reference_alignment.get("alignment_quality") or "").strip().lower()
            if quality in {"weak", "partial", "drifted"}:
                weak_or_partial_count += 1
            for node in list(reference_alignment.get("missing_evidence_nodes") or [])[:4]:
                node_name = str(node).strip()
                if node_name:
                    missing_evidence_counts[node_name] = missing_evidence_counts.get(node_name, 0) + 1
            for node in list(reference_alignment.get("missing_agenda_nodes") or [])[:4]:
                node_name = str(node).strip()
                if node_name:
                    missing_agenda_counts[node_name] = missing_agenda_counts.get(node_name, 0) + 1
            if entry_count >= 4:
                break

        if entry_count <= 0:
            return {
                "available": False,
                "summary": "No recent reference alignment is available yet.",
            }

        def _dominant_key(counts: Dict[str, int]) -> str:
            if not counts:
                return ""
            return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

        average_alignment_score = self._clamp_endogenous_ratio(score_total / entry_count)
        missing_evidence_node_count = sum(missing_evidence_counts.values())
        missing_agenda_node_count = sum(missing_agenda_counts.values())
        return {
            "available": True,
            "entry_count": entry_count,
            "average_alignment_score": round(average_alignment_score, 4),
            "weak_or_partial_count": weak_or_partial_count,
            "primary_missing_evidence_node": _dominant_key(missing_evidence_counts) or None,
            "primary_missing_agenda_node": _dominant_key(missing_agenda_counts) or None,
            "missing_evidence_node_count": missing_evidence_node_count,
            "missing_agenda_node_count": missing_agenda_node_count,
            "summary": (
                "Recent reference alignment from history remains available; "
                f"entries={entry_count}; "
                f"avg_alignment={average_alignment_score:.2f}; "
                f"weak_or_partial={weak_or_partial_count}; "
                f"missing_evidence={missing_evidence_node_count}; "
                f"missing_agenda={missing_agenda_node_count}."
            ),
        }


    def _build_recent_lm_cognitive_assessment_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        current_judgement_counts: Dict[str, int] = {}
        dominant_constraint_counts: Dict[str, int] = {}
        why_not_improvement_counts: Dict[str, int] = {}
        self_iteration_target_counts: Dict[str, int] = {}
        self_iteration_hypothesis_counts: Dict[str, int] = {}
        stay_switch_counts: Dict[str, int] = {}
        switch_reason_counts: Dict[str, int] = {}
        entry_count = 0

        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict) or not assessment:
                continue

            current_judgement = str(assessment.get("current_judgement") or "").strip()
            dominant_constraint = str(assessment.get("dominant_constraint") or "").strip()
            why_not_improvement_now = [
                str(item).strip()
                for item in list(assessment.get("why_not_improvement_now") or [])[:3]
                if str(item).strip()
            ]
            stay_or_switch = str(assessment.get("stay_or_switch") or "").strip().lower()
            switch_reason = str(assessment.get("switch_reason") or "").strip()
            self_iteration_target = str(
                assessment.get("self_iteration_target") or ""
            ).strip()
            self_iteration_hypothesis = str(
                assessment.get("self_iteration_hypothesis") or ""
            ).strip()
            if current_judgement:
                current_judgement_counts[current_judgement] = (
                    current_judgement_counts.get(current_judgement, 0) + 1
                )
            if dominant_constraint:
                dominant_constraint_counts[dominant_constraint] = (
                    dominant_constraint_counts.get(dominant_constraint, 0) + 1
                )
            for item in why_not_improvement_now:
                why_not_improvement_counts[item] = (
                    why_not_improvement_counts.get(item, 0) + 1
                )
            if self_iteration_target:
                self_iteration_target_counts[self_iteration_target] = (
                    self_iteration_target_counts.get(self_iteration_target, 0) + 1
                )
            if self_iteration_hypothesis:
                self_iteration_hypothesis_counts[self_iteration_hypothesis] = (
                    self_iteration_hypothesis_counts.get(self_iteration_hypothesis, 0) + 1
                )
            if stay_or_switch in {"stay", "switch"}:
                stay_switch_counts[stay_or_switch] = (
                    stay_switch_counts.get(stay_or_switch, 0) + 1
                )
            if switch_reason:
                switch_reason_counts[switch_reason] = (
                    switch_reason_counts.get(switch_reason, 0) + 1
                )
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

        dominant_constraint = ""
        if dominant_constraint_counts:
            dominant_constraint = sorted(
                dominant_constraint_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[0][0]
        return {
            "available": True,
            "dominant_constraint": dominant_constraint or None,
            "current_judgement": _dominant(current_judgement_counts) or None,
            "current_judgement_count": len(current_judgement_counts),
            "why_not_improvement_now": _dominant(why_not_improvement_counts) or None,
            "why_not_improvement_now_count": len(why_not_improvement_counts),
            "self_iteration_target": _dominant(self_iteration_target_counts) or None,
            "self_iteration_target_count": len(self_iteration_target_counts),
            "self_iteration_hypothesis": _dominant(self_iteration_hypothesis_counts) or None,
            "self_iteration_hypothesis_count": len(self_iteration_hypothesis_counts),
            "stay_or_switch": _dominant(stay_switch_counts) or None,
            "stay_or_switch_count": len(stay_switch_counts),
            "switch_reason": _dominant(switch_reason_counts) or None,
            "switch_reason_count": len(switch_reason_counts),
            "entry_count": entry_count,
            "summary": (
                "Recent LM cognitive assessments remain available from history; "
                f"dominant constraint={dominant_constraint or 'unknown'}."
            ),
        }

    def _build_recent_self_iteration_trend_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        target_counts: Dict[str, int] = {}
        hypothesis_counts: Dict[str, int] = {}
        stay_switch_counts: Dict[str, int] = {}
        switch_reason_counts: Dict[str, int] = {}
        recent_targets: list[str] = []
        entry_count = 0

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict) or not assessment:
                continue
            target = str(assessment.get("self_iteration_target") or "").strip()
            hypothesis = str(assessment.get("self_iteration_hypothesis") or "").strip()
            stay_or_switch = str(assessment.get("stay_or_switch") or "").strip().lower()
            switch_reason = str(assessment.get("switch_reason") or "").strip()
            if not target and not hypothesis:
                continue
            if target:
                target_counts[target] = target_counts.get(target, 0) + 1
                recent_targets.append(target)
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
                "summary": "No recent self-iteration trend memory is available yet.",
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
        dominant_target = ranked_targets[0] if ranked_targets else ""
        unique_recent_targets = {
            item for item in recent_targets[:4] if str(item or "").strip()
        }
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
                sorted(stay_switch_counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
                if stay_switch_counts
                else None
            ),
            "stay_or_switch_count": len(stay_switch_counts),
            "dominant_switch_reason": (
                sorted(switch_reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
                if switch_reason_counts
                else None
            ),
            "switch_reason_count": len(switch_reason_counts),
            "entry_count": entry_count,
            "summary": (
                "Recent self-iteration trend favors "
                f"{dominant_target or 'unknown'}; trend_state={trend_state}; "
                f"target_stability={target_stability}."
            ),
        }

    def _build_recent_switch_self_regulation_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        switch_quality_scores: list[float] = []
        stay_quality_scores: list[float] = []

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict) or not assessment:
                continue
            decision = str(assessment.get("stay_or_switch") or "").strip().lower()
            if decision not in {"stay", "switch"}:
                continue
            quality_score = self._clamp_endogenous_ratio(outcome.get("quality_score") or 0.0)
            if decision == "switch":
                switch_quality_scores.append(quality_score)
            else:
                stay_quality_scores.append(quality_score)

        if not switch_quality_scores and not stay_quality_scores:
            return {
                "available": False,
                "summary": "No switch self-regulation memory is available yet.",
            }

        def _avg(values: list[float]) -> float:
            if not values:
                return 0.0
            return self._clamp_endogenous_ratio(sum(values) / len(values))

        average_switch_quality = _avg(switch_quality_scores)
        average_stay_quality = _avg(stay_quality_scores)
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
            "switch_effectiveness": (
                "strong" if average_switch_quality >= 0.65 else "weak"
            )
            if switch_quality_scores
            else "unknown",
            "stay_effectiveness": (
                "strong" if average_stay_quality >= 0.65 else "weak"
            )
            if stay_quality_scores
            else "unknown",
            "average_switch_quality": round(average_switch_quality, 4),
            "average_stay_quality": round(average_stay_quality, 4),
            "summary": (
                "Recent stay/switch outcomes suggest "
                f"preferred_bias={preferred_switch_bias}; "
                f"switch_quality={average_switch_quality:.2f}; "
                f"stay_quality={average_stay_quality:.2f}."
            ),
        }

    def _build_recent_post_task_effect_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        quality_scores: list[float] = []
        cognitive_scores: list[float] = []
        reference_scores: list[float] = []
        target_effect_counts: Dict[str, int] = {}

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
            target = str((assessment or {}).get("self_iteration_target") or "").strip()
            quality_score = self._clamp_endogenous_ratio(outcome.get("quality_score") or 0.0)
            cognitive_score = self._clamp_endogenous_ratio(
                (cognitive_alignment or {}).get("score") or 0.0
            )
            reference_score = self._clamp_endogenous_ratio(
                (reference_alignment or {}).get("alignment_score") or 0.0
            )
            if not quality_score and not cognitive_score and not reference_score:
                continue
            quality_scores.append(quality_score)
            cognitive_scores.append(cognitive_score)
            reference_scores.append(reference_score)
            if target:
                effect_label = "helped" if quality_score >= 0.65 and cognitive_score >= 0.55 else "unclear"
                if quality_score < 0.4 or reference_score < 0.4:
                    effect_label = "hurt"
                key = f"{target}:{effect_label}"
                target_effect_counts[key] = target_effect_counts.get(key, 0) + 1

        if not quality_scores and not cognitive_scores and not reference_scores:
            return {
                "available": False,
                "summary": "No recent post-task effect memory is available yet.",
            }

        def _avg(values: list[float]) -> float:
            if not values:
                return 0.0
            return self._clamp_endogenous_ratio(sum(values) / len(values))

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
            "summary": (
                "Recent post-task effects appear "
                f"{effect_direction}; avg_quality={average_quality_score:.2f}; "
                f"avg_cognitive_alignment={average_cognitive_alignment_score:.2f}; "
                f"avg_reference_alignment={average_reference_alignment_score:.2f}."
            ),
        }

    def _build_recent_meta_cognition_profile_summary(
        self,
        *,
        cognitive_assessment_memory: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        switch_self_regulation_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
        task_type_priors: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        current_judgement = str(
            cognitive_assessment_memory.get("current_judgement") or ""
        ).strip()
        dominant_constraint = str(
            cognitive_assessment_memory.get("dominant_constraint") or ""
        ).strip()
        top_self_iteration_domain = str(
            cognitive_assessment_memory.get("self_iteration_target")
            or self_iteration_trend_memory.get("dominant_target")
            or ""
        ).strip()
        top_self_iteration_hypothesis = str(
            cognitive_assessment_memory.get("self_iteration_hypothesis")
            or self_iteration_trend_memory.get("dominant_hypothesis")
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
        grounding_pressure = "low"
        alignment_available = bool(recent_reference_alignment.get("available")) or any(
            key in recent_reference_alignment
            for key in ("average_alignment_score", "weak_or_partial_count")
        )
        weak_or_partial_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        average_alignment_score = self._clamp_endogenous_ratio(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        if alignment_available:
            if weak_or_partial_count >= 2 or average_alignment_score < 0.4:
                grounding_pressure = "high"
            elif weak_or_partial_count >= 1 or average_alignment_score < 0.65:
                grounding_pressure = "medium"

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

        top_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
        why_not_improvement_now = str(
            cognitive_assessment_memory.get("why_not_improvement_now") or ""
        ).strip()
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
                "summary": "No recent meta-cognition profile is available yet.",
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
                "Recent meta-cognition indicates "
                f"judgement={current_judgement or 'unknown'}; "
                f"constraint={dominant_constraint or 'unknown'}; "
                f"grounding_pressure={grounding_pressure}; "
                f"self_iteration_domain={top_self_iteration_domain or 'unknown'}; "
                f"recent_effect_direction={recent_effect_direction or 'unknown'}."
            ),
        }


    def _build_current_candidate_cognition_summary(
        self,
        *,
        candidate_items: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_count = 0
        cognitive_scores: list[float] = []
        reference_scores: list[float] = []
        lm_generated_count = 0

        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            candidate_count += 1
            metadata = dict(item.get("metadata") or {})
            evidence = dict(item.get("evidence") or {})
            llm_generated = bool(metadata.get("llm_task_generated") or evidence.get("llm_generated"))
            if llm_generated:
                lm_generated_count += 1
            cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")

            score = None
            if isinstance(cognitive_alignment, dict) and cognitive_alignment:
                score = round(
                    self._clamp_endogenous_ratio(cognitive_alignment.get("score") or 0.0),
                    4,
                )
                cognitive_scores.append(score)
            reference_score = None
            if isinstance(reference_alignment, dict) and reference_alignment:
                reference_score = round(
                    self._clamp_endogenous_ratio(
                        reference_alignment.get("alignment_score") or 0.0
                    ),
                    4,
                )
                reference_scores.append(reference_score)

        average_cognitive_alignment_score = (
            sum(cognitive_scores) / len(cognitive_scores) if cognitive_scores else 0.0
        )
        average_reference_alignment_score = (
            sum(reference_scores) / len(reference_scores) if reference_scores else 0.0
        )
        return {
            "count": candidate_count,
            "lm_generated_count": lm_generated_count,
            "average_cognitive_alignment_score": round(
                self._clamp_endogenous_ratio(average_cognitive_alignment_score),
                4,
            ),
            "average_reference_alignment_score": round(
                self._clamp_endogenous_ratio(average_reference_alignment_score),
                4,
            ),
        }

__all__ = ["EndogenousCognitiveHistorySummaryService"]
