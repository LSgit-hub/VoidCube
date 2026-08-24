from datetime import datetime, timezone

from voidcube.systems.supervisor.endogenous_evidence import (
    build_evidence_channels,
    build_evidence_graph,
    channel_confidence_from_learning,
    evidence_conflict_flags,
    item_evidence_quality,
    normalize_external_research_entries,
    normalize_external_research_file_payload,
    normalize_recent_learning_evidence,
    research_freshness_hint,
)


def test_external_research_normalization_keeps_configured_shape_and_quality():
    rows = normalize_external_research_entries(
        ["Topic A::A useful detail", "Topic B", "  "]
    )

    assert [row["title"] for row in rows] == ["Topic A", "Topic B"]
    assert rows[0]["summary"] == "A useful detail"
    assert rows[0]["supports"] == ["external_research", "forward_direction"]
    assert 0 < rows[0]["confidence_score"] <= 1


def test_research_file_payload_normalizes_dict_and_scalar_items():
    rows = normalize_external_research_file_payload(
        {
            "entries": [
                {"topic": "Structured topic", "content": "Details", "url": "https://example.test"},
                "Plain note",
            ]
        },
        source_path="research.json",
    )

    assert rows[0]["title"] == "Structured topic"
    assert rows[0]["source_path"] == "research.json"
    assert rows[0]["url"] == "https://example.test"
    assert rows[1]["source"] == "external_research_file"


def test_recent_learning_normalization_bounds_rows_and_uses_evidence_summary():
    rows = normalize_recent_learning_evidence(
        [
            {
                "title": "Learning trace",
                "summary": "A" * 400,
                "quality_score": 0.8,
                "evidence": {"evidence_summary": ["one", "two", "", "three", "four"]},
            },
            {"topic": ""},
            "ignored",
        ]
    )

    assert len(rows) == 1
    assert len(rows[0]["summary"]) == 280
    assert rows[0]["evidence_summary"] == ["one", "two", "three", "four"]
    assert rows[0]["supports"] == ["self_understanding", "learning_trace"]


def test_evidence_quality_is_clamped_for_untrusted_inputs():
    quality = item_evidence_quality(
        item={"title": "x", "summary": "y", "quality_score": "bad"},
        source_reliability=4,
        supports=[],
        contradicts=[],
    )

    assert quality["source_reliability"] == 1.0
    assert quality["confidence_score"] <= 1.0


def test_evidence_graph_preserves_support_and_contradiction_edges():
    graph = build_evidence_graph(
        recent_learning_evidence=[
            {
                "title": "Learning trace",
                "confidence_score": 0.8,
                "supports": ["self_understanding"],
                "contradicts": ["forward_direction"],
            }
        ],
        external_research_evidence=[
            {
                "title": "Research note",
                "confidence_score": 0.6,
                "supports": ["forward_direction"],
            }
        ],
        shell_body_profile={},
    )

    assert graph["node_count"] == 2
    assert graph["edge_count"] == 3
    forward = next(
        node for node in graph["nodes"] if node["topic"] == "forward_direction"
    )
    assert forward["support_count"] == 1
    assert forward["contradict_count"] == 1
    assert forward["net_signal"] == 0


def test_evidence_channels_assemble_stable_shape_and_conflict_flags():
    learning = [{"title": "Weak trace", "quality_score": 0.1}]
    channels = build_evidence_channels(
        recent_learning_evidence=learning,
        external_research_evidence=[],
        shell_body_profile={"profile_status": "missing_worktree"},
        deliberation_dict={"world_model": {"self_confidence": 0.5}},
    )

    assert [row["channel"] for row in channels["channels"]] == [
        "recent_learning",
        "shell_body_profile",
        "external_research",
        "deliberation_state",
    ]
    assert channels["channels"][0]["evidence_strength"] == "weak"
    assert channels["research_digest"]["conflict_flags"] == [
        "research_missing_external_support"
    ]
    assert evidence_conflict_flags(
        recent_learning_evidence=learning,
        external_research_evidence=[],
        shell_body_profile={"profile_status": "missing_worktree"},
    ) == [
        "learning_weak_quality_signal",
        "body_profile_incomplete",
        "research_missing_external_support",
    ]
    assert channel_confidence_from_learning(learning) == 0.36


def test_research_freshness_uses_explicit_clock():
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)

    assert research_freshness_hint([], now=now) == "unknown"
    assert research_freshness_hint(
        [{"published_at": "2026-07-25T00:00:00+00:00"}],
        now=now,
    ) == "fresh"
    assert research_freshness_hint(
        [{"published_at": "2026-05-01T00:00:00+00:00"}],
        now=now,
    ) == "stale"
