import asyncio
import threading
from types import SimpleNamespace

import pytest

from systems.supervisor.endogenous_drive_orchestration import (
    EndogenousDriveEvaluationContext,
    build_endogenous_drive_policy,
    evaluate_endogenous_drive,
)
from systems.supervisor.endogenous_policy import (
    HISTORICAL_OBSERVATION_CARRYOVER_RELEASED,
)
from systems.supervisor.autonomous_cycle_service import AutonomousCycleService
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
    event_loop_thread = threading.get_ident()
    candidate_threads = []

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
        candidate_threads.append(threading.get_ident())
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
        load_evolution_foundation=lambda: {
            "mode": "shadow_read_only",
            "shadow_tasks": [
                {
                    "task_kind": "fill_self_cognition",
                    "execution_allowed": False,
                }
            ],
        },
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
    assert result["drive_input"]["evolution_foundation"]["mode"] == "shadow_read_only"
    assert result["governance_event_stream"]["events"] == [
        {"event_type": "review"}
    ]
    assert [call[0] for call in calls] == [
        "resolve",
        "deliberation",
        "candidates",
        "activity",
    ]
    assert candidate_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_drive_orchestration_rebuilds_after_historical_observation_carryover_release():
    deliberation_policies = []

    class Deliberation:
        def to_dict(self):
            return {"perception": {}, "reflection": {}}

    def build_deliberation_report(**kwargs):
        policy = dict(kwargs["drive_input"]["endogenous_drive_policy"])
        deliberation_policies.append(policy)
        return Deliberation()

    context = EndogenousDriveEvaluationContext(
        runtime_config=SimpleNamespace(
            endogenous_drive_enabled=True,
            endogenous_drive_max_candidates=1,
        ),
        resolve_drive_input_request=lambda request: asyncio.sleep(
            0,
            result={
                "activity": {},
                "task_family_decisions": {},
                "governance_task_type_decisions": {},
            },
        ),
        load_self_regulation=lambda: {},
        load_drive_history=lambda: {},
        normalize_strategy_memory=lambda value: {},
        api_b_judgement_task_summaries=lambda limit: [],
        api_a_execution_lane_task_summaries=lambda limit: [],
        build_deliberation_report=build_deliberation_report,
        generate_candidates=lambda **kwargs: [],
        existing_drive_keys=lambda: set(),
        schedule_candidate_items=lambda candidates: [],
        lm_generation_application_state=lambda: SimpleNamespace(
            reasoning_state={},
            candidate_repass_proposals=None,
        ),
        derive_cognitive_self_regulation=lambda **kwargs: {
            "dynamic_candidate_throttle_boost": 0.0,
            "dynamic_observation_bias_boost": 0.0,
            "dynamic_truthfulness_bias_boost": 0.0,
            "dynamic_learning_expansion_suppression": 0.0,
        },
        release_cleared_observation_carryover=lambda **kwargs: {
            **kwargs["cognitive_self_regulation"],
            HISTORICAL_OBSERVATION_CARRYOVER_RELEASED: True,
            "last_reason": "cleared_historical_window_releases_composite_observation_carryover",
        },
        governance_channels_from_deliberation=lambda deliberation: {},
        persist_evaluation=lambda **kwargs: pytest.fail(
            "non-persistent evaluation must not write state"
        ),
        load_governance_events=lambda: {},
        build_cognition_state=lambda **kwargs: {},
        record_ui_activity=lambda *args, **kwargs: None,
        build_response_fields=lambda **kwargs: {
            "drive_input": kwargs["drive_input"]
        },
        drive_posture_from_deliberation=lambda deliberation: {},
        core_values=[],
    )

    result = await evaluate_endogenous_drive(
        request={"record_activity": False, "persist_evaluation": False},
        context=context,
    )

    assert len(deliberation_policies) == 2
    assert HISTORICAL_OBSERVATION_CARRYOVER_RELEASED not in deliberation_policies[0]
    assert deliberation_policies[1][HISTORICAL_OBSERVATION_CARRYOVER_RELEASED] is True
    assert result["drive_input"]["endogenous_drive_policy"][
        HISTORICAL_OBSERVATION_CARRYOVER_RELEASED
    ] is True


@pytest.mark.asyncio
async def test_drive_orchestration_keeps_event_loop_responsive_during_candidate_generation():
    started = threading.Event()
    release = threading.Event()

    class Deliberation:
        def to_dict(self):
            return {}

    def generate_candidates(**kwargs):
        del kwargs
        started.set()
        release.wait(timeout=2)
        return []

    context = EndogenousDriveEvaluationContext(
        runtime_config=SimpleNamespace(
            endogenous_drive_enabled=True,
            endogenous_drive_max_candidates=1,
        ),
        resolve_drive_input_request=lambda request: asyncio.sleep(0, result={}),
        load_self_regulation=lambda: {},
        load_drive_history=lambda: {},
        normalize_strategy_memory=lambda value: {},
        api_b_judgement_task_summaries=lambda limit: [],
        api_a_execution_lane_task_summaries=lambda limit: [],
        build_deliberation_report=lambda **kwargs: Deliberation(),
        generate_candidates=generate_candidates,
        existing_drive_keys=lambda: set(),
        schedule_candidate_items=lambda candidates: [],
        lm_generation_application_state=lambda: SimpleNamespace(
            reasoning_state={},
            candidate_repass_proposals=None,
        ),
        derive_cognitive_self_regulation=lambda **kwargs: {},
        release_cleared_observation_carryover=lambda **kwargs: {},
        governance_channels_from_deliberation=lambda deliberation: {},
        persist_evaluation=lambda **kwargs: {},
        load_governance_events=lambda: {},
        build_cognition_state=lambda **kwargs: {},
        record_ui_activity=lambda *args, **kwargs: None,
        build_response_fields=lambda **kwargs: {},
        drive_posture_from_deliberation=lambda deliberation: {},
        core_values=[],
    )

    task = asyncio.create_task(
        evaluate_endogenous_drive(
            request={"record_activity": False, "persist_evaluation": False},
            context=context,
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    loop_tick = asyncio.create_task(asyncio.sleep(0, result="responsive"))
    assert await asyncio.wait_for(loop_tick, timeout=0.2) == "responsive"
    release.set()
    await task


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


@pytest.mark.asyncio
async def test_autonomous_cycle_service_skips_overlapping_drive_cycle():
    started = asyncio.Event()
    release = asyncio.Event()

    async def evaluate_drive(request):
        del request
        started.set()
        await release.wait()
        return {"candidates": []}

    service = AutonomousCycleService(
        runtime_config=SimpleNamespace(
            endogenous_drive_enabled=True,
            endogenous_drive_interval=900,
            autonomous_chain_review_interval=300,
        ),
        evaluate_drive=evaluate_drive,
        drive_input_fields_from_evaluation=lambda evaluation: {},
        load_drive_history=lambda: {},
        load_governance_events=lambda: {},
        load_cognition_state=lambda: {},
        persist_evaluation=lambda **kwargs: {},
        restore_evaluation_snapshots=lambda **kwargs: None,
        lm_generation_application_state=lambda: SimpleNamespace(reasoning_state={}),
        plan_autonomous_chain_task=lambda request: asyncio.sleep(0, result={"tasks": []}),
        record_ui_activity=lambda *args, **kwargs: None,
        touch_gateway_activity=lambda *args, **kwargs: asyncio.sleep(0),
        run_review_cycle=lambda request: asyncio.sleep(0, result={}),
        update_drive_schedule=lambda last_at, next_at: None,
        update_review_schedule=lambda last_at, next_at: None,
    )

    first = asyncio.create_task(service.run_drive_cycle())
    await started.wait()
    overlapping = await service.run_drive_cycle()
    release.set()
    completed = await first

    assert overlapping == {
        "status": "skipped",
        "skipped": "cycle_already_running",
        "planned": 0,
        "tasks": [],
    }
    assert completed["status"] == "idle"
