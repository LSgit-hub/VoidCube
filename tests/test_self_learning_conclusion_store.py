import pytest

from systems.self_learning import (
    LearningRecommendation,
    SupervisorTaskProposal,
)
from systems.self_learning.conclusion_store import SelfLearningConclusionStore
from memai.governance_repository import GovernanceEventRepository


class _FailingGovernanceRepository:
    def append(self, event):
        raise RuntimeError(f"cannot persist {event.id}")


def test_self_learning_conclusion_store_persists_conclusion_and_only_builds_payload(tmp_path):
    governance = GovernanceEventRepository(
        tmp_path / "supervisor" / "mem_governance.jsonl"
    )
    store = SelfLearningConclusionStore(
        tmp_path / "self-learning",
        governance_repository=governance,
    )

    topic = store.create_topic(
        title="Gateway idle-window policy",
        reason="Need evidence before changing self-evolution timing.",
        tags=["gateway", "planning"],
    )
    session = store.plan_session(topic=topic, planned_minutes=20, trigger="idle")
    experiment = store.record_experiment(
        topic=topic,
        session=session,
        hypothesis="Using activity facts is safer than trusting clock time alone.",
        method="Compare gateway activity markers with static time ranges.",
        observations=["Gateway activity markers reflect real traffic spikes."],
        outcome="passed",
        compared_against=["static-clock-window"],
    )
    conclusion = store.submit_conclusion(
        topic=topic,
        session=session,
        experiments=[experiment],
        comparisons=["activity-fact > clock-only"],
        summary="Use gateway facts for idle judgement and keep clock time as a hint.",
        verified=True,
        recommendations=[
            LearningRecommendation(
                recommendation_type="propose_evolution_task",
                title="Add gateway-backed idle judgement",
                summary="Create a supervisor-visible task instead of changing runtime directly.",
            )
        ],
    )

    payload = store.build_supervisor_payload(conclusion)

    assert payload["source"] == "self_learning"
    assert payload["verified"] is True
    assert payload["recommendations"][0]["recommendation_type"] == "propose_evolution_task"
    assert "task_type" not in payload["proposals"][0]
    assert payload["proposals"][0]["governance_task_type"] == "self_evolution"
    assert payload["proposals"][0]["task_family"] == "general_self_evolution"
    assert payload["proposals"][0]["execution_kind"] == "general_self_evolution"
    assert (tmp_path / "self-learning" / "conclusions" / f"{conclusion.conclusion_id}.json").exists()

    events = governance.list_events()
    assert len(events) == 1
    execution_result = events[0].execution_result or {}
    assert execution_result["title"] == "Gateway idle-window policy"
    assert execution_result["summary"] == (
        "Use gateway facts for idle judgement and keep clock time as a hint."
    )
    assert execution_result["runtime_task_profile"] == {
        "governance_task_type": "self_learning",
        "task_family": "self_learning",
        "execution_kind": None,
    }
    assert execution_result["constraints"] == {}
    assert execution_result["recommendations_count"] == 1


def test_self_learning_conclusion_does_not_report_success_without_governance_event(tmp_path):
    store = SelfLearningConclusionStore(
        tmp_path / "self-learning",
        governance_repository=_FailingGovernanceRepository(),
    )
    topic = store.create_topic(title="Durable evidence", reason="governance first")
    session = store.plan_session(topic=topic)

    with pytest.raises(RuntimeError, match="cannot persist"):
        store.submit_conclusion(
            topic=topic,
            session=session,
            experiments=[],
            comparisons=[],
            summary="This conclusion requires canonical governance history.",
            verified=True,
        )
    assert list((tmp_path / "self-learning" / "conclusions").glob("*.json")) == []


def test_self_learning_conclusion_store_marks_experiment_followups_as_self_learning(tmp_path):
    store = SelfLearningConclusionStore(tmp_path / "self-learning")

    topic = store.create_topic(
        title="Executor smoke coverage",
        reason="Need a learn-only follow-up instead of an execution handoff.",
        tags=["executor", "learning"],
    )
    session = store.plan_session(topic=topic, planned_minutes=10, trigger="scheduled")
    conclusion = store.submit_conclusion(
        topic=topic,
        session=session,
        experiments=[],
        comparisons=[],
        summary="Collect more learn-only evidence before any governed change.",
        verified=False,
        recommendations=[
            LearningRecommendation(
                recommendation_type="propose_experiment",
                title="Run an extra executor smoke experiment",
                summary="Keep this as self-learning follow-up work.",
            )
        ],
    )

    payload = store.build_supervisor_payload(conclusion)

    assert "task_type" not in payload["proposals"][0]
    assert payload["proposals"][0]["governance_task_type"] == "self_learning"
    assert payload["proposals"][0]["task_family"] == "self_learning"
    assert payload["proposals"][0]["execution_kind"] is None



