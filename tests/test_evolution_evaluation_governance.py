from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from systems.evolution_authoring import (
    AuthoringCommandEvidence,
    EvolutionAuthoringResult,
    JsonEvolutionAuthoringRepository,
)
from systems.evolution_evaluation import (
    EXECUTION_ENVIRONMENT_GATE,
    BenchmarkCase,
    BenchmarkCaseExecutionEvidence,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    ExperimentResult,
    ExperimentSpec,
    HardGateResult,
    JsonEvaluationRepository,
    MetricDelta,
    MetricTarget,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
    SubjectCheckoutEvidence,
    capture_host_environment_manifest,
    select_benchmark_platforms,
)
from systems.research_knowledge import (
    JsonKnowledgeRepository,
    KnowledgeArtifact,
    KnowledgeClaim,
    KnowledgeSource,
)
from systems.self_cognition import JsonSelfCognitionRepository, SelfCognitionSnapshot
from systems.supervisor.evolution_evaluation_governance import (
    EvolutionEvaluationGovernanceVerifier,
    validate_body_improvement_authorization_binding,
)


NOW = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)
BASELINE_COMMIT = "b" * 40
CANDIDATE_COMMIT = "a" * 40
ENVIRONMENT = capture_host_environment_manifest(
    Path(__file__).parents[1],
    repository_head=CANDIDATE_COMMIT,
)
BASELINE_ENVIRONMENT = ENVIRONMENT.__class__.create(
    **{
        **ENVIRONMENT.content_payload(),
        "repository_head": BASELINE_COMMIT,
    }
)


def _records(*, verdict: str = "promote", completed_at: datetime = NOW, gate: str = "tests"):
    baseline = SelfCognitionSnapshot.create(
        body_id="body-baseline",
        git_commit=BASELINE_COMMIT,
        config_digest="1" * 64,
        collector_version="collector-1",
        collected_at=NOW,
    )
    candidate = SelfCognitionSnapshot.create(
        body_id="body-candidate",
        git_commit=CANDIDATE_COMMIT,
        config_digest="2" * 64,
        collector_version="collector-1",
        collected_at=NOW,
    )
    knowledge = KnowledgeArtifact.create(
        topic="stream handling",
        artifact_version="1",
        claims=(
            KnowledgeClaim(
                claim_id="claim-1",
                statement="The candidate improves stream handling.",
                confidence=0.9,
                applicable_modules=("agent",),
            ),
        ),
        sources=(
            KnowledgeSource(
                source_id="source-1",
                url="https://example.test/research",
                source_type="paper",
                retrieved_at=NOW,
                source_content_hash="3" * 64,
                prompt_injection_reviewed=True,
            ),
        ),
        confidence=0.9,
        quality_score=0.9,
        raw_research_task_id="research-1",
        ingested_at=NOW,
    )
    pack = BenchmarkPack.create(
        name="body-core",
        pack_version="1",
        cases=(BenchmarkCase(case_id="case-1", runner="core", input_ref="input"),),
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
    authoring = EvolutionAuthoringResult.create(
        task_id="governance-test",
        status="candidate_created",
        baseline_commit=BASELINE_COMMIT,
        candidate_commit=CANDIDATE_COMMIT,
        candidate_ref="refs/voidcube/candidates/governance-test",
        changed_files=("agent/demo.py",),
        environment_manifest_id=ENVIRONMENT.execution_environment_id,
        environment_identity_id=(
            ENVIRONMENT.identity().execution_environment_identity_id
        ),
        environment_dependency_fingerprint=ENVIRONMENT.dependency_fingerprint,
        command_evidence=(
            AuthoringCommandEvidence(
                command="pytest tests/test_demo.py",
                exit_code=0,
                output="1 passed",
                security_scanner_status="available",
                container_disk_quota_status="unsupported",
            ),
        ),
        agent_summary="Improved correctness",
        started_at=NOW,
        finished_at=NOW,
    )
    spec = ExperimentSpec.create(
        authoring_result_id=authoring.authoring_result_id,
        platform_selection=select_benchmark_platforms(
            authoring.changed_files,
            str(authoring.environment_dependency_fingerprint),
            created_at=NOW,
        ),
        baseline_snapshot_id=baseline.snapshot_id,
        candidate_commit=CANDIDATE_COMMIT,
        candidate_snapshot_id=candidate.snapshot_id,
        hypothesis="Candidate improves correctness.",
        knowledge_ids=(knowledge.knowledge_id,),
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
        benchmark_pack_id=pack.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=NOW,
    )
    environment_identity = ENVIRONMENT.identity()
    baseline_checkout = SubjectCheckoutEvidence.create(
        subject="baseline",
        commit=BASELINE_COMMIT,
        worktree_path=ENVIRONMENT.execution_workspace_path,
        execution_environment_identity_id=(
            environment_identity.execution_environment_identity_id
        ),
        checked_out_at=NOW,
    )
    candidate_checkout = SubjectCheckoutEvidence.create(
        subject="candidate",
        commit=CANDIDATE_COMMIT,
        worktree_path=ENVIRONMENT.execution_workspace_path,
        execution_environment_identity_id=(
            environment_identity.execution_environment_identity_id
        ),
        checked_out_at=NOW,
    )
    result = ExperimentResult.create(
        experiment_spec_id=spec.experiment_spec_id,
        baseline_metrics=(MetricValue(metric="correctness", value=0.8, unit="ratio"),),
        candidate_metrics=(MetricValue(metric="correctness", value=0.9, unit="ratio"),),
        metric_deltas=(MetricDelta(metric="correctness", delta=0.1),),
        confidence=0.9,
        hard_gate_results=(
            HardGateResult(gate=gate, passed=True),
            HardGateResult(
                gate=EXECUTION_ENVIRONMENT_GATE,
                passed=True,
                evidence_refs=(ENVIRONMENT.execution_environment_id,),
            ),
        ),
        execution_environment=ENVIRONMENT,
        verdict=verdict,
        completed_at=completed_at,
        execution_environment_identity=environment_identity,
        subject_checkouts=(baseline_checkout, candidate_checkout),
        benchmark_case_evidence=(
            BenchmarkCaseExecutionEvidence(
                subject="baseline",
                case_id="case-1",
                commands=(
                    BenchmarkCommandEvidence(
                        command="pytest tests/test_demo.py",
                        exit_code=0,
                        output_summary="1 passed",
                    ),
                ),
                execution_environment_id=(
                    BASELINE_ENVIRONMENT.execution_environment_id
                ),
                execution_environment_identity_id=(
                    environment_identity.execution_environment_identity_id
                ),
                subject_checkout_evidence_id=(
                    baseline_checkout.subject_checkout_evidence_id
                ),
            ),
            BenchmarkCaseExecutionEvidence(
                subject="candidate",
                case_id="case-1",
                commands=(
                    BenchmarkCommandEvidence(
                        command="pytest tests/test_demo.py",
                        exit_code=0,
                        output_summary="1 passed",
                    ),
                ),
                execution_environment_id=ENVIRONMENT.execution_environment_id,
                execution_environment_identity_id=(
                    environment_identity.execution_environment_identity_id
                ),
                subject_checkout_evidence_id=(
                    candidate_checkout.subject_checkout_evidence_id
                ),
            ),
        ),
    )
    return baseline, candidate, knowledge, pack, policy, authoring, spec, result


def _seed(root: Path, *, omit: str | None = None, **record_options):
    records = _records(**record_options)
    baseline, candidate, knowledge, pack, policy, authoring, spec, result = records
    cognition_repo = JsonSelfCognitionRepository(root / "self-cognition")
    knowledge_repo = JsonKnowledgeRepository(root / "knowledge")
    evaluation_repo = JsonEvaluationRepository(root / "evaluation")
    authoring_repo = JsonEvolutionAuthoringRepository(root / "authoring")
    if omit != "baseline_snapshot":
        cognition_repo.put(baseline)
    if omit != "candidate_snapshot":
        cognition_repo.put(candidate)
    if omit != "knowledge_artifact":
        knowledge_repo.put(knowledge)
    if omit != "benchmark_pack":
        evaluation_repo.put_benchmark_pack(pack)
    if omit != "scoring_policy":
        evaluation_repo.put_scoring_policy(policy)
    if omit != "authoring_result":
        authoring_repo.put(authoring)
    if omit != "experiment_spec":
        evaluation_repo.put_experiment_spec(spec)
    evaluation_repo.put_experiment_result(result)
    return records


def test_promote_result_with_complete_references_authorizes_exact_commit(tmp_path: Path):
    records = _seed(tmp_path)
    result = records[-1]

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        result.experiment_result_id
    )

    assert authorization["authorized"] is True
    assert authorization["evaluated_candidate_commit"] == CANDIDATE_COMMIT
    assert authorization["evaluated_baseline_commit"] == BASELINE_COMMIT
    assert authorization["experiment_spec_id"] == records[-2].experiment_spec_id


@pytest.mark.parametrize(
    ("omit", "reason"),
    [
        ("experiment_spec", "experiment_spec_not_found"),
        ("authoring_result", "authoring_result_not_found"),
        ("benchmark_pack", "benchmark_pack_not_found"),
        ("scoring_policy", "scoring_policy_not_found"),
        ("baseline_snapshot", "baseline_snapshot_not_found"),
        ("candidate_snapshot", "candidate_snapshot_not_found"),
        ("knowledge_artifact", "knowledge_artifact_not_found"),
    ],
)
def test_missing_referenced_record_rejects_authorization(tmp_path: Path, omit: str, reason: str):
    result = _seed(tmp_path, omit=omit)[-1]

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        result.experiment_result_id
    )

    assert authorization["authorized"] is False
    assert authorization["reason"] == reason


@pytest.mark.parametrize("verdict", ["observe", "reject"])
def test_non_promote_result_is_not_authorized(tmp_path: Path, verdict: str):
    result = _seed(tmp_path, verdict=verdict)[-1]

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        result.experiment_result_id
    )

    assert authorization["authorized"] is False
    assert authorization["reason"] == "experiment_verdict_not_promote"


def test_required_hard_gate_must_be_present(tmp_path: Path):
    result = _seed(tmp_path, gate="lint")[-1]

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        result.experiment_result_id
    )

    assert authorization["authorized"] is False
    assert authorization["reason"] == "required_hard_gates_missing"


def test_promote_result_without_authoring_provenance_is_rejected(tmp_path: Path):
    baseline, candidate, knowledge, pack, policy, authoring, spec, result = _records()
    legacy_spec = ExperimentSpec.create(
        baseline_snapshot_id=baseline.snapshot_id,
        candidate_commit=CANDIDATE_COMMIT,
        candidate_snapshot_id=candidate.snapshot_id,
        hypothesis=spec.hypothesis,
        knowledge_ids=spec.knowledge_ids,
        target_metrics=spec.target_metrics,
        benchmark_pack_id=pack.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=NOW,
    )
    legacy_result_payload = result.content_payload()
    legacy_result_payload["experiment_spec_id"] = legacy_spec.experiment_spec_id
    legacy_result = ExperimentResult.create(**legacy_result_payload)
    _seed(tmp_path)
    repository = JsonEvaluationRepository(tmp_path / "evaluation")
    repository.put_experiment_spec(legacy_spec)
    repository.put_experiment_result(legacy_result)

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        legacy_result.experiment_result_id
    )

    assert authorization["authorized"] is False
    assert authorization["reason"] == "authoring_result_missing"


@pytest.mark.parametrize(
    ("evidence_update", "reason"),
    [
        (None, "benchmark_command_evidence_missing"),
        ("failed", "benchmark_command_failed"),
    ],
)
def test_promote_result_requires_successful_command_evidence(
    tmp_path: Path,
    evidence_update: str | None,
    reason: str,
):
    result = _seed(tmp_path)[-1]
    payload = result.content_payload()
    if evidence_update is None:
        payload["benchmark_case_evidence"] = None
    else:
        payload["benchmark_case_evidence"][0]["commands"][0]["exit_code"] = 1
    modified = ExperimentResult.create(**payload)
    repository = JsonEvaluationRepository(tmp_path / "evaluation")
    repository.put_experiment_result(modified)

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        modified.experiment_result_id
    )

    assert authorization["authorized"] is False
    assert authorization["reason"] == reason


def test_latest_authorization_can_use_older_promote_when_latest_is_observe(tmp_path: Path):
    _seed(tmp_path, completed_at=NOW)
    baseline, candidate, knowledge, pack, policy, authoring, spec, observe = _records(
        verdict="observe",
        completed_at=NOW + timedelta(minutes=1),
    )
    repo = JsonEvaluationRepository(tmp_path / "evaluation")
    repo.put_experiment_result(observe)

    authorization = EvolutionEvaluationGovernanceVerifier.from_root(
        tmp_path
    ).latest_authorization()

    assert authorization["authorized"] is True
    assert authorization["experiment_result_id"] != observe.experiment_result_id


def test_corrupted_result_and_forged_id_are_rejected(tmp_path: Path):
    result = _seed(tmp_path)[-1]
    result_path = (
        tmp_path
        / "evaluation"
        / "experiments"
        / "results"
        / f"{result.experiment_result_id}.json"
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["confidence"] = 0.1
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    verifier = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path)

    corrupted = verifier.verify(result.experiment_result_id)
    forged = verifier.verify("experiment-result-" + "f" * 64)

    assert corrupted["reason"] == "experiment_result_unreadable"
    assert forged["reason"] == "experiment_result_not_found"


def test_binding_rejects_actual_commit_mismatch(tmp_path: Path):
    result = _seed(tmp_path)[-1]
    authorization = EvolutionEvaluationGovernanceVerifier.from_root(tmp_path).verify(
        result.experiment_result_id
    )
    fields = {
        key: authorization[key]
        for key in (
            "experiment_result_id",
            "experiment_spec_id",
            "authoring_result_id",
            "evaluated_baseline_commit",
            "evaluated_candidate_commit",
            "candidate_ref",
            "changed_files",
            "baseline_snapshot_id",
            "candidate_snapshot_id",
            "benchmark_pack_id",
            "scoring_policy_id",
            "execution_environment_id",
            "authoring_environment_manifest_id",
            "authoring_environment_identity_id",
            "authoring_dependency_fingerprint",
            "authoring_security_scanner_statuses",
            "authoring_container_disk_quota_statuses",
            "environment_capability_warnings",
            "platform_selection_id",
            "selected_validation_platforms",
            "validation_scope",
            "validated_platforms",
            "knowledge_ids",
        )
    }

    binding = validate_body_improvement_authorization_binding(
        evidence=fields,
        constraints={
            **fields,
            "must_match_evaluated_commit": True,
            "requires_governor_review": True,
            "requires_user_consent": True,
        },
        authorization=authorization,
        actual_commit="c" * 40,
        actual_baseline_commit=BASELINE_COMMIT,
    )

    assert binding == {
        "valid": False,
        "reason": "evaluated_candidate_commit_mismatch",
    }
