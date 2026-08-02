from types import SimpleNamespace

from systems.supervisor.endogenous_reflection import build_reflection_projection


def _perception(**overrides):
    values = {
        "learning_quality": 50.0,
        "stale_backlog_count": 0,
        "api_b_judgement_count": 0,
        "active_sessions": 0,
        "user_mode": "idle",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _world(**overrides):
    values = {
        "governance_load_state": "clear",
        "self_confidence": 0.6,
        "learning_momentum": 0.6,
        "body_upgrade_readiness": 0.5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reflection_projection_uses_perception_baseline_without_history():
    result = build_reflection_projection(
        perception=_perception(),
        world_model=_world(),
        drive_context={},
    )

    assert result["recent_learning_count"] == 0
    assert result["recent_learning_quality"] == 0.5
    assert result["learning_yield_state"] == "mixed"
    assert result["api_b_judgement_blockage_state"] == "clear"
    assert result["dominant_constraint"] == "none"


def test_reflection_projection_detects_backlog_blockage_and_repeated_drive_pressure():
    result = build_reflection_projection(
        perception=_perception(stale_backlog_count=2, api_b_judgement_count=4),
        world_model=_world(governance_load_state="strained"),
        drive_context={
            "autonomous_chain_live_tasks": [
                {"status": "deferred", "metadata": {"endogenous_drive_key": "a"}},
                {"status": "retry", "evidence": {"endogenous_drive_key": "a"}},
            ]
        },
    )

    assert result["api_b_judgement_blockage_state"] == "blocked"
    assert result["dominant_constraint"] == "api_b_judgement_blockage"
    assert result["repeated_drive_pressure"] > 0.0
    assert "blocked_status_count=2" in result["source_evidence"]
