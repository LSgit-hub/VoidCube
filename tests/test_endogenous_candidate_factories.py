from dataclasses import dataclass

from systems.supervisor.endogenous_candidate_factories import (
    body_improvement_constraints,
    build_body_improvement_candidate,
    build_governance_hygiene_review_candidate,
    build_memory_maintenance_candidate,
    build_truthfulness_review_candidate,
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


def test_memory_and_truthfulness_factories_preserve_stable_contracts():
    memory = build_memory_maintenance_candidate(
        urgency=0.8,
        backlog_pressure_penalty=0.2,
        adaptive_policy=Policy(),
        drive_judgement={"intent": {"rationale": "keep continuity"}},
        observation_checks={"memory": True},
        idle_seconds={"api_a_execution": 300},
    )
    truth = build_truthfulness_review_candidate(
        recent_errors=2,
        uncertainty_count=3,
        correction_signals=4,
        runtime_signal_present=True,
        backlog_pressure_penalty=0.1,
        adaptive_policy=Policy(),
        drive_judgement={"intent": {"rationale": "review uncertainty"}},
    )

    assert memory.stable_key == "continuity:memory_maintenance_sweep"
    assert memory.execution_kind == "memory_maintenance"
    assert memory.evidence["observation_checks"] == {"memory": True}
    assert memory.rationale() == "keep continuity"
    assert truth.stable_key == "truthfulness:review_correction_signals"
    assert truth.evidence["signal_source"] == "runtime_observation_snapshot"
    assert truth.evidence["correction_signals"] == 4
    assert truth.metadata["score_breakdown"]["candidate_kind"] == (
        "truthfulness_review"
    )


def test_governance_factory_is_review_only():
    candidate = build_governance_hygiene_review_candidate(
        urgency=0.7,
        api_b_judgement_count=5,
        adaptive_policy=Policy(),
        drive_judgement={},
    )

    assert candidate.stable_key == "continuity:governance_hygiene_review"
    assert candidate.task_family == "general_self_evolution"
    assert candidate.constraints == {"must_not_execute_without_review": True}
    assert candidate.evidence["trigger"] == "supervisor_backlog_governance"


def test_body_factory_projects_mapping_evidence_and_constraints():
    projection = {
        "mapping_key": "abc123",
        "mapping_source": "learning_evidence_structure_projection_v1",
        "target_slot_id": "slot-shell",
        "worktree_path": "F:/worktree",
        "target_paths": ["agent/context_engine.py"],
        "structure_domains": ["prompt_context"],
        "editable_dirs": ["agent"],
        "forbidden_patterns": ["config.yaml"],
        "max_files_changed": 3,
        "learning_quality_score": 85.0,
        "learning_refs": [{"mem_id": "mem-1"}],
        "evidence_summary": ["validated mapping"],
    }

    candidate = build_body_improvement_candidate(
        body_projection=projection,
        backlog_pressure_penalty=0.1,
        adaptive_policy=Policy(),
        drive_judgement={},
    )

    assert candidate.stable_key == "creativity:body_improvement:abc123"
    assert candidate.priority == "high"
    assert candidate.metadata["learning_task_ids"] == ["mem-1"]
    assert candidate.evidence["structure_mapping"]["target_paths"] == [
        "agent/context_engine.py"
    ]
    assert candidate.constraints == body_improvement_constraints(projection)
    assert candidate.constraints["must_commit"] is True
