from datetime import datetime, timezone

from voidcube.systems.supervisor.activity_projection import (
    enforce_auto_drive_input_boundary,
    idle_seconds_since,
    parse_activity_timestamp,
    project_auto_activity_snapshot,
    project_runtime_observation_input,
)


def test_activity_timestamp_normalizes_aware_values_to_naive_utc() -> None:
    parsed = parse_activity_timestamp("2026-07-30T08:00:00+08:00")

    assert parsed == datetime(2026, 7, 30, 0, 0, 0)
    assert parse_activity_timestamp("not-a-date") is None
    assert idle_seconds_since(parsed, now=datetime(2026, 7, 30, 0, 0, 5)) == 5


def test_runtime_observation_projection_normalizes_activity_and_user_signal() -> None:
    projected = project_runtime_observation_input(
        {
            "activity": {"active_sessions": "2", "counts": None},
            "user_chain_signal": {"quiet_after_seconds": "invalid"},
        },
        snapshot_source=" cached ",
    )

    assert projected["activity"]["active_sessions"] == 2
    assert projected["activity"]["counts"] == {}
    assert projected["user_chain_signal"] == {
        "quiet_after_seconds": 600,
        "scope": "soft_signal_only",
        "active_sessions": 2,
        "is_quiet": False,
    }
    assert projected["snapshot_source"] == "cached"


def test_auto_boundary_excludes_user_signals_and_preserves_only_supervisor_activity() -> None:
    source = {
        "activity": {
            "last_memory_task_at": "2026-07-30T00:00:00",
            "last_user_request_at": "2026-07-30T00:00:01",
            "active_cli_executor": {"agent_lane": "supervisor_task", "idle_seconds": 3},
        },
        "thresholds": {"user_idle_seconds": 42},
        "active_sessions": 5,
        "correction_signals": 4,
        "governance_task_type_decisions": {"user": {"eligible_for_planning": True}},
    }

    projected_activity = project_auto_activity_snapshot(source["activity"])
    bounded = enforce_auto_drive_input_boundary(source, evidence_packet={"id": "evidence"})

    assert projected_activity["last_memory_task_at"] == "2026-07-30T00:00:00"
    assert "last_user_request_at" not in projected_activity
    assert bounded["activity"] == projected_activity
    assert bounded["active_sessions"] == 0
    assert bounded["user_chain_signal"]["scope"] == "excluded_in_auto"
    assert bounded["user_chain_signal"]["quiet_after_seconds"] == 42
    assert bounded["governance_task_type_decisions"]["user"] == {
        "eligible_for_planning": False,
        "eligible_for_execution": False,
    }
