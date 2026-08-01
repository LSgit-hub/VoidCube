from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from systems.supervisor.endogenous_learning import (
    build_cognitive_assessment_review_candidate,
    build_exploratory_learning_candidate,
    build_shell_baseline_learning_candidate,
    extract_learning_topic,
    filter_learning_topics,
    idle_learning_urgency,
    stable_learning_topic_key,
)


@dataclass
class Policy:
    memory_continuity_bias: float = 0.6
    truthfulness_bias: float = 0.7
    learning_expansion_bias: float = 0.5
    governance_hygiene_bias: float = 0.55
    body_growth_bias: float = 0.65
    candidate_throttle: float = 0.1
    preferred_focus: str = "truthfulness"


def test_topic_extraction_and_stable_key_are_owner_local():
    activity = {
        "recent_metadata": {
            "user_request": {
                "text": "How can I optimize the agent tool-calling pipeline?"
            }
        }
    }

    topic = extract_learning_topic(activity)

    assert "optimize" in topic.lower()
    assert stable_learning_topic_key(topic).startswith("creativity:idle_learning:")


def test_topic_filter_respects_existing_and_recent_learning_cooldowns():
    now = datetime.now(timezone.utc)
    context = {
        "completed_learning_tasks": [
            {
                "title": "Improve websocket event delivery",
                "completed_at": (now - timedelta(hours=2)).isoformat(),
            }
        ],
        "autonomous_chain_live_tasks": [],
        "recent_learning_signatures": [],
    }
    topics = filter_learning_topics(
        [
            {"title": "Improve websocket event delivery"},
            {"title": "Research SQLite checkpoint compaction"},
        ],
        drive_context=context,
        existing_keys=set(),
        cooldown_hours=24,
        overlap_threshold=0.6,
        max_topics=3,
        now=now,
    )

    assert [item["title"] for item in topics] == [
        "Research SQLite checkpoint compaction"
    ]


def test_learning_factories_preserve_candidate_families_and_constraints():
    policy = Policy()
    shell = build_shell_baseline_learning_candidate(
        stable_key="creativity:self_learning:shell_baseline:shell",
        active_sessions=0,
        shell_slot_id="shell",
        shell_worktree="F:/shell",
        trigger="bootstrap_shell_baseline",
        bootstrap=True,
        urgency=0.55,
        backlog_pressure_penalty=0.0,
        drive_judgement={},
        adaptive_policy=policy,
    )
    exploratory = build_exploratory_learning_candidate(
        topic={
            "title": "SQLite checkpoint compaction",
            "summary": "Research checkpoint compaction.",
            "novelty_score": 0.9,
            "specificity_score": 0.8,
        },
        active_sessions=0,
        urgency=0.42,
        backlog_pressure_penalty=0.0,
        adaptive_policy=policy,
        drive_judgement={},
    )
    cognitive = build_cognitive_assessment_review_candidate(
        target="truthfulness",
        judgement="review uncertainty",
        cognitive_assessment_memory={
            "available": True,
            "current_judgement": "review uncertainty",
        },
        active_sessions=0,
        preferred_focus="truthfulness",
        backlog_pressure_penalty=0.0,
        adaptive_policy=policy,
        drive_judgement={},
    )

    assert shell.metadata["score_breakdown"]["candidate_kind"] == (
        "shell_baseline_learning"
    )
    assert shell.constraints["execution_policy"] == "learn_shell_baseline"
    assert exploratory.metadata["score_breakdown"]["candidate_kind"] == (
        "exploratory_learning"
    )
    assert exploratory.constraints["must_not_modify_active_body"] is True
    assert cognitive.stable_key.startswith(
        "creativity:self_learning:cognitive_review:"
    )
    assert cognitive.metadata["learning_branch"] == "cognitive_assessment_review"


def test_idle_learning_urgency_applies_session_penalty_and_gate_bonus():
    assert idle_learning_urgency(
        active_sessions=2,
        topic_source="activity_metadata",
        autonomous_chain_gate=True,
    ) == 0.37
