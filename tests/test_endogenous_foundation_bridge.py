from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from systems.evolution_evaluation import (
    BenchmarkCase,
    BenchmarkPack,
    ExperimentResult,
    ExperimentSpec,
    HardGateResult,
    JsonEvaluationRepository,
    MetricDelta,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
)
from systems.research_knowledge import (
    JsonKnowledgeRepository,
    KnowledgeArtifact,
    KnowledgeClaim,
    KnowledgeSource,
)
from systems.self_cognition import (
    HealthMetric,
    JsonSelfCognitionRepository,
    ModuleDependency,
    RuntimeCapability,
    SelfCognitionSnapshot,
)
from systems.supervisor.endogenous_foundation_bridge import (
    EndogenousFoundationReadOnlyProjection,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]

NOW = datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc)


def _snapshot() -> SelfCognitionSnapshot:
    return SelfCognitionSnapshot.create(
        body_id="body-a",
        git_commit="commit-a",
        config_digest="a" * 64,
        modules=(ModuleDependency(module="agent.main", dependencies=("json",)),),
        capabilities=(RuntimeCapability(name="python", capability_type="runtime"),),
        health_metrics=(HealthMetric(name="tests", value=1.0, unit="ratio", status="healthy"),),
        known_gaps=(),
        uncovered_areas=(),
        collector_version="collector-1",
        collected_at=NOW - timedelta(hours=1),
    )


def _artifact() -> KnowledgeArtifact:
    return KnowledgeArtifact.create(
        topic="retrieval",
        artifact_version="1",
        claims=(
            KnowledgeClaim(
                claim_id="claim-1",
                statement="A stable claim.",
                confidence=0.9,
            ),
        ),
        sources=(
            KnowledgeSource(
                source_id="source-1",
                url="https://example.com/research",
                source_type="paper",
                retrieved_at=NOW - timedelta(hours=2),
                source_content_hash="b" * 64,
                prompt_injection_reviewed=True,
            ),
        ),
        valid_until=NOW + timedelta(days=30),
        confidence=0.9,
        quality_score=0.9,
        raw_research_task_id="task-1",
        ingested_at=NOW - timedelta(hours=1),
    )


def _evaluation_records():
    pack = BenchmarkPack.create(
        name="core",
        pack_version="1",
        cases=(BenchmarkCase(case_id="case-1", runner="runner", input_ref="input"),),
        created_at=NOW,
    )
    policy = ScoringPolicy.create(
        policy_version="1",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=NOW,
    )
    spec = ExperimentSpec.create(
        baseline_snapshot_id="self-cognition-" + "c" * 64,
        candidate_commit="candidate",
        candidate_snapshot_id="self-cognition-" + "d" * 64,
        hypothesis="candidate improves",
        target_metrics=({"metric": "correctness", "objective": "increase"},),
        benchmark_pack_id=pack.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=NOW,
    )
    result = ExperimentResult.create(
        experiment_spec_id=spec.experiment_spec_id,
        baseline_metrics=(MetricValue(metric="correctness", value=0.8, unit="ratio"),),
        candidate_metrics=(MetricValue(metric="correctness", value=0.9, unit="ratio"),),
        metric_deltas=(MetricDelta(metric="correctness", delta=0.1),),
        confidence=1.0,
        hard_gate_results=(HardGateResult(gate="tests", passed=True),),
        verdict="promote",
        completed_at=NOW,
    )
    return pack, policy, spec, result


def test_missing_foundation_records_produce_three_shadow_tasks_without_writes(tmp_path: Path):
    root = tmp_path / "evolution-foundation"
    projection = EndogenousFoundationReadOnlyProjection.from_root(root, now=lambda: NOW)

    facts = projection.load()

    assert facts["mode"] == "shadow_read_only"
    assert facts["read_only"] is True
    assert {task["task_kind"] for task in facts["shadow_tasks"]} == {
        "fill_self_cognition",
        "fill_research_knowledge",
        "run_evolution_evaluation",
    }
    assert all(task["execution_allowed"] is False for task in facts["shadow_tasks"])
    assert not root.exists()


def test_available_foundation_records_are_projected_without_shadow_debt(tmp_path: Path):
    root = tmp_path / "evolution-foundation"
    self_repo = JsonSelfCognitionRepository(root / "self-cognition")
    knowledge_repo = JsonKnowledgeRepository(root / "knowledge")
    evaluation_repo = JsonEvaluationRepository(root / "evaluation")
    self_repo.put(_snapshot())
    knowledge_repo.put(_artifact())
    pack, policy, spec, result = _evaluation_records()
    evaluation_repo.put_benchmark_pack(pack)
    evaluation_repo.put_scoring_policy(policy)
    evaluation_repo.put_experiment_spec(spec)
    evaluation_repo.put_experiment_result(result)

    facts = EndogenousFoundationReadOnlyProjection.from_root(root, now=lambda: NOW).load()

    assert facts["known_gaps"] == []
    assert facts["self_cognition"]["status"] == "available"
    assert facts["research_knowledge"]["status"] == "available"
    assert facts["evaluation"]["verdict"] == "promote"
    assert facts["shadow_tasks"] == []
    assert json.loads((root / "self-cognition" / "index.json").read_text(encoding="utf-8"))


def test_stale_or_corrupt_records_are_reported_and_never_written(tmp_path: Path):
    root = tmp_path / "evolution-foundation"
    knowledge_root = root / "knowledge"
    knowledge_root.mkdir(parents=True)
    (knowledge_root / "index.json").write_text("{bad json", encoding="utf-8")

    facts = EndogenousFoundationReadOnlyProjection.from_root(root, now=lambda: NOW).load()

    assert facts["research_knowledge"]["status"] == "error"
    assert any(item.startswith("research_knowledge:read_error:") for item in facts["known_gaps"])
    assert facts["shadow_tasks"]
    assert (knowledge_root / "index.json").read_text(encoding="utf-8") == "{bad json"
