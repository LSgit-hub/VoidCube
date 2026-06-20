from systems.self_learning import (
    LearningRecommendation,
    SelfLearningService,
    SupervisorTaskProposal,
)
from systems.supervisor.task_queue import SelfEvolutionTaskQueue


def test_self_learning_service_persists_conclusion_and_only_builds_payload(tmp_path):
    service = SelfLearningService(tmp_path / "self-learning")

    topic = service.create_topic(
        title="Gateway idle-window policy",
        reason="Need evidence before changing self-evolution timing.",
        tags=["gateway", "planning"],
    )
    session = service.plan_session(topic=topic, planned_minutes=20, trigger="idle")
    experiment = service.record_experiment(
        topic=topic,
        session=session,
        hypothesis="Using activity facts is safer than trusting clock time alone.",
        method="Compare gateway activity markers with static time ranges.",
        observations=["Gateway activity markers reflect real traffic spikes."],
        outcome="passed",
        compared_against=["static-clock-window"],
    )
    conclusion = service.submit_conclusion(
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

    payload = service.build_supervisor_payload(conclusion)

    assert payload["source"] == "self_learning"
    assert payload["verified"] is True
    assert payload["recommendations"][0]["recommendation_type"] == "propose_evolution_task"
    assert "task_type" not in payload["proposals"][0]
    assert payload["proposals"][0]["governance_task_type"] == "self_evolution"
    assert payload["proposals"][0]["task_family"] == "general_self_evolution"
    assert payload["proposals"][0]["execution_kind"] == "general_self_evolution"
    assert (tmp_path / "self-learning" / "conclusions" / f"{conclusion.conclusion_id}.json").exists()


def test_self_learning_service_can_submit_recommendations_into_supervisor_queue(tmp_path):
    service = SelfLearningService(tmp_path / "self-learning")
    queue = SelfEvolutionTaskQueue(tmp_path / "queue" / "self_evolution_queue.json")

    topic = service.create_topic(
        title="Probe retry tuning",
        reason="Need a follow-up task with evidence.",
        tags=["probe"],
    )
    session = service.plan_session(topic=topic, planned_minutes=15, trigger="scheduled")
    experiment = service.record_experiment(
        topic=topic,
        session=session,
        hypothesis="A bounded retry policy reduces flaky probe noise.",
        method="Compare no-retry and bounded-retry probe results.",
        observations=["Bounded retry removed transient failures in the sample."],
        outcome="passed",
        compared_against=["no-retry"],
    )
    conclusion = service.submit_conclusion(
        topic=topic,
        session=session,
        experiments=[experiment],
        comparisons=["bounded-retry > no-retry"],
        summary="Use a bounded retry policy for probe execution follow-up review.",
        verified=True,
        recommendations=[
            LearningRecommendation(
                recommendation_type="propose_evolution_task",
                title="Review bounded probe retry policy",
                summary="Create a guarded supervisor-visible task with evidence attached.",
                evidence={"confidence": "medium"},
                constraints={"window": "night"},
            )
        ],
    )

    created = service.submit_to_supervisor_queue(conclusion=conclusion, queue=queue)

    assert len(created) == 1
    assert created[0]["title"] == "Review bounded probe retry policy"
    assert created[0]["trace_id"]
    assert created[0]["task_type"] == "self_evolution"
    assert created[0]["governance_task_type"] == "self_evolution"
    assert created[0]["task_family"] == "general_self_evolution"
    assert created[0]["execution_kind"] == "general_self_evolution"
    assert created[0]["metadata"]["conclusion_id"] == conclusion.conclusion_id
    assert created[0]["evidence"]["confidence"] == "medium"


def test_self_learning_service_marks_experiment_followups_as_self_learning(tmp_path):
    service = SelfLearningService(tmp_path / "self-learning")

    topic = service.create_topic(
        title="Executor smoke coverage",
        reason="Need a learn-only follow-up instead of an execution handoff.",
        tags=["executor", "learning"],
    )
    session = service.plan_session(topic=topic, planned_minutes=10, trigger="scheduled")
    conclusion = service.submit_conclusion(
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

    payload = service.build_supervisor_payload(conclusion)

    assert "task_type" not in payload["proposals"][0]
    assert payload["proposals"][0]["governance_task_type"] == "self_learning"
    assert payload["proposals"][0]["task_family"] == "self_learning"
    assert payload["proposals"][0]["execution_kind"] is None


def test_self_learning_queue_payload_prefers_canonical_runtime_profile_over_broad_task_type(tmp_path):
    service = SelfLearningService(tmp_path / "self-learning")
    queue = SelfEvolutionTaskQueue(tmp_path / "queue" / "self_evolution_queue.json")

    proposal = SupervisorTaskProposal(
        title="Promote body candidate",
        summary="Use canonical body-switch runtime semantics.",
        task_type="self_evolution",
        governance_task_type="self_evolution",
        task_family="body_switch",
        execution_kind="body_switch",
        source="self_learning",
    )

    task = queue.create_task(
        title=proposal.title,
        summary=proposal.summary,
        task_type=service._resolved_proposal_task_type(proposal),
        source=proposal.source,
        priority=proposal.priority,
        metadata=service._proposal_task_metadata(proposal),
        evidence=proposal.evidence,
        constraints=proposal.constraints,
    )
    created = service._serialize_task_payload(task)

    assert created["task_type"] == "self_evolution"
    assert created["governance_task_type"] == "self_evolution"
    assert created["task_family"] == "body_switch"
    assert created["execution_kind"] == "body_switch"
