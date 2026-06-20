from __future__ import annotations

from datetime import datetime, timezone

from memai import (
    CertaintyState,
    ChroniclePipeline,
    MemoryQueryEngine,
    Status,
    TranscriptTurn,
)


def _build_turns() -> list[TranscriptTurn]:
    return [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="2026-03-22 we decided to build the memory system project.",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="2026-03-23 we implemented the schema and retrieval pipeline.",
            timestamp=datetime(2026, 3, 23, 10, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="user",
            text="2026-03-24 we refined the revision rules for the project.",
            timestamp=datetime(2026, 3, 24, 11, 0, tzinfo=timezone.utc),
        ),
    ]


def test_range_query_respects_detail_level_and_evidence_flags() -> None:
    result = ChroniclePipeline().ingest(_build_turns())

    payload = result.create_query_engine().range_query(
        datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 31, 23, 59, tzinfo=timezone.utc),
        topic="memory system",
        detail_level="brief",
        max_results=1,
        include_evidence=False,
    )

    assert payload["result_type"] == "range_summary"
    assert payload["detail_level"] == "brief"
    assert len(payload["observed"]) <= 1
    assert len(payload["main_arcs"]) <= 1
    assert payload["evidence_refs"] == []


def test_point_query_can_include_superseded_history() -> None:
    pipeline = ChroniclePipeline()
    result = pipeline.ingest(_build_turns())
    execution = result.revise_memory(
        target_id=result.events[0].id,
        revision_type="factual_revision",
        reason="Clarify the event wording",
        changes={"summary": "Revised summary for the initial memory-system decision."},
    )
    engine = MemoryQueryEngine(
        events=execution.events,
        scenes=execution.scenes,
        arcs=execution.arcs,
        epochs=execution.epochs,
    )

    current_view = engine.point_query(
        datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc),
        include_superseded=False,
    )
    historical_view = engine.point_query(
        datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc),
        include_superseded=True,
    )

    assert len(current_view["events"]) == 1
    assert len(historical_view["events"]) == 2


def test_active_arcs_respects_status_filter_and_max_results() -> None:
    result = ChroniclePipeline().ingest(_build_turns())
    result.arcs[0].status = Status.DORMANT

    payload = result.create_query_engine().active_arcs(
        statuses=[Status.DORMANT],
        max_results=1,
    )

    assert payload["result_type"] == "active_arcs"
    assert len(payload["arcs"]) == 1
    assert payload["arcs"][0]["status"] == "dormant"


def test_profile_lookup_filters_by_subject_and_kind() -> None:
    turns = _build_turns() + [
        TranscriptTurn(
            turn_id="turn_004",
            speaker="user",
            text="Please use Chinese responses.",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_005",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 24, 13, 0, tzinfo=timezone.utc),
        ),
    ]
    result = ChroniclePipeline().ingest(turns)

    payload = result.create_query_engine().profile_lookup(
        subject="user",
        memory_kind=None,
        max_results=5,
    )

    assert payload["result_type"] == "profile_lookup"
    assert payload["items"]
    assert all(item["subject"] == "user" for item in payload["items"])


def test_profile_conflicts_are_marked_disputed_and_ranked_last() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="Please use Chinese responses.",
            timestamp=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="user",
            text="Please use English responses.",
            timestamp=datetime(2026, 3, 24, 13, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_003",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 24, 14, 0, tzinfo=timezone.utc),
        ),
    ]
    result = ChroniclePipeline().ingest(turns)

    user_items = result.create_query_engine().profile_lookup(
        subject="user", max_results=5
    )["items"]
    disputed_items = [
        item for item in user_items if item["certainty_state"] == "disputed"
    ]

    assert len(disputed_items) == 2
    assert all(item["conflict_refs"] for item in disputed_items)

    all_items = result.create_query_engine().profile_lookup(max_results=5)["items"]
    assert all_items[0]["certainty_state"] != CertaintyState.DISPUTED.value


def test_range_query_surfaces_related_stable_context() -> None:
    turns = [
        TranscriptTurn(
            turn_id="turn_001",
            speaker="user",
            text="2026-03-22 we decided to build the memory system project.",
            timestamp=datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc),
        ),
        TranscriptTurn(
            turn_id="turn_002",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 22, 10, 0, tzinfo=timezone.utc),
        ),
    ]
    result = ChroniclePipeline().ingest(turns)

    payload = result.create_query_engine().range_query(
        datetime(2026, 3, 22, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 22, 23, 59, tzinfo=timezone.utc),
        entity="project",
    )

    assert payload["result_type"] == "range_summary"
    assert payload["stable_context"]
    assert any(
        item["memory_kind"] == "constraint" for item in payload["stable_context"]
    )


def test_theme_query_includes_stable_context_field() -> None:
    turns = _build_turns() + [
        TranscriptTurn(
            turn_id="turn_004",
            speaker="assistant",
            text="The project must preserve evidence traces.",
            timestamp=datetime(2026, 3, 24, 13, 0, tzinfo=timezone.utc),
        ),
    ]
    result = ChroniclePipeline().ingest(turns)

    payload = result.create_query_engine().theme_evolution("memory-system")

    assert payload["result_type"] == "theme_evolution"
    assert "stable_context" in payload
