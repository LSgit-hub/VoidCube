from voidcube.systems.supervisor.endogenous_api_b_snapshot import build_api_b_judgement_snapshot


def test_api_b_snapshot_is_empty_without_backlog_inputs():
    assert build_api_b_judgement_snapshot({}) == {}


def test_api_b_snapshot_projects_bounded_backlog_and_recent_statuses():
    result = build_api_b_judgement_snapshot(
        {
            "api_b_judgement_tasks": [
                {"title": "Review grounding", "status": "awaiting_review"},
                {"title": "Repair evidence", "status": "running"},
            ],
            "learning_backlog_titles": ["Learn topic"],
            "body_improvement_backlog_titles": ["Improve shell"],
        }
    )

    assert result["api_b_judgement_task_count"] == 2
    assert result["learning_backlog_count"] == 1
    assert result["body_improvement_backlog_count"] == 1
    assert result["recent_titles"] == ["Review grounding", "Repair evidence"]
    assert result["recent_statuses"] == ["awaiting_review", "running"]
    assert "API-B 判断在途 2 项" in result["summary"]
