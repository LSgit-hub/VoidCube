from types import SimpleNamespace

import pytest

from systems.supervisor.endogenous_drive_orchestration import (
    EndogenousDriveEvaluationContext,
    build_endogenous_drive_policy,
    evaluate_endogenous_drive,
)
from systems.supervisor.endogenous_drive_cycle import (
    EndogenousDriveCycleContext,
    run_endogenous_drive_cycle,
)


def test_drive_orchestration_owner_projects_runtime_policy_defaults_and_overrides():
    policy = build_endogenous_drive_policy(
        SimpleNamespace(
            endogenous_drive_learning_topic_cooldown_hours=18,
            endogenous_drive_body_improvement_cooldown_hours=7,
            endogenous_drive_topic_overlap_threshold=0.75,
            body_improvement_min_quality=72,
            body_improvement_editable_dirs=("agent/",),
            body_improvement_forbidden_patterns=("systems/**",),
            body_improvement_max_files=2,
        )
    )

    assert policy == {
        "learning_topic_cooldown_hours": 18,
        "body_improvement_cooldown_hours": 7,
        "topic_overlap_threshold": 0.75,
        "body_improvement_min_quality": 72.0,
        "body_improvement_editable_dirs": ["agent/"],
        "body_improvement_forbidden_patterns": ["systems/**"],
        "body_improvement_max_files": 2,
    }


@pytest.mark.asyncio
async def test_drive_orchestration_owner_runs_explicit_callback_pipeline():
    calls = []

    class Candidate:
        stable_key = "candidate:observe"

        def to_api_b_judgement_item(self):
            return {"stable_key": self.stable_key, "title": "Observe"}

    class Deliberation:
        def to_dict(self):
            return {
                "perception": {"system_posture": "stable"},
                "world_model": {"readiness": "ready"},
            }

    async def resolve_drive_input(request):
        calls.append(("resolve", request))
        return {
            "activity": {},
            "task_family_decisions": {},
            "governance_task_type_decisions": {},
        }

    def build_deliberation_report(**kwargs):
        calls.append(("deliberation", kwargs["drive_input"]))
        assert kwargs["drive_input"]["endogenous_drive_policy"][
            "dynamic_observation_bias_boost"
        ] == 0.2
        return Deliberation()

    def generate_candidates(**kwargs):
        calls.append(("candidates", kwargs))
        return [Candidate()]

    context = EndogenousDriveEvaluationContext(
        runtime_config=SimpleNamespace(
            endogenous_drive_enabled=True,
            endogenous_drive_max_candidates=4,
            endogenous_drive_learning_topic_cooldown_hours=20,
        ),
        resolve_drive_input_request=resolve_drive_input,
        load_self_regulation=lambda: {
            "dynamic_observation_bias_boost": 0.2,
            "last_reason": "persisted",
        },
        load_drive_history=lambda: {
            "strategy_memory": {},
            "judgements": [],
            "outcomes": [],
        },
        normalize_strategy_memory=lambda value: dict(value or {}),
        api_b_judgement_task_summaries=lambda limit: [],
        api_a_execution_lane_task_summaries=lambda limit: [],
        build_deliberation_report=build_deliberation_report,
        generate_candidates=generate_candidates,
        existing_drive_keys=lambda: {"existing"},
        schedule_candidate_items=lambda candidates: [
            candidate.to_api_b_judgement_item() for candidate in candidates
        ],
        lm_generation_application_state=lambda: SimpleNamespace(
            reasoning_state={},
            candidate_repass_proposals=None,
        ),
        derive_cognitive_self_regulation=lambda **kwargs: {
            "dynamic_observation_bias_boost": 0.0,
            "last_reason": None,
        },
        release_cleared_observation_carryover=lambda **kwargs: kwargs[
            "cognitive_self_regulation"
        ],
        governance_channels_from_deliberation=lambda deliberation: {
            "task_candidates": []
        },
        persist_evaluation=lambda **kwargs: pytest.fail(
            "non-persistent evaluation must not write state"
        ),
        load_governance_events=lambda: {"events": [{"event_type": "review"}]},
        build_cognition_state=lambda **kwargs: {
            "status": "evaluated",
            "candidate_count": len(kwargs["candidate_items"]),
        },
        record_ui_activity=lambda *args, **kwargs: calls.append(
            ("activity", args, kwargs)
        ),
        build_response_fields=lambda **kwargs: {
            "drive_input": kwargs["drive_input"]
        },
        drive_posture_from_deliberation=lambda deliberation: {
            "signal_type": "drive_posture_signal"
        },
        core_values=["truthfulness"],
    )

    result = await evaluate_endogenous_drive(
        request={"record_activity": True, "persist_evaluation": False},
        context=context,
    )

    assert result["status"] == "evaluated"
    assert result["enabled"] is True
    assert result["count"] == 1
    assert result["core_values"] == ["truthfulness"]
    assert result["cognition_state"] == {"status": "evaluated", "candidate_count": 1}
    assert result["governance_event_stream"]["events"] == [
        {"event_type": "review"}
    ]
    assert [call[0] for call in calls] == [
        "resolve",
        "deliberation",
        "candidates",
        "activity",
    ]


@pytest.mark.asyncio
async def test_drive_cycle_owner_persists_before_planning_and_touches_gateway():
    calls = []

    async def evaluate_drive(request):
        calls.append("evaluate")
        return {
            "drive_input": {"task_family": "self_learning"},
            "deliberation": {"needs": []},
            "candidates": [
                {
                    "stable_key": "learning:one",
                    "metadata": {"score_breakdown": {"candidate_kind": "learning"}},
                }
            ],
            "drive_posture": {"payload": {"preferred_focus": "learning"}},
            "governance_channels": {"task_candidates": []},
            "governance_event_stream": {"events": []},
            "self_regulation": {},
        }

    async def plan_task(request):
        calls.append(("plan", request))
        return {
            "tasks": [
                {
                    "task_id": "task-1",
                    "metadata": {"endogenous_drive_key": "learning:one"},
                }
            ]
        }

    async def touch_gateway(activity_kind, *, metadata=None):
        calls.append(("touch", activity_kind, metadata))

    context = EndogenousDriveCycleContext(
        runtime_config=SimpleNamespace(endogenous_drive_enabled=True),
        evaluate_drive=evaluate_drive,
        drive_input_fields_from_evaluation=lambda evaluation: {
            "drive_input": dict(evaluation["drive_input"])
        },
        load_drive_history=lambda: {"version": 1},
        load_governance_events=lambda: {"version": 1},
        load_cognition_state=lambda: {"version": 1},
        persist_evaluation=lambda **kwargs: calls.append("persist") or {
            "candidate_items": list(kwargs["candidate_items"]),
            "governance_event_stream": {"events": [{"event_type": "planned"}]},
            "cognition_state": {"status": "evaluated"},
        },
        restore_evaluation_snapshots=lambda **kwargs: calls.append("restore"),
        lm_generation_application_state=lambda: SimpleNamespace(
            reasoning_state={}
        ),
        plan_autonomous_chain_task=plan_task,
        record_ui_activity=lambda *args, **kwargs: calls.append("activity"),
        touch_gateway_activity=touch_gateway,
    )

    result = await run_endogenous_drive_cycle(context=context)

    assert result["status"] == "planned"
    assert result["planned"] == 1
    assert calls[:2] == ["evaluate", "persist"]
    assert calls[2][0] == "plan"
    assert calls[2][1]["items"][0]["stable_key"] == "learning:one"
    assert calls[3] == "activity"
    assert calls[4][0:2] == ("touch", "autonomous_chain_plan")
