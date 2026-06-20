from __future__ import annotations

from datetime import datetime, timezone

from memai import ChroniclePipeline, QueryPlanner, TranscriptTurn


def _build_result():
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="2026-03-01 we decided to build the memory system project.",
            timestamp=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="2026-03-10 we implemented the retrieval pipeline.",
            timestamp=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc),
        ),
    ]
    return ChroniclePipeline().ingest(turns)


def test_query_planner_builds_recent_changes_plan() -> None:
    planner = _build_result().create_query_planner()

    plan = planner.plan(
        "What changed in the project this month?",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    assert plan.intent == "summarize_recent_changes"
    assert plan.steps[0].step_type == "range_query"
    assert plan.answer_strategy == "timeline_first"


def test_query_planner_executes_theme_request() -> None:
    planner = _build_result().create_query_planner()

    execution = planner.plan_and_execute(
        "How has retrieval evolved so far?",
        reference_time=datetime(2026, 3, 31, 0, 0, tzinfo=timezone.utc),
    )

    assert execution.plan.intent == "trace_theme"
    assert "theme_evolution" in execution.artifacts
    assert execution.artifacts["theme_evolution"]["result_type"] == "theme_evolution"


def test_query_planner_detects_stable_context_requests() -> None:
    planner = _build_result().create_query_planner()

    plan = planner.plan("What stable constraints should I remember before answering?")

    assert plan.intent == "retrieve_stable_context"
    assert plan.steps[0].step_type == "profile_lookup"
    assert plan.answer_strategy == "stable_context_first"
