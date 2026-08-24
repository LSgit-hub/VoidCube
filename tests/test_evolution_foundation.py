from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from voidcube.systems.evolution_evaluation import (
    AllowedRegression,
    BenchmarkCase,
    BenchmarkPack,
    EvaluationRecordCorrupted,
    ExperimentResult,
    ExperimentSpec,
    HardGateResult,
    JsonEvaluationRepository,
    MetricDelta,
    MetricTarget,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
    capture_host_environment_manifest,
)
from voidcube.systems.research_knowledge import (
    JsonKnowledgeRepository,
    KnowledgeArtifact,
    KnowledgeClaim,
    KnowledgeRecordCorrupted,
    KnowledgeSource,
)
from voidcube.systems.self_cognition import (
    HealthMetric,
    JsonSelfCognitionRepository,
    SelfCognitionRecordCorrupted,
    SelfCognitionSnapshot,
)


NOW = datetime(2026, 8, 14, 4, 0, tzinfo=timezone.utc)
ENVIRONMENT = capture_host_environment_manifest(
    Path(__file__).parents[1],
    repository_head="c" * 40,
)


def _snapshot() -> SelfCognitionSnapshot:
    return SelfCognitionSnapshot.create(
        body_id="body-a",
        git_commit="commit-a",
        config_digest="a" * 64,
        modules=(),
        capabilities=(),
        health_metrics=(
            HealthMetric(name="tests", value=1.0, unit="ratio", status="healthy"),
        ),
        known_gaps=("coverage",),
        uncovered_areas=(),
        collector_version="collector-1",
        collected_at=NOW,
    )


def _artifact() -> KnowledgeArtifact:
    return KnowledgeArtifact.create(
        topic="retrieval",
        artifact_version="1",
        claims=(
            KnowledgeClaim(
                claim_id="claim-1",
                statement="A claim",
                confidence=0.8,
                applicable_modules=("systems",),
            ),
        ),
        sources=(
            KnowledgeSource(
                source_id="source-1",
                url="https://example.com/research",
                source_type="paper",
                retrieved_at=NOW,
                source_content_hash="b" * 64,
                prompt_injection_reviewed=True,
            ),
        ),
        confidence=0.8,
        quality_score=0.75,
        raw_research_task_id="research-task-1",
        ingested_at=NOW,
    )


def _evaluation_records() -> tuple[BenchmarkPack, ScoringPolicy, ExperimentSpec, ExperimentResult]:
    benchmark = BenchmarkPack.create(
        name="core",
        pack_version="1",
        cases=(
            BenchmarkCase(case_id="case-1", runner="runner", input_ref="input-1"),
        ),
        created_at=NOW,
    )
    policy = ScoringPolicy.create(
        policy_version="1",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        required_validation_platforms=("windows",),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=NOW,
    )
    spec = ExperimentSpec.create(
        baseline_snapshot_id="self-cognition-" + "c" * 64,
        candidate_commit="candidate-commit",
        candidate_snapshot_id="self-cognition-" + "d" * 64,
        hypothesis="The candidate improves correctness.",
        knowledge_ids=("knowledge-" + "e" * 64,),
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
        allowed_regressions=(AllowedRegression(metric="latency", maximum_delta=0.1),),
        benchmark_pack_id=benchmark.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=NOW,
    )
    result = ExperimentResult.create(
        experiment_spec_id=spec.experiment_spec_id,
        baseline_metrics=(MetricValue(metric="correctness", value=0.8, unit="ratio"),),
        candidate_metrics=(MetricValue(metric="correctness", value=0.9, unit="ratio"),),
        metric_deltas=(MetricDelta(metric="correctness", delta=0.1),),
        confidence=0.9,
        hard_gate_results=(HardGateResult(gate="tests", passed=True),),
        execution_environment=ENVIRONMENT,
        verdict="promote",
        completed_at=NOW,
    )
    return benchmark, policy, spec, result


def test_self_cognition_snapshot_is_immutable_and_content_addressed():
    snapshot = _snapshot()

    assert snapshot.snapshot_id == f"self-cognition-{snapshot.content_hash}"
    with pytest.raises(ValidationError):
        snapshot.body_id = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SelfCognitionSnapshot.model_validate(
            {**snapshot.model_dump(mode="json"), "content_hash": "0" * 64}
        )


def test_repositories_are_idempotent_and_reject_corrupt_records(tmp_path: Path):
    snapshot = _snapshot()
    snapshot_repo = JsonSelfCognitionRepository(tmp_path / "self-cognition")
    assert snapshot_repo.put(snapshot) == snapshot
    assert snapshot_repo.put(snapshot) == snapshot
    assert snapshot_repo.get(snapshot.snapshot_id) == snapshot
    assert snapshot_repo.list_ids() == (snapshot.snapshot_id,)

    snapshot_path = (
        tmp_path / "self-cognition" / "snapshots" / f"{snapshot.snapshot_id}.json"
    )
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    payload["body_id"] = "tampered"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SelfCognitionRecordCorrupted):
        snapshot_repo.get(snapshot.snapshot_id)

    artifact = _artifact()
    knowledge_repo = JsonKnowledgeRepository(tmp_path / "knowledge")
    knowledge_repo.put(artifact)
    assert knowledge_repo.get(artifact.knowledge_id) == artifact
    knowledge_index = tmp_path / "knowledge" / "index.json"
    knowledge_index.write_text("[]", encoding="utf-8")
    with pytest.raises(KnowledgeRecordCorrupted):
        knowledge_repo.list_ids()


def test_evaluation_repository_keeps_record_kinds_and_id_references(tmp_path: Path):
    benchmark, policy, spec, result = _evaluation_records()
    repo = JsonEvaluationRepository(tmp_path / "evaluation")

    repo.put_benchmark_pack(benchmark)
    repo.put_scoring_policy(policy)
    repo.put_experiment_spec(spec)
    repo.put_experiment_result(result)

    assert repo.get_benchmark_pack(benchmark.benchmark_pack_id) == benchmark
    assert repo.get_scoring_policy(policy.scoring_policy_id) == policy
    assert repo.get_experiment_spec(spec.experiment_spec_id) == spec
    assert repo.get_experiment_result(result.experiment_result_id) == result
    assert repo.list_ids("benchmark_packs") == (benchmark.benchmark_pack_id,)
    assert repo.list_ids("experiment_results") == (result.experiment_result_id,)
    assert result.experiment_spec_id == spec.experiment_spec_id
    assert spec.benchmark_pack_id == benchmark.benchmark_pack_id
    assert spec.scoring_policy_id == policy.scoring_policy_id

    index_path = tmp_path / "evaluation" / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["schema_version"] = 999
    index_path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(EvaluationRecordCorrupted):
        repo.list_ids("benchmark_packs")


def test_evaluation_hard_gates_and_policy_weights_are_invariant():
    with pytest.raises(ValidationError):
        BenchmarkPack.create(
            schema_version=2,
            name="unsupported",
            pack_version="1",
            cases=(
                BenchmarkCase(case_id="case-1", runner="runner", input_ref="input-1"),
            ),
            created_at=NOW,
        )

    with pytest.raises(ValidationError):
        ScoringPolicy.create(
            policy_version="bad",
            dimensions=(ScoringDimension(name="correctness", weight=0.9),),
            required_hard_gates=("tests",),
            required_validation_platforms=("windows",),
            promote_threshold=0.8,
            observe_threshold=0.5,
            created_at=NOW,
        )

    benchmark, policy, spec, _result = _evaluation_records()
    with pytest.raises(ValidationError):
        ExperimentResult.create(
            experiment_spec_id=spec.experiment_spec_id,
            baseline_metrics=(MetricValue(metric="correctness", value=0.8, unit="ratio"),),
            candidate_metrics=(MetricValue(metric="correctness", value=0.9, unit="ratio"),),
            metric_deltas=(MetricDelta(metric="correctness", delta=0.1),),
            confidence=0.9,
            hard_gate_results=(HardGateResult(gate="tests", passed=False),),
            execution_environment=ENVIRONMENT,
            verdict="promote",
            completed_at=NOW,
        )
    assert benchmark.benchmark_pack_id.startswith("benchmark-pack-")
    assert policy.scoring_policy_id.startswith("scoring-policy-")


def test_foundation_packages_do_not_import_each_other():
    root = Path(__file__).parents[1] / "systems"
    package_names = ("self_cognition", "research_knowledge", "evolution_evaluation")
    module_prefixes = tuple(f"systems.{name}" for name in package_names)
    for package_name in package_names:
        for path in (root / package_name).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            forbidden = [
                name
                for name in imported
                if any(name.startswith(prefix) for prefix in module_prefixes)
                and not name.startswith(f"systems.{package_name}")
            ]
            assert not forbidden, (package_name, path, forbidden)
