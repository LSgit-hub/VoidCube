from systems.supervisor.endogenous_pressure import (
    backlog_pressure_penalty,
    build_backlog_pressure_penalties,
    governance_hygiene_urgency,
    memory_maintenance_urgency,
)


def test_backlog_pressure_penalty_is_bounded_and_tracks_related_work():
    drive_context = {
        "api_b_judgement_count": 20,
        "active_backlog_by_governance": {"self_learning": 2},
        "active_backlog_by_family": {"self_learning": 1},
        "active_backlog_by_execution_kind": {},
    }

    assert backlog_pressure_penalty(drive_context) == 0.28
    assert backlog_pressure_penalty(
        {**drive_context, "api_b_judgement_count": 1},
        governance_task_type="self_learning",
        task_family="self_learning",
    ) == 0.09


def test_backlog_pressure_penalties_preserve_candidate_lanes():
    result = build_backlog_pressure_penalties(
        {
            "api_b_judgement_count": 2,
            "active_backlog_by_governance": {"self_evolution": 1},
            "active_backlog_by_family": {"body_upgrade": 2},
            "active_backlog_by_execution_kind": {"body_improvement": 1},
        }
    )

    assert list(result) == ["memory_maintenance", "self_learning", "body_improvement"]
    assert result["memory_maintenance"] == 0.02
    assert result["body_improvement"] == 0.14


def test_urgency_projections_keep_existing_idle_and_governance_semantics():
    formal = {
        "idle_seconds": {"user": 900, "api_a_execution": 0, "agent": 900, "memory": 900}
    }
    missing_api_a_execution = {"idle_seconds": {"user": 900, "agent": 900, "memory": 900}}
    formal_idle = {
        "idle_seconds": {"user": 900, "api_a_execution": 900, "agent": 0, "memory": 900}
    }

    assert memory_maintenance_urgency(formal) == memory_maintenance_urgency(
        missing_api_a_execution
    )
    assert memory_maintenance_urgency(formal_idle) > memory_maintenance_urgency(
        missing_api_a_execution
    )
    assert governance_hygiene_urgency(
        {"api_b_judgement_count": 2, "stale_backlog_count": 1, "pending_review_count": 1}
    ) == 0.56
