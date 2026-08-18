"""Pure uncertainty-ledger projection for endogenous cognition."""

from __future__ import annotations

from typing import Any, Dict

from systems.supervisor.endogenous_state_projection import derive_corrective_mode


def build_uncertainty_ledger_projection(
    *,
    deliberation: Dict[str, Any],
    governance_channels: Dict[str, Any],
    self_regulation: Dict[str, Any],
) -> Dict[str, Any]:
    """Build explicit uncertainty entries from immutable cognition snapshots."""
    perception = dict(deliberation.get("perception") or {})
    world_model = dict(deliberation.get("world_model") or {})
    reflection = dict(deliberation.get("reflection") or {})
    adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
    corrective_mode = derive_corrective_mode(self_regulation)
    entries: list[Dict[str, Any]] = []
    autonomy_alignment_requests = len(
        list(governance_channels.get("autonomy_alignment_requests") or [])
    )

    correction_signals = int(perception.get("correction_signals") or 0)
    if correction_signals > 0:
        risk = _clamp_ratio(
            float(world_model.get("truthfulness_pressure") or 0.0) * 0.55
            + min(correction_signals, 6) / 6.0 * 0.45
        )
        entries.append(
            {
                "ledger_id": "uncertainty:truthfulness",
                "domain": "truthfulness",
                "risk": round(risk, 4),
                "confidence": round(
                    _clamp_ratio(
                        0.56 + float(adaptive_policy.get("truthfulness_bias") or 0.0) * 0.24
                    ),
                    4,
                ),
                "hypothesis": (
                    "Recent correction pressure may reflect unresolved truthfulness debt rather than isolated noise."
                ),
                "why_uncertain": (
                    "The drive sees rising errors or high-uncertainty answers, but it still needs targeted review to confirm whether a stable truthfulness issue exists."
                ),
                "observation_target": "truthfulness",
                "recommended_probe": "review recent uncertain answers and correction signals",
                "evidence": [
                    f"correction_signals={correction_signals}",
                    f"recent_errors={int(perception.get('recent_errors') or 0)}",
                    f"uncertainty_count={int(perception.get('uncertainty_count') or 0)}",
                ],
            }
        )

    api_b_judgement_pressure = float(
        reflection.get("api_b_judgement_blockage_pressure") or 0.0
    )
    if api_b_judgement_pressure >= 0.28 or str(
        world_model.get("governance_load_state") or ""
    ).strip() in {"busy", "strained"}:
        risk = _clamp_ratio(
            api_b_judgement_pressure * 0.7
            + (
                0.2
                if str(world_model.get("governance_load_state") or "").strip()
                == "strained"
                else 0.08
            )
        )
        entries.append(
            {
                "ledger_id": "uncertainty:api_b_judgement_blockage",
                "domain": "api_b_judgement",
                "risk": round(risk, 4),
                "confidence": round(
                    _clamp_ratio(
                        0.58
                        + float(adaptive_policy.get("governance_hygiene_bias") or 0.0)
                        * 0.18
                    ),
                    4,
                ),
                "hypothesis": (
                    "Additional autonomous output may worsen backlog drag before governance review debt is reduced."
                ),
                "why_uncertain": (
                    "Backlog pressure is visible, but the drive still needs to inspect whether the backlog is blocked by stale work, review debt, or repeated low-yield candidates."
                ),
                "observation_target": "api_b_judgement_blockage",
                "recommended_probe": "inspect stale, deferred, and pending-review endogenous tasks",
                "evidence": [
                    f"api_b_judgement_blockage_state={reflection.get('api_b_judgement_blockage_state')}",
                    f"api_b_judgement_count={int(perception.get('api_b_judgement_count') or 0)}",
                    f"pending_review_count={int(perception.get('pending_review_count') or 0)}",
                ],
            }
        )

    learning_yield_state = str(
        reflection.get("learning_yield_state") or ""
    ).strip().lower()
    if learning_yield_state in {"cold", "mixed"} or str(
        reflection.get("dominant_constraint") or ""
    ) == "weak_learning_yield":
        risk = _clamp_ratio(
            max(0.0, 0.65 - float(reflection.get("autonomy_readiness") or 0.0))
            * 0.6
            + (0.18 if learning_yield_state == "cold" else 0.08)
        )
        entries.append(
            {
                "ledger_id": "uncertainty:learning_yield",
                "domain": "learning_yield",
                "risk": round(risk, 4),
                "confidence": round(
                    _clamp_ratio(
                        0.48 + float(adaptive_policy.get("observation_bias") or 0.0) * 0.24
                    ),
                    4,
                ),
                "hypothesis": (
                    "Further learning expansion may create low-yield tasks before existing evidence is properly consolidated."
                ),
                "why_uncertain": (
                    "The drive can see mixed or weak learning signals, but it lacks enough evidence to know whether the problem is topic choice, backlog drag, or low follow-through."
                ),
                "observation_target": "learning_yield",
                "recommended_probe": "compare recent learning quality against downstream task completion and review outcomes",
                "evidence": [
                    f"learning_yield_state={learning_yield_state or 'unknown'}",
                    f"autonomy_readiness={round(float(reflection.get('autonomy_readiness') or 0.0), 4)}",
                    f"candidate_throttle={round(float(adaptive_policy.get('candidate_throttle') or 0.0), 4)}",
                ],
            }
        )

    autonomy_readiness = float(reflection.get("autonomy_readiness") or 0.0)
    dominant_constraint = str(
        reflection.get("dominant_constraint") or ""
    ).strip().lower()
    if (
        autonomy_alignment_requests > 0
        or autonomy_readiness <= 0.45
        or dominant_constraint
        in {"weak_learning_yield", "historical_underdelivery", "api_b_judgement_blockage"}
    ):
        risk = _clamp_ratio(
            max(0.0, 0.58 - autonomy_readiness) * 0.75
            + float(adaptive_policy.get("observation_bias") or 0.0) * 0.18
            + autonomy_alignment_requests * 0.08
        )
        entries.append(
            {
                "ledger_id": "uncertainty:autonomy_alignment",
                "domain": "autonomy_alignment",
                "risk": round(risk, 4),
                "confidence": round(
                    _clamp_ratio(
                        0.52 + float(adaptive_policy.get("observation_bias") or 0.0) * 0.2
                    ),
                    4,
                ),
                "hypothesis": (
                    "The current autonomous posture may be expanding or planning faster than the system can responsibly validate."
                ),
                "why_uncertain": (
                    "Readiness and observation pressure suggest the core should verify alignment before treating more output as safe."
                ),
                "observation_target": dominant_constraint or "autonomy_alignment",
                "recommended_probe": "inspect whether current posture should remain guarded or corrective on the next endogenous cycle",
                "evidence": [
                    f"autonomy_readiness={round(autonomy_readiness, 4)}",
                    f"observation_bias={round(float(adaptive_policy.get('observation_bias') or 0.0), 4)}",
                    f"autonomy_alignment_requests={autonomy_alignment_requests}",
                    f"dominant_constraint={dominant_constraint or 'none'}",
                ],
            }
        )

    if corrective_mode.get("active"):
        entries.append(
            {
                "ledger_id": "uncertainty:self_regulation_decay",
                "domain": "self_regulation",
                "risk": round(
                    _clamp_ratio(
                        float(self_regulation.get("dynamic_candidate_throttle_boost") or 0.0)
                        * 0.4
                        + float(self_regulation.get("dynamic_truthfulness_bias_boost") or 0.0)
                        * 0.6
                    ),
                    4,
                ),
                "confidence": round(0.62, 4),
                "hypothesis": (
                    "Temporary corrective mode may still be shaping governance posture even if the original trigger is fading."
                ),
                "why_uncertain": (
                    "Short-term boosts decay automatically, so the drive should verify whether the pressure is still real before treating guarded posture as the new normal."
                ),
                "observation_target": "self_regulation",
                "recommended_probe": "re-evaluate whether corrective boosts are still justified after the next endogenous cycle",
                "evidence": [
                    f"corrective_mode={corrective_mode.get('mode')}",
                    f"last_reason={corrective_mode.get('last_reason')}",
                ],
            }
        )

    truthfulness_alerts = len(
        list(governance_channels.get("truthfulness_alerts") or [])
    )
    if truthfulness_alerts > 0 and not any(
        item.get("domain") == "truthfulness" for item in entries
    ):
        entries.append(
            {
                "ledger_id": "uncertainty:latent_truthfulness",
                "domain": "truthfulness",
                "risk": round(_clamp_ratio(0.4 + truthfulness_alerts * 0.12), 4),
                "confidence": round(0.54, 4),
                "hypothesis": "Truthfulness alerts may indicate a latent evidence-quality problem.",
                "why_uncertain": "The alerts are present, but the correction pattern has not yet been fully explained by the current snapshot.",
                "observation_target": "truthfulness",
                "recommended_probe": "inspect which observation requests escalated into truthfulness alerts",
                "evidence": [f"truthfulness_alerts={truthfulness_alerts}"],
            }
        )

    entries.sort(key=lambda item: item.get("risk") or 0.0, reverse=True)
    highest_risk_domain = entries[0]["domain"] if entries else None
    summary = (
        f"The endogenous core is tracking {len(entries)} active uncertainty item(s); "
        f"highest current risk is {highest_risk_domain}."
        if entries
        else "The endogenous core sees no active uncertainty requiring explicit tracking right now."
    )
    return {
        "summary": summary,
        "active_count": len(entries),
        "highest_risk_domain": highest_risk_domain,
        "entries": entries[:6],
    }


def _clamp_ratio(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = ["build_uncertainty_ledger_projection"]
