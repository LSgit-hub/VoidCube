from __future__ import annotations

from voidcube.systems.supervisor.endogenous_meta_governance import derive_meta_governance_mode


def test_meta_governance_mode_is_derived_from_explicit_cognition_projections() -> None:
    result = derive_meta_governance_mode(
        attention_agenda={"entries": [{"topic": "observe_before_acting", "priority": 0.3}]},
        uncertainty_ledger={"entries": [{"domain": "truthfulness", "risk": 0.8}]},
        observation_program={"entries": [{"target": "truthfulness", "priority": 0.75}]},
        self_regulation={},
        reflection={"dominant_constraint": "api_b_judgement_blockage", "autonomy_readiness": 0.4},
        adaptive_policy={"preferred_focus": "observation", "observation_bias": 0.3},
        strategy_memory={},
    )

    assert result["mode"] == "observe"
    assert result["stability"] in {"stable", "moderate", "strong", "fragile"}
    assert any(item.startswith("uncertainty=truthfulness") for item in result["drivers"])
    assert "prioritize evidence collection before expansion" in result["guardrails"]
