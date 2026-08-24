from voidcube.systems.supervisor.endogenous_history import (
    normalize_historical_outcomes,
    summarize_historical_pressure,
)


def test_historical_outcomes_sort_newest_timestamp_before_untimed_fallback():
    result = normalize_historical_outcomes(
        [
            {"id": "old", "updated_at": "2026-08-01T00:00:00+00:00"},
            {"id": "untimed"},
            {"id": "new", "updated_at": "2026-08-02T00:00:00+00:00"},
        ]
    )

    assert [item["id"] for item in result] == ["new", "old", "untimed"]


def test_historical_pressure_uses_self_learning_scope_when_enough_outcomes_exist():
    outcomes = [
        {"status": "completed", "task_family": "self_learning"},
        {"status": "failed", "task_family": "self_learning"},
        {"status": "awaiting_review", "task_family": "self_learning"},
        {"status": "completed", "task_family": "other"},
    ]

    result = summarize_historical_pressure(
        recent_historical_outcomes=outcomes,
        recent_self_learning_outcomes=outcomes[:3],
    )

    assert result["scope"] == "self_learning"
    assert result["total"] == 3
    assert result["success_ratio"] == 1 / 3
    assert result["drag_ratio"] == 2 / 3
    assert result["underdelivery_active"] is True


def test_historical_pressure_detects_relapse_after_a_recovery_window():
    result = summarize_historical_pressure(
        recent_historical_outcomes=[],
        recent_self_learning_outcomes=[
            {"status": "completed"},
            {"status": "completed"},
            {"status": "failed"},
            {"status": "completed"},
            {"status": "failed"},
            {"status": "awaiting_review"},
        ],
    )

    assert result["recent_relapse_drag_count"] == 2
    assert result["recent_relapse_drag_ratio"] == 2 / 3
    assert result["underdelivery_active"] is True
