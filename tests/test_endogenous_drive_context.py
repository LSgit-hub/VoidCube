from datetime import datetime, timedelta, timezone

from voidcube.systems.supervisor.endogenous_drive_context import (
    build_drive_context,
    normalize_drive_input,
    normalize_strategy_memory,
    parse_timestamp,
)


def test_strategy_memory_normalization_keeps_supported_buckets_and_clamps_values():
    result = normalize_strategy_memory(
        {
            "focus_stats": {
                " Learning_Expansion ": {
                    "judged": 2,
                    "completed": -1,
                    "failed": 1,
                    "dragging": 3,
                },
                "": {"judged": 99},
            },
            "agenda_topic_stats": {
                " Topic A ": {
                    "seen": 2,
                    "last_priority": 1.7,
                    "last_confidence": -0.2,
                    "last_status": " Completed ",
                }
            },
        }
    )

    assert result["focus_stats"] == {
        "learning_expansion": {
            "judged": 2,
            "completed": 0,
            "failed": 1,
            "dragging": 3,
        }
    }
    assert result["agenda_topic_stats"]["topic a"]["last_priority"] == 1.0
    assert result["agenda_topic_stats"]["topic a"]["last_confidence"] == 0.0
    assert result["agenda_topic_stats"]["topic a"]["last_status"] == "completed"


def test_drive_context_projects_backlog_and_execution_lane_counts():
    now = datetime(2026, 8, 2, tzinfo=timezone.utc)
    drive_input = {
        "endogenous_drive_policy": {"candidate_budget": 2},
        "drive_history": {"judgements": [{"id": "j1"}], "outcomes": []},
        "completed_learning_tasks": [{"title": "Recent learning"}],
        "api_b_judgement_tasks": [
            {
                "title": "stale review",
                "status": "awaiting_review",
                "task_family": "self_learning",
                "governance_task_type": "self_learning",
                "execution_kind": "research",
                "updated_at": (now - timedelta(hours=25)).isoformat(),
            },
            {
                "title": "active review",
                "status": "running",
                "task_family": "general_self_evolution",
                "governance_task_type": "self_evolution",
                "execution_kind": "body_improvement",
                "updated_at": now.isoformat(),
            },
        ],
        "employee_execution_lane_tasks": [
            {"status": "approved"},
            {"status": "running"},
        ],
    }

    result = build_drive_context(drive_input, now=now)

    assert result["api_b_judgement_count"] == 2
    assert result["pending_review_count"] == 1
    assert result["stale_backlog_count"] == 1
    assert result["employee_dispatch_count"] == 1
    assert result["employee_running_count"] == 1
    assert result["learning_backlog_titles"] == ["stale review"]
    assert result["body_improvement_backlog_titles"] == ["active review"]
    assert result["active_backlog_by_governance"] == {
        "self_learning": 1,
        "self_evolution": 1,
    }
    assert result["drive_history"]["judgements"] == [{"id": "j1"}]


def test_parse_timestamp_normalizes_naive_values_and_rejects_invalid_values():
    parsed = parse_timestamp("2026-08-02T12:00:00")

    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parse_timestamp("not-a-timestamp") is None
    assert parse_timestamp(None) is None


def test_normalize_drive_input_returns_a_copy_for_mapping_values():
    source = {"checks": {"ready": True}}

    result = normalize_drive_input(source)

    assert result == source
    assert result is not source
    assert normalize_drive_input(None) == {}
