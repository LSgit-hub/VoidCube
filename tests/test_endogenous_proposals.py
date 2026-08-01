from __future__ import annotations

from typing import Any

import pytest

from systems.supervisor.endogenous_proposals import (
    align_lm_references,
    constraints_for_lm_candidate_kind,
    generate_lm_task_proposals,
    normalize_lm_cognitive_assessment,
    normalize_lm_execution_mode,
    normalize_lm_proposal,
    supervisor_advisory_for_lm_proposal,
    task_type_for_candidate_kind,
)


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
