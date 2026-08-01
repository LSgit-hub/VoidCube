from dataclasses import dataclass, field

from systems.supervisor.endogenous_candidate_pipeline import (
    EndogenousTaskCandidate,
    active_api_b_judgement_candidate_kinds,
    adaptive_factor_for_candidate,
    apply_adaptive_candidate_budget,
    build_scored_candidate,
    candidate_semantic_signature,
    merge_lm_led_candidate_stream,
    score_candidate,
)


@dataclass
class Candidate:
    title: str
    task_family: str
    governance_task_type: str
    utility: float
    metadata: dict = field(default_factory=dict)


@dataclass
class Policy:
    candidate_budget: int = 2
    exploratory_learning_quota: int = 1
    body_growth_quota: int = 1
    preferred_focus: str = "truthfulness"
    observation_bias: float = 0.0
    memory_continuity_bias: float = 0.5
    truthfulness_bias: float = 0.5
    learning_expansion_bias: float = 0.5
    governance_hygiene_bias: float = 0.5
    body_growth_bias: float = 0.5
    candidate_throttle: float = 0.0


def _candidate(kind: str, title: str, utility: float) -> Candidate:
    return Candidate(
        title=title,
        task_family="self_learning",
        governance_task_type="self_learning",
        utility=utility,
        metadata={"score_breakdown": {"candidate_kind": kind}},
    )


def test_active_api_b_candidate_kinds_ignore_terminal_and_malformed_tasks():
    kinds = active_api_b_judgement_candidate_kinds(
        [
            {"status": "planned", "metadata": {"candidate_kind": "truthfulness_review"}},
            {
                "status": "awaiting_review",
                "evidence": {
                    "score_breakdown": {"candidate_kind": "body_improvement"}
                },
            },
            {"status": "completed", "metadata": {"candidate_kind": "ignored"}},
            "invalid",
        ],
    )

    assert kinds == {"truthfulness_review", "body_improvement"}


def test_lm_led_merge_preserves_canonical_shell_baseline_and_limits_complement():
    policy = Policy(preferred_focus="truthfulness")
    lm_candidates = [
        _candidate("shell_baseline_learning", "LM baseline", 0.95),
        _candidate("truthfulness_review", "LM review", 0.9),
    ]
    heuristic_candidates = [
        _candidate("shell_baseline_learning", "Canonical baseline", 0.7),
        _candidate("truthfulness_review", "Heuristic review", 0.85),
        _candidate("memory_maintenance", "Memory sweep", 0.8),
        _candidate("governance_hygiene_review", "Governance review", 0.75),
        _candidate("body_improvement", "Body change", 0.7),
    ]

    merged = merge_lm_led_candidate_stream(
        lm_candidates=lm_candidates,
        heuristic_candidates=heuristic_candidates,
        adaptive_policy=policy,
    )

    assert [candidate.title for candidate in merged[:2]] == [
        "Canonical baseline",
        "LM review",
    ]
    assert "LM baseline" not in [candidate.title for candidate in merged]
    assert "Heuristic review" not in [candidate.title for candidate in merged]
    assert len(merged) == 4


def test_score_candidate_preserves_auditable_dimensions_and_penalties():
    utility, breakdown = score_candidate(
        candidate_kind="truthfulness_review",
        core_value_strength=1.0,
        urgency=0.8,
        novelty=0.6,
        specificity=0.7,
        execution_readiness=0.9,
        repetition_penalty=0.2,
        adaptive_factor=1.1,
    )

    assert 0 < utility < 1
    assert breakdown["candidate_kind"] == "truthfulness_review"
    assert breakdown["penalties"]["repetition_penalty"] == 0.2
    assert breakdown["utility"] == utility


def test_scored_candidate_factory_owns_projection_and_score_breakdown():
    candidate = build_scored_candidate(
        stable_key="truthfulness:test",
        title="Review evidence",
        summary="Review the current evidence gap.",
        priority="high",
        governance_task_type="self_learning",
        task_family="self_learning",
        execution_kind=None,
        value_tags=["truthfulness"],
        candidate_kind="truthfulness_review",
        score_inputs={
            "core_value_strength": 1.0,
            "urgency": 0.8,
            "novelty": 0.6,
            "specificity": 0.7,
            "execution_readiness": 0.9,
        },
        metadata={"rationale": "Evidence is incomplete."},
        evidence={"signal_source": "test"},
    )

    assert isinstance(candidate, EndogenousTaskCandidate)
    assert candidate.rationale() == "Evidence is incomplete."
    assert candidate.metadata["score_breakdown"]["candidate_kind"] == (
        "truthfulness_review"
    )
    projected = candidate.to_api_b_judgement_item()
    assert projected["evidence"]["signal_source"] == "test"
    assert projected["evidence"]["endogenous_drive"]["core_value_definitions"] == {
        "truthfulness": (
            "Surface uncertainty, correction signals, and evidence gaps before they harden."
        )
    }


def test_candidate_budget_prioritizes_focus_and_caps_learning_groups():
    candidates = [
        _candidate("exploratory_learning", "Research one", 0.99),
        _candidate("exploratory_learning", "Research two", 0.98),
        _candidate("truthfulness_review", "Review correction", 0.7),
    ]

    selected = apply_adaptive_candidate_budget(candidates, adaptive_policy=Policy())

    assert [item.title for item in selected] == ["Review correction", "Research one"]


def test_observation_budget_excludes_unrelated_candidates_and_signature_is_stable():
    policy = Policy(
        candidate_budget=3,
        preferred_focus="observation",
        observation_bias=0.8,
    )
    selected = apply_adaptive_candidate_budget(
        [
            _candidate("body_improvement", "Change body", 1.0),
            _candidate("truthfulness_review", "Review signals", 0.5),
        ],
        adaptive_policy=policy,
    )

    assert [item.title for item in selected] == ["Review signals"]
    assert candidate_semantic_signature(selected[0]) == (
        "truthfulness_review|self_learning|self_learning|review signals"
    )
    assert adaptive_factor_for_candidate(
        candidate_kind="truthfulness_review",
        adaptive_policy=policy,
    ) > 0
