from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import voidcube.systems.supervisor.endogenous_drive as endogenous_drive_module
from voidcube.systems.supervisor.endogenous_drive import EndogenousDriveEngine
from voidcube.systems.supervisor.endogenous_generation_state import LmGenerationStateOwner
from voidcube.systems.supervisor.endogenous_proposal_port import (
    project_lm_generation_application_state,
)
from voidcube.systems.supervisor.endogenous_cognition_state import (
    build_cognition_state_projection,
)
from voidcube.systems.supervisor.endogenous_proposal_cognition import (
    compact_proposal_memory,
    build_proposal_cognition_projection,
)
from voidcube.systems.supervisor.endogenous_proposals import (
    align_lm_references,
    build_lm_task_generation_request,
    constraints_for_lm_candidate_kind,
    execute_lm_task_generation,
    execute_lm_task_generation_from_runtime_config,
    generate_lm_task_proposals,
    normalize_lm_cognitive_assessment,
    normalize_lm_execution_mode,
    normalize_lm_proposal,
    is_lm_task_generation_enabled,
    supervisor_advisory_for_lm_proposal,
    task_type_for_candidate_kind,
)


def test_lm_generation_request_owner_normalizes_charter_role_and_limit():
    request = build_lm_task_generation_request(
        charter_model={},
        core_mission="  govern evidence  ",
        task_generation_principles=["reason first"],
        model_role="  ",
        max_candidates=-4,
    )

    assert request.cognition_charter["core_mission"] == "govern evidence"
    assert request.cognition_charter["task_generation_policy"] == ["reason first"]
    assert request.role == "governance_reasoner"
    assert request.max_candidates == 0


def test_lm_generation_gate_owner_normalizes_runtime_flag():
    assert is_lm_task_generation_enabled(
        SimpleNamespace(endogenous_drive_lm_task_generation_enabled=True)
    ) is True
    assert is_lm_task_generation_enabled(
        SimpleNamespace(endogenous_drive_lm_task_generation_enabled=False)
    ) is False
    assert is_lm_task_generation_enabled(None) is False


def test_latest_lm_generation_state_is_a_single_defensive_application_projection():
    generation_state = LmGenerationStateOwner()
    engine = EndogenousDriveEngine(generation_state=generation_state)
    generation_state.record(
        context_snapshot={
            "status": "completed",
            "assessment": {"gaps": ["missing trace"]},
        },
        proposals=[
            {"candidate_kind": "observation", "metadata": {"tags": ["grounded"]}}
        ],
    )

    state = engine.get_latest_lm_task_generation_state()

    assert state == {
        "context": {
            "status": "completed",
            "assessment": {"gaps": ["missing trace"]},
        },
        "proposals": [
            {"candidate_kind": "observation", "metadata": {"tags": ["grounded"]}}
        ],
    }

    state["context"]["assessment"]["gaps"].append("weak source")
    state["proposals"][0]["metadata"]["tags"].append("reviewable")

    assert engine.get_latest_lm_task_generation_state() == {
        "context": {
            "status": "completed",
            "assessment": {"gaps": ["missing trace"]},
        },
        "proposals": [
            {"candidate_kind": "observation", "metadata": {"tags": ["grounded"]}}
        ],
    }


def test_engine_generation_execution_has_one_state_owner_and_override_is_read_only(
    monkeypatch,
):
    generation_state = LmGenerationStateOwner()
    engine = EndogenousDriveEngine(
        config=SimpleNamespace(
            service_runtime=SimpleNamespace(
                endogenous_drive_lm_task_generation_enabled=True,
            ),
            execution=SimpleNamespace(),
        ),
        generation_state=generation_state,
    )
    execution_calls = []

    monkeypatch.setattr(
        endogenous_drive_module,
        "build_lm_evidence_packet_from_runtime_config",
        lambda **kwargs: {"evidence": "packet"},
    )
    monkeypatch.setattr(
        endogenous_drive_module,
        "execute_lm_task_generation_from_runtime_config",
        lambda **kwargs: execution_calls.append(kwargs)
        or SimpleNamespace(
            context_snapshot={
                "status": "completed",
                "cognitive_assessment": {"focus": "fresh"},
            },
            proposals=[{"candidate_kind": "observation"}],
        ),
    )
    materialization_calls = []

    def materialize(**kwargs):
        materialization_calls.append(kwargs)
        return list(kwargs["proposals"])

    monkeypatch.setattr(
        endogenous_drive_module,
        "materialize_lm_proposals_for_deliberation",
        materialize,
    )
    common = {
        "existing_keys": set(),
        "deliberation": SimpleNamespace(),
        "drive_context": {},
        "memory_plan": {},
        "self_learning_plan": {},
        "autonomous_improvement_plan": {},
    }

    assert engine._llm_task_proposals(**common) == [
        {"candidate_kind": "observation"}
    ]
    assert len(execution_calls) == 1
    assert materialization_calls[0]["cognitive_assessment"] == {"focus": "fresh"}
    assert generation_state.snapshot()["proposals"] == [
        {"candidate_kind": "observation"}
    ]

    assert engine._llm_task_proposals(
        **common,
        proposals_override=[{"candidate_kind": "override"}],
    ) == [{"candidate_kind": "override"}]
    assert len(execution_calls) == 1
    assert materialization_calls[1]["cognitive_assessment"] == {"focus": "fresh"}
    assert generation_state.snapshot()["proposals"] == [
        {"candidate_kind": "observation"}
    ]


def test_lm_generation_application_port_projects_one_snapshot_for_both_consumers():
    runtime_config = SimpleNamespace(
        endogenous_drive_lm_task_generation_enabled=True
    )
    state = {
        "context": {"status": "completed", "cognitive_assessment": {"focus": "review"}},
        "proposals": [{"candidate_kind": "observation", "metadata": {"tags": ["evidence"]}}],
    }
    loader_calls = []

    def load_state():
        loader_calls.append(True)
        return state

    application_state = project_lm_generation_application_state(
        runtime_config=runtime_config,
        state_loader=load_state,
    )

    assert application_state.reasoning_state == state["context"]
    assert application_state.candidate_repass_proposals == state["proposals"]
    assert len(loader_calls) == 1

    application_state.candidate_repass_proposals[0]["metadata"]["tags"].append(
        "isolated"
    )
    assert state["proposals"][0]["metadata"]["tags"] == ["evidence"]


def test_lm_generation_application_port_preserves_disabled_and_unavailable_states():
    disabled = project_lm_generation_application_state(
        runtime_config=SimpleNamespace(
            endogenous_drive_lm_task_generation_enabled=False
        ),
        state_loader=lambda: pytest.fail("disabled gate must not load state"),
    )
    unavailable = project_lm_generation_application_state(
        runtime_config=SimpleNamespace(
            endogenous_drive_lm_task_generation_enabled=True
        ),
        state_loader=lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )

    assert disabled.reasoning_state == {}
    assert disabled.candidate_repass_proposals is None
    assert unavailable.reasoning_state == {}
    assert unavailable.candidate_repass_proposals is None


def test_cognition_state_owner_assembles_read_model_from_explicit_snapshots():
    state = build_cognition_state_projection(
        enabled=True,
        deliberation={
            "perception": {"uncertainty_count": 2},
            "world_model": {"readiness": "ready"},
            "reflection": {"dominant_constraint": "evidence"},
            "adaptive_policy": {"preferred_focus": "truthfulness"},
        },
        governance_channels={
            "task_candidates": [{"title": "Review"}],
            "observation_requests": [],
            "governance_review_requests": [{"title": "Check"}],
            "truthfulness_alerts": [],
            "autonomy_alignment_requests": [],
        },
        governance_event_stream={"events": [{"event_type": "review"}]},
        self_regulation={"dynamic_candidate_throttle_boost": 0.2},
        drive_posture={"signal_type": "drive_posture_signal"},
        context_key="truthfulness|ready|none",
        strategy_memory={
            "focus_stats": {"truthfulness": {"judged": 2}},
            "agenda_topic_stats": {"repair": {"judged": 1}},
            "observation_target_stats": {"trace": {"observed": 1}},
            "meta_governance_stats": {"observe": {"count": 1}},
            "contextual_focus_stats": {"truthfulness|ready|none": {"judged": 1}},
        },
        corrective_mode={"mode": "observe"},
        attention_agenda={"entries": [{"topic": "repair"}]},
        uncertainty_ledger={"active_count": 1},
        observation_program={"entries": [{"target": "trace"}]},
        meta_governance={"mode": "observe"},
        judgement_core={"summary": "Review evidence."},
        proposal_cognition={"summary": "posture=observe_first; drift=stable."},
    )

    assert state["status"] == "evaluated"
    assert state["governance"]["channel_counts"] == {
        "task_candidates": 1,
        "observation_requests": 0,
        "governance_review_requests": 1,
        "truthfulness_alerts": 0,
        "autonomy_alignment_requests": 0,
    }
    assert state["strategy_memory"]["current_agenda_topic_stats"]["repair"] == {
        "judged": 1
    }
    assert state["recent_events"] == [{"event_type": "review"}]


def test_proposal_cognition_owner_assembles_stable_trace_and_assessment_views():
    result = build_proposal_cognition_projection(
        lm_reasoning_state={
            "status": "completed",
            "model_role": "reasoner",
            "proposal_count": 2,
            "charter": {"core_mission": "Stay grounded."},
        },
        cognitive_control_policy={"reference_alignment_min_score": 0.8},
        active_cognitive_posture_profile={"name": "observe_first"},
        meta_cognition_profile={
            "available": True,
            "current_judgement": "observe",
            "dominant_constraint": "evidence",
            "grounding_pressure": "medium",
            "dominant_failure_mode": "drift",
            "governance_posture": "observe",
            "priority_signals": ["grounding"],
            "top_self_iteration_domain": "memory",
            "top_self_iteration_hypothesis": "trace first",
        },
        cognitive_assessment_memory={
            "available": True,
            "current_judgement": "observe",
            "dominant_constraint": "evidence",
            "why_not_improvement_now": "missing trace",
            "why_not_improvement_now_count": 2,
            "self_iteration_target": "memory",
            "self_iteration_hypothesis": "trace first",
        },
        compact_memory={"proposal_drift_memory": {"drift_state": "stable"}},
        current_candidates={"count": 1},
    )

    assert result["summary"] == "posture=observe_first; drift=stable."
    assert result["lm_trace"] == {
        "available": True,
        "status": "completed",
        "model_role": "reasoner",
        "charter_core_mission": "Stay grounded.",
        "proposal_count": 2,
    }
    assert result["assessment_trace"]["why_not_improvement_now_count"] == 2
    assert result["meta_cognition_profile"]["self_iteration_focus"] == {
        "domain": "memory",
        "hypothesis": "trace first",
    }


def test_proposal_memory_compaction_owner_keeps_bounded_projection_contract():
    compact = compact_proposal_memory(
        recent_reference_alignment={
            "available": True,
            "average_alignment_score": 1.4,
            "weak_or_partial_count": 2,
            "entry_count": 3,
            "primary_missing_evidence_node": "trace",
        },
        proposal_drift_memory={
            "available": True,
            "average_score": 0.4,
            "drift_state": "correcting",
            "quality_counts": {"weak": 1},
            "missing_priority_basis_count": 2,
        },
        recent_cognitive_alignment={
            "available": True,
            "average_score": 0.7,
            "quality_counts": {"partial": 1},
            "dominant_task_shape": "review",
            "reason_count": 2,
            "entry_count": 1,
        },
        cognitive_assessment_memory={
            "available": True,
            "current_judgement": "observe",
            "current_judgement_count": 1,
        },
        self_iteration_hypotheses={
            "available": True,
            "dominant_hypothesis": "trace first",
            "hypothesis_count": 2,
        },
        self_iteration_trend_memory={
            "available": True,
            "dominant_target": "memory",
            "trend_state": "stable",
            "target_count": 2,
            "target_signal_count": 3,
        },
        switch_self_regulation_memory={
            "available": True,
            "preferred_switch_bias": "stay",
            "average_stay_quality": 0.8,
            "stay_or_switch_count": 1,
        },
        post_task_effect_memory={
            "available": True,
            "effect_direction": "mixed",
            "average_quality_score": 0.9,
        },
    )

    assert compact["recent_reference_alignment"]["average_alignment_score"] == 1.0
    assert compact["recent_reference_alignment"]["primary_missing_evidence_node"] == "trace"
    assert compact["proposal_drift_memory"]["drift_state"] == "correcting"
    assert compact["self_iteration_trend_memory"]["target_count"] == 3
    assert compact["switch_self_regulation_memory"]["preferred_switch_bias"] == "stay"
    assert "recent_entries" not in compact["recent_reference_alignment"]


def test_lm_generation_runtime_config_adapter_preserves_owner_execution():
    client = StubCompletionClient({"proposals": [{"candidate_kind": "observation"}]})
    runtime_config = SimpleNamespace(
        endogenous_drive_cognition_charter={},
        endogenous_drive_core_mission_prompt="Observe grounded work.",
        endogenous_drive_task_generation_principles=["stay grounded"],
        endogenous_drive_lm_task_model_role="  governance_reasoner  ",
        endogenous_drive_lm_task_max_candidates=2,
    )

    execution = execute_lm_task_generation_from_runtime_config(
        evidence_packet={"cognitive_posture": {"name": "observe_first"}},
        runtime_config=runtime_config,
        client_resolver=lambda **_: (client, "test-model"),
    )

    assert execution.proposals == [{"candidate_kind": "observation"}]
    assert execution.context_snapshot["model_role"] == "governance_reasoner"
    assert execution.context_snapshot["max_candidates"] == 2


def test_lm_generation_execution_owner_returns_proposals_and_context_snapshot():
    client = StubCompletionClient(
        {
            "cognitive_assessment": {"current_judgement": "observe first"},
            "proposals": [{"candidate_kind": "observation", "title": "Inspect"}],
        }
    )
    request = build_lm_task_generation_request(
        charter_model={},
        core_mission="Choose grounded work.",
        task_generation_principles=[],
        model_role="governance_reasoner",
        max_candidates=2,
    )

    execution = execute_lm_task_generation(
        evidence_packet={"cognitive_posture": {"name": "observe_first"}},
        request=request,
        client_resolver=lambda **_: (client, "test-model"),
    )

    assert execution.proposals == [
        {"candidate_kind": "observation", "title": "Inspect"}
    ]
    assert execution.context_snapshot["status"] == "completed"
    assert execution.context_snapshot["proposal_count"] == 1
    assert execution.context_snapshot["cognitive_assessment"] == {
        "current_judgement": "observe first"
    }


class StubCompletionClient:
    def __init__(self, response: Any = None, *, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        task: str,
    ) -> Any:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_payload": user_payload,
                "task": task,
            }
        )
        if self.error is not None:
            raise self.error
        return self.response


def _generate(
    response: Any,
    *,
    error: Exception | None = None,
):
    client = StubCompletionClient(response, error=error)
    result = generate_lm_task_proposals(
        evidence_packet={"cognitive_posture": {"name": "evidence_repair_first"}},
        cognition_charter={"core_mission": "Choose the next grounded task."},
        role="governance_reasoner",
        max_candidates=3,
        client_resolver=lambda **_: (client, "test-model"),
    )
    return result, client


@pytest.mark.parametrize(
    ("charter", "max_candidates", "expected_error"),
    [
        ({}, 3, "missing_core_mission"),
        ({"core_mission": "mission"}, 0, "max_candidates_disabled"),
    ],
)
def test_generation_is_disabled_before_resolving_client(
    charter,
    max_candidates,
    expected_error,
):
    resolver_calls = []

    result = generate_lm_task_proposals(
        evidence_packet={},
        cognition_charter=charter,
        role="governance_reasoner",
        max_candidates=max_candidates,
        client_resolver=lambda **kwargs: resolver_calls.append(kwargs),
    )

    assert result.status == "disabled"
    assert result.proposals == []
    assert result.error == expected_error
    assert resolver_calls == []


def test_generation_reports_unavailable_client_and_resolution_error():
    unavailable = generate_lm_task_proposals(
        evidence_packet={},
        cognition_charter={"core_mission": "mission"},
        role="governance_reasoner",
        max_candidates=2,
        client_resolver=lambda **_: (None, ""),
    )

    def failing_resolver(**_):
        raise RuntimeError("resolver failed")

    failed = generate_lm_task_proposals(
        evidence_packet={},
        cognition_charter={"core_mission": "mission"},
        role="governance_reasoner",
        max_candidates=2,
        client_resolver=failing_resolver,
    )

    assert unavailable.status == "llm_unavailable"
    assert unavailable.error == "llm_client_unavailable"
    assert failed.status == "llm_unavailable"
    assert failed.error == "resolver failed"


def test_generation_reports_completion_error():
    result, client = _generate(None, error=RuntimeError("completion failed"))

    assert result.status == "generation_error"
    assert result.proposals == []
    assert result.error == "completion failed"
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        ("not a mapping", "non_dict_response"),
        ({"cognitive_assessment": {"current_judgement": "review first"}}, "missing_proposals_list"),
    ],
)
def test_generation_rejects_invalid_response_shapes(response, expected_error):
    result, _ = _generate(response)

    assert result.status == "invalid_response"
    assert result.proposals == []
    assert result.error == expected_error


def test_generation_returns_filtered_proposals_and_normalized_assessment():
    result, client = _generate(
        {
            "cognitive_assessment": {
                "current_judgement": "  Observe before changing  ",
                "primary_grounding_gaps": ["missing trace", "", "weak source"],
                "stay_or_switch": "STAY",
            },
            "proposals": [
                {"candidate_kind": "truthfulness_review", "title": "Review"},
                "discard me",
            ],
        }
    )

    assert result.status == "completed"
    assert result.error is None
    assert result.proposals == [
        {"candidate_kind": "truthfulness_review", "title": "Review"}
    ]
    assert result.cognitive_assessment == {
        "current_judgement": "Observe before changing",
        "primary_grounding_gaps": ["missing trace", "weak source"],
        "stay_or_switch": "stay",
    }
    assert client.calls[0]["task"] == "scholar.revision"
    assert "task_generation" in client.calls[0]["user_payload"]


def test_cognitive_assessment_normalization_accepts_scalar_list_fields():
    normalized = normalize_lm_cognitive_assessment(
        {
            "why_not_improvement_now": "insufficient evidence",
            "stay_or_switch": "invalid",
        }
    )

    assert normalized == {
        "why_not_improvement_now": ["insufficient evidence"],
    }


def test_proposal_normalization_binds_references_and_builds_advisory():
    normalized = normalize_lm_proposal(
        {
            "candidate_kind": "body_improvement",
            "title": "Improve retrieval",
            "summary": "Change retrieval only after validating the weak evidence.",
            "confidence": 0.3,
            "evidence_summary": "one weak observation",
            "risk_level": "invalid",
            "execution_mode": "guarded_execution",
            "referenced_evidence_nodes": ["retrieval", "missing-node"],
            "referenced_agenda_nodes": ["focus:grounding"],
            "blocking_factors": ["needs review"],
        },
        evidence_graph={
            "nodes": [
                {"topic": "retrieval", "avg_confidence": 0.3, "priority": 0.8},
            ]
        },
        agenda_graph={"focus": "grounding", "focus_confidence": 0.8},
    )

    assert normalized is not None
    assert normalized.task_type == "improvement"
    assert normalized.risk_level == "high"
    assert normalized.evidence_level == "weak"
    assert normalized.evidence_summary == ["one weak observation"]
    assert normalized.reference_alignment["matched_evidence_nodes"] == ["retrieval"]
    assert normalized.reference_alignment["missing_evidence_nodes"] == ["missing-node"]
    assert normalized.reference_alignment["alignment_quality"] == "weak"
    assert normalized.supervisor_advisory["recommended_observation_required"] is True
    assert normalized.supervisor_advisory["recommended_execution_mode"] == (
        "review_then_handoff"
    )


def test_proposal_normalization_rejects_incomplete_identity_fields():
    assert (
        normalize_lm_proposal(
            {"candidate_kind": "exploratory_learning", "title": "Missing summary"},
            evidence_graph={},
            agenda_graph={},
        )
        is None
    )


def test_reference_alignment_and_advisory_are_independently_reusable():
    alignment = align_lm_references(
        referenced_evidence_nodes=[],
        referenced_agenda_nodes=[],
        evidence_graph={"nodes": [{"topic": "grounding", "priority": 0.9}]},
        agenda_graph={"focus": "review", "focus_confidence": 0.9},
    )
    advisory = supervisor_advisory_for_lm_proposal(
        candidate_kind="exploratory_learning",
        evidence_level="moderate",
        risk_level="medium",
        observation_required=False,
        execution_mode="guarded_execution",
        blocking_factors=[],
        reference_alignment=alignment,
    )

    assert alignment["grounding_penalty"] > 0
    assert alignment["alignment_quality"] == "weak"
    assert advisory["recommended_execution_mode"] == "review_then_handoff"


def test_candidate_defaults_are_canonical_without_legacy_aliases():
    assert task_type_for_candidate_kind("shell_baseline_learning") == "learning"
    assert normalize_lm_execution_mode(
        "review_then_backlog",
        candidate_kind="exploratory_learning",
    ) == "review_then_handoff"
    assert constraints_for_lm_candidate_kind("shell_baseline_learning") == {
        "execution_policy": "learn_shell_baseline",
        "must_not_modify_active_body": True,
    }
