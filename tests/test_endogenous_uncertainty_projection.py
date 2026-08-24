from __future__ import annotations

from voidcube.systems.supervisor.endogenous_uncertainty_projection import (
    build_uncertainty_ledger_projection,
)


def test_uncertainty_ledger_projection_builds_explicit_truthfulness_and_alignment_entries() -> None:
    result = build_uncertainty_ledger_projection(
        deliberation={
            "perception": {
                "correction_signals": 3,
                "recent_errors": 2,
                "uncertainty_count": 1,
            },
            "world_model": {
                "truthfulness_pressure": 0.8,
                "governance_load_state": "strained",
            },
            "reflection": {
                "api_b_judgement_blockage_pressure": 0.5,
                "autonomy_readiness": 0.35,
                "dominant_constraint": "api_b_judgement_blockage",
            },
            "adaptive_policy": {"observation_bias": 0.2},
        },
        governance_channels={"autonomy_alignment_requests": [{"id": "one"}]},
        self_regulation={},
    )

    domains = {entry["domain"] for entry in result["entries"]}
    assert "truthfulness" in domains
    assert "api_b_judgement" in domains
    assert "autonomy_alignment" in domains
    assert result["highest_risk_domain"] in domains


def test_uncertainty_ledger_projection_keeps_corrective_mode_explicit() -> None:
    result = build_uncertainty_ledger_projection(
        deliberation={
            "perception": {},
            "world_model": {},
            "reflection": {"autonomy_readiness": 0.9},
            "adaptive_policy": {},
        },
        governance_channels={},
        self_regulation={
            "dynamic_truthfulness_bias_boost": 0.2,
            "last_reason": "recent correction pressure",
        },
    )

    entry = next(item for item in result["entries"] if item["domain"] == "self_regulation")
    assert entry["risk"] == 0.12
    assert "corrective_mode=corrective" in entry["evidence"]
