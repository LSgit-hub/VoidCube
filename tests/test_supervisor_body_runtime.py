from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voidcube.systems.governor import GovernorRequest
from voidcube.systems.evolution_authoring import (
    AuthoringCommandEvidence,
    EvolutionAuthoringResult,
)
from voidcube.systems.evolution_evaluation import (
    EXECUTION_ENVIRONMENT_GATE,
    BenchmarkCase,
    BenchmarkCaseExecutionEvidence,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    ExperimentResult,
    ExperimentSpec,
    HardGateResult,
    MetricDelta,
    MetricTarget,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
    SubjectCheckoutEvidence,
    ExecutionEnvironmentManifest,
    capture_host_environment_manifest,
    select_benchmark_platforms,
)
from voidcube.systems.research_knowledge import KnowledgeArtifact, KnowledgeClaim, KnowledgeSource
from voidcube.systems.self_cognition import SelfCognitionSnapshot
from voidcube.systems.supervisor.supervisor import (
    AgentInstance,
    Supervisor,
    SupervisorConfig,
    SupervisorExecutionConfig,
    SupervisorServiceRuntimeConfig,
)


_HOST_ENVIRONMENT = capture_host_environment_manifest(
    Path(__file__).parents[1],
    repository_head="a" * 40,
)
_BODY_ENVIRONMENT_PAYLOAD = _HOST_ENVIRONMENT.content_payload()
_BODY_ENVIRONMENT_PAYLOAD.update(
    backend="podman",
    validation_scope="container",
    execution_os="Linux 6.8",
    architecture="x86_64",
    execution_workspace_path="/workspace",
    path_mappings=(
        {
            "host_path": _HOST_ENVIRONMENT.host_workspace_path,
            "execution_path": "/workspace",
        },
    ),
    repository_head="b" * 40,
    validated_platforms=("linux",),
)
_BODY_ENVIRONMENT = ExecutionEnvironmentManifest.create(
    **_BODY_ENVIRONMENT_PAYLOAD
)


def _make_supervisor_config(tmp_path: Path) -> SupervisorConfig:
    config = SupervisorConfig(
        execution=SupervisorExecutionConfig(git_repo_path=str(tmp_path)),
        soul_store_path=str(tmp_path / ".soul-runtime"),
        service_runtime=SupervisorServiceRuntimeConfig(
            governor_llm_advisory_enabled=False,
            endogenous_drive_lm_task_generation_enabled=False,
        ),
    )
    config.body_runtime.state_root = str(tmp_path / "body-state")
    return config


def _seed_probe_ready_files(tmp_path: Path) -> None:
    cli_dir = tmp_path / "src" / "voidcube" / "interfaces" / "cli"
    cli_dir.mkdir(parents=True, exist_ok=True)
    (cli_dir / "root_launcher.py").write_text(
        "print('agent entrypoint')\n", encoding="utf-8"
    )
    (tmp_path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    tools_dir = tmp_path / "src" / "voidcube" / "extensions" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "__init__.py").write_text("", encoding="utf-8")
    (tools_dir / "model_tools.py").write_text("# probe smoke\n", encoding="utf-8")


def _make_probe_ready_supervisor(tmp_path: Path) -> Supervisor:
    _seed_probe_ready_files(tmp_path)
    return Supervisor(_make_supervisor_config(tmp_path))


def _seed_probe_ready_git_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "voidcube@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "VoidCube Test"], cwd=tmp_path, check=True)
    _seed_probe_ready_files(tmp_path)
    agent_dir = tmp_path / "src" / "voidcube" / "runtime" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "stream_handler.py").write_text(
        "VERSION = 'stable'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "stable body"], cwd=tmp_path, check=True, capture_output=True, text=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _create_running_body_improvement_task(
    supervisor: Supervisor,
    *,
    target_paths: list[str],
    learning_refs: list[dict] | None = None,
    baseline_commit: str | None = None,
    candidate_commit: str | None = None,
):
    authorization = _seed_body_evaluation_authorization(
        supervisor,
        changed_files=tuple(target_paths),
        baseline_commit=baseline_commit,
        candidate_commit=candidate_commit,
    )
    authorization_fields = {
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
            "knowledge_ids",
            "execution_environment_id",
            "execution_environment_ids",
            "execution_environment_identity_ids",
            "authoring_environment_manifest_id",
            "authoring_environment_identity_id",
            "authoring_dependency_fingerprint",
            "authoring_security_scanner_statuses",
            "authoring_container_disk_quota_statuses",
            "environment_capability_warnings",
            "validation_security_scanner_statuses",
            "validation_container_disk_quota_statuses",
            "validation_environment_capability_warnings",
            "capability_policy_id",
            "capability_policy_version",
            "capability_policy_profile",
            "environment_capability_policy_violations",
            "platform_selection_id",
            "selected_validation_platforms",
            "platform_selection_reason_codes",
            "validation_scope",
            "validated_platforms",
        )
    }
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    task = supervisor._autonomous_chain_store.create_task(
        title="Governed body improvement",
        summary="Apply mapped learning evidence to approved targets.",
        task_type="self_evolution",
        source="endogenous_drive",
        metadata={
            "governance_task_type": "self_evolution",
            "task_family": "body_upgrade",
            "execution_kind": "body_improvement",
        },
        evidence={
            "learning_quality_score": 90.0,
            **authorization_fields,
            "learning_refs": learning_refs
            or [
                {
                    "mem_id": "learning-1",
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "relevance": 1.0,
                }
            ],
        },
        constraints={
            "target_slot_id": "slot-B",
            "worktree_path": slot_meta.worktree_path,
            "target_paths": target_paths,
            "max_files_changed": 5,
            "must_match_evaluated_commit": True,
            "requires_governor_review": True,
            "requires_user_consent": True,
            **authorization_fields,
        },
    )
    supervisor._autonomous_chain_store.update_status(
        task.task_id,
        status="approved",
        actor="test",
        reason="approved for API-A",
    )
    return supervisor._autonomous_chain_store.claim_execution(
        task.task_id,
        owner_session_id="body-improvement-test-session",
        actor="cli_agent",
        reason="claimed by API-A",
    )


def _seed_body_evaluation_authorization(
    supervisor: Supervisor,
    *,
    changed_files: tuple[str, ...] = ("src/voidcube/runtime/agent/stream_handler.py",),
    baseline_commit: str | None = None,
    candidate_commit: str | None = None,
) -> dict:
    now = datetime(2026, 8, 14, 6, 0, tzinfo=timezone.utc)
    baseline_commit = baseline_commit or "b" * 40
    candidate_commit = candidate_commit or "a" * 40
    baseline = SelfCognitionSnapshot.create(
        body_id="body-baseline",
        git_commit=baseline_commit,
        config_digest="1" * 64,
        collector_version="collector-1",
        collected_at=now,
    )
    candidate = SelfCognitionSnapshot.create(
        body_id="body-candidate",
        git_commit=candidate_commit,
        config_digest="2" * 64,
        collector_version="collector-1",
        collected_at=now,
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
                retrieved_at=now,
                source_content_hash="3" * 64,
                prompt_injection_reviewed=True,
            ),
        ),
        confidence=0.9,
        quality_score=0.9,
        raw_research_task_id="research-1",
        ingested_at=now,
    )
    pack = BenchmarkPack.create(
        name="body-core",
        pack_version="1",
        cases=(BenchmarkCase(case_id="case-1", runner="core", input_ref="input"),),
        created_at=now,
    )
    policy = ScoringPolicy.create(
        policy_version="1",
        dimensions=(ScoringDimension(name="correctness", weight=1.0),),
        required_hard_gates=("tests",),
        required_validation_platforms=("windows",),
        promote_threshold=0.8,
        observe_threshold=0.5,
        created_at=now,
    )
    authoring = EvolutionAuthoringResult.create(
        task_id="body-runtime-test",
        status="candidate_created",
        baseline_commit=baseline_commit,
        candidate_commit=candidate_commit,
        candidate_ref="refs/voidcube/candidates/body-runtime-test",
        changed_files=changed_files,
        environment_manifest_id=_HOST_ENVIRONMENT.execution_environment_id,
        environment_identity_id=(
            _HOST_ENVIRONMENT.identity().execution_environment_identity_id
        ),
        environment_dependency_fingerprint=_HOST_ENVIRONMENT.dependency_fingerprint,
        command_evidence=(
            AuthoringCommandEvidence(
                command="pytest tests/test_stream_handler.py",
                exit_code=0,
                output="1 passed",
                security_scanner_status="available",
                container_disk_quota_status="not_applicable",
            ),
        ),
        agent_summary="Improved stream handling",
        started_at=now,
        finished_at=now,
    )
    spec = ExperimentSpec.create(
        authoring_result_id=authoring.authoring_result_id,
        platform_selection=select_benchmark_platforms(
            authoring.changed_files,
            str(authoring.environment_dependency_fingerprint),
            created_at=now,
        ),
        baseline_snapshot_id=baseline.snapshot_id,
        candidate_commit=candidate_commit,
        candidate_snapshot_id=candidate.snapshot_id,
        hypothesis="Candidate improves correctness.",
        knowledge_ids=(knowledge.knowledge_id,),
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
        benchmark_pack_id=pack.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=now,
    )
    environment_identity = _HOST_ENVIRONMENT.identity()
    baseline_environment = ExecutionEnvironmentManifest.create(
        **{
            **_HOST_ENVIRONMENT.content_payload(),
            "repository_head": baseline_commit,
        }
    )
    baseline_checkout = SubjectCheckoutEvidence.create(
        subject="baseline",
        commit=baseline_commit,
        worktree_path=_HOST_ENVIRONMENT.execution_workspace_path,
        execution_environment_identity_id=(
            environment_identity.execution_environment_identity_id
        ),
        checked_out_at=now,
    )
    candidate_checkout = SubjectCheckoutEvidence.create(
        subject="candidate",
        commit=candidate_commit,
        worktree_path=_HOST_ENVIRONMENT.execution_workspace_path,
        execution_environment_identity_id=(
            environment_identity.execution_environment_identity_id
        ),
        checked_out_at=now,
    )
    candidate_environment = ExecutionEnvironmentManifest.create(
        **{
            **_HOST_ENVIRONMENT.content_payload(),
            "repository_head": candidate_commit,
        }
    )
    result = ExperimentResult.create(
        experiment_spec_id=spec.experiment_spec_id,
        baseline_metrics=(MetricValue(metric="correctness", value=0.8, unit="ratio"),),
        candidate_metrics=(MetricValue(metric="correctness", value=0.9, unit="ratio"),),
        metric_deltas=(MetricDelta(metric="correctness", delta=0.1),),
        confidence=0.9,
        hard_gate_results=(
            HardGateResult(gate="tests", passed=True),
            HardGateResult(
                gate=EXECUTION_ENVIRONMENT_GATE,
                passed=True,
                evidence_refs=(candidate_environment.execution_environment_id,),
            ),
        ),
        execution_environment=candidate_environment,
        verdict="promote",
        completed_at=now,
        execution_environment_identity=environment_identity,
        execution_environments=(baseline_environment, candidate_environment),
        execution_environment_identities=(environment_identity,),
        subject_checkouts=(baseline_checkout, candidate_checkout),
        benchmark_case_evidence=(
            BenchmarkCaseExecutionEvidence(
                subject="baseline",
                case_id="case-1",
                commands=(
                    BenchmarkCommandEvidence(
                        command="pytest tests/test_stream_handler.py",
                        exit_code=0,
                        output_summary="1 passed",
                        security_scanner_status="available",
                        container_disk_quota_status="not_applicable",
                    ),
                ),
                execution_environment_id=baseline_environment.execution_environment_id,
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
                        command="pytest tests/test_stream_handler.py",
                        exit_code=0,
                        output_summary="1 passed",
                        security_scanner_status="available",
                        container_disk_quota_status="not_applicable",
                    ),
                ),
                execution_environment_id=candidate_environment.execution_environment_id,
                execution_environment_identity_id=(
                    environment_identity.execution_environment_identity_id
                ),
                subject_checkout_evidence_id=(
                    candidate_checkout.subject_checkout_evidence_id
                ),
            ),
        ),
    )
    verifier = supervisor._evolution_evaluation_governance_verifier
    verifier.self_cognition_repository.put(baseline)
    verifier.self_cognition_repository.put(candidate)
    verifier.knowledge_repository.put(knowledge)
    verifier.evaluation_repository.put_benchmark_pack(pack)
    verifier.evaluation_repository.put_scoring_policy(policy)
    verifier.authoring_repository.put(authoring)
    verifier.evaluation_repository.put_experiment_spec(spec)
    verifier.evaluation_repository.put_experiment_result(result)
    return verifier.verify(result.experiment_result_id)


def _body_improvement_execution_context(task) -> dict:
    return {
        "session_id": task.execution_lease.owner_session_id,
        "execution_lease": task.execution_lease.model_dump(mode="json"),
        "execution_environment": _BODY_ENVIRONMENT.model_dump(mode="json"),
    }


async def _mark_body_candidate(supervisor: Supervisor, slot_id: str, request: dict | None = None):
    return await supervisor._execution_facade.mark_body_candidate(slot_id, request)


async def _prepare_body_slot(supervisor: Supervisor, slot_id: str, request: dict | None = None):
    return await supervisor._execution_facade.prepare_body_slot(slot_id, request)


async def _record_body_probe_report(supervisor: Supervisor, request: dict):
    return await supervisor._execution_facade.record_body_probe_report(request)


async def _run_body_probe(supervisor: Supervisor, request: dict):
    return await supervisor._execution_facade.run_body_probe(request)


async def _evaluate_watch_window(supervisor: Supervisor, request: dict | None = None):
    return await supervisor._execution_facade.evaluate_watch_window(request)


async def _get_watch_window_status(supervisor: Supervisor):
    return supervisor._execution_facade.get_watch_window_status()


async def _execute_body_upgrade(supervisor: Supervisor, request: dict | None = None):
    return await supervisor._execution_facade.execute_body_upgrade(request)


async def _confirm_body_switch(supervisor: Supervisor, request: dict | None = None):
    return await supervisor.confirm_body_switch(request)


async def _execute_governor_review_request(supervisor: Supervisor, request: dict):
    return supervisor._governor_review_executor.execute_governor_request(
        GovernorRequest.model_validate(request)
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_exposes_initialized_body_registry(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))

    result = await supervisor.get_body_registry()

    assert result["registry"]["active_slot"] == "slot-A"
    assert result["registry"]["shell_slot"] == "slot-B"
    assert "slot-A" in result["slots"]
    assert result["slots"]["slot-A"]["body_state"] == "active"
    assert result["integrity"]["healthy"] is True
    assert result["integrity"]["violations"] == []

    active_target = await supervisor.get_active_body_target()
    assert active_target["slot_id"] == "slot-A"


@pytest.mark.unit
def test_failed_probe_report_contributes_no_shell_health_score(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    slot_meta.last_probe_result = {
        "overall_passed": False,
        "checks": [
            {"name": "startup_ok", "passed": True},
            {"name": "config_load_ok", "passed": True},
            {"name": "tool_smoke_ok", "passed": False},
        ],
    }

    assert supervisor._body_improvement_review_service._get_probe_score("slot-B", slot_meta) == 0.0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_improvement_report_verifies_commit_and_executes_switch_suggestion(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    governed_task = _create_running_body_improvement_task(
        supervisor,
        target_paths=["src/voidcube/runtime/agent/stream_handler.py"],
    )
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    slot_meta.health_score = 55.0
    supervisor._body_registry.save_slot_meta(slot_meta)
    supervisor._body_improvement_review_service._inspect_body_improvement_commit = Mock(
        return_value={
            "ok": True,
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "diff_text": "src/voidcube/runtime/agent/stream_handler.py | 2 +-",
        }
    )
    supervisor._body_improvement_review_service._llm_review_diff = AsyncMock(return_value=20.0)

    result = await supervisor.receive_improvement_report(
        {
            **_body_improvement_execution_context(governed_task),
            "slot_id": "slot-B",
            "task_id": governed_task.task_id,
            "baseline_commit": "b" * 40,
            "commit_hash": "a" * 40,
            "diff_summary": "Improve stream handling",
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "learning_refs": [
                {
                    "mem_id": "learning-1",
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "relevance": 1.0,
                }
            ],
            "improvement_description": "Apply the verified learning result.",
        }
    )

    assert result["status"] == "reviewed"
    assert result["score_delta"] > 0
    assert result["evolution_boundary"]["ok"] is True
    assert result["switch_suggestion"]["governor_response"]["decision"] == "approve"
    updated = supervisor._body_registry.load_slot_meta("slot-B")
    assert updated.body_state == "probe"
    assert updated.improvement_count == 1
    assert updated.current_healthy_commit == "a" * 40
    assert updated.previous_healthy_commit == "b" * 40
    assert updated.candidate_commit == "a" * 40
    assert updated.health_history[-1]["baseline_commit"] == "b" * 40
    assert updated.health_history[-1]["changed_files"] == ["src/voidcube/runtime/agent/stream_handler.py"]

    duplicate = await supervisor.receive_improvement_report(
        {
            **_body_improvement_execution_context(governed_task),
            "slot_id": "slot-B",
            "task_id": governed_task.task_id,
            "baseline_commit": "b" * 40,
            "commit_hash": "a" * 40,
            "diff_summary": "Retry after a lost response",
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "improvement_description": "Must not score twice.",
        }
    )
    assert duplicate["duplicate"] is True
    assert duplicate["score_delta"] == 0
    after_duplicate = supervisor._body_registry.load_slot_meta("slot-B")
    assert after_duplicate.health_score == updated.health_score
    assert after_duplicate.improvement_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_employee_dispatch_reconcile_runs_real_body_review_with_fixed_provider(tmp_path):
    baseline_commit = _seed_probe_ready_git_repo(tmp_path)
    runner = tmp_path / "src" / "voidcube" / "runtime" / "agent" / "stream_handler.py"
    runner.write_text("VERSION = 'candidate'\n", encoding="utf-8")
    subprocess.run(["git", "add", str(runner.relative_to(tmp_path))], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "evaluated body candidate"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    candidate_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "reset", "--hard", baseline_commit],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    supervisor._body_registry.materialize_candidate_commit(
        "slot-B",
        baseline_commit=baseline_commit,
        candidate_commit=candidate_commit,
        changed_files=["src/voidcube/runtime/agent/stream_handler.py"],
        source_label="evaluated:deterministic-employee-review",
    )
    task = _create_running_body_improvement_task(
        supervisor,
        target_paths=["src/voidcube/runtime/agent/stream_handler.py"],
        baseline_commit=baseline_commit,
        candidate_commit=candidate_commit,
    )
    supervisor._body_improvement_review_service._llm_review_diff = AsyncMock(
        return_value=20.0
    )

    dispatch = supervisor._autonomous_employee_dispatch_service.dispatch(task)
    schedule = supervisor._scheduled_task_store.get(dispatch["employee_task_id"])
    claimed = supervisor._scheduled_task_store.claim_due(
        owner_session_id="deterministic-employee",
        role_limits={"coding": 1},
    )
    assert claimed is not None
    lease = task.execution_lease
    result_summary = json.dumps(
        {
            "body_improvement_report": {
                "task_id": task.task_id,
                "lease_generation": lease.generation,
                "attempt_id": lease.attempt_id,
                "slot_id": "slot-B",
                "baseline_commit": baseline_commit,
                "commit_hash": candidate_commit,
                "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
                "execution_environment": _BODY_ENVIRONMENT.model_dump(mode="json"),
                "verification": {"passed": True, "checks": ["deterministic pytest"]},
                "learning_refs": [{
                    "mem_id": "learning-1",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "relevance": 1.0,
                }],
                "improvement_description": "Apply the verified stream handling improvement.",
            }
        }
    )
    supervisor._scheduled_task_store.finish_run(
        claimed["run"]["run_id"],
        owner_session_id="deterministic-employee",
        success=True,
        result_summary=result_summary,
    )

    updates = await supervisor._autonomous_employee_dispatch_service.reconcile()

    assert updates == [{"task_id": task.task_id, "status": "completed"}]
    completed = supervisor._autonomous_chain_store.get_task(task.task_id)
    assert completed is not None
    assert completed.status == "completed"
    updated_slot = supervisor._body_registry.load_slot_meta("slot-B")
    assert updated_slot.improvement_count == 1
    assert updated_slot.health_score > 0
    assert updated_slot.health_history[-1]["reason"] == "body_improvement"
    assert updated_slot.health_history[-1]["commit_hash"] == candidate_commit
    supervisor._body_improvement_review_service._llm_review_diff.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.operational
async def test_body_improvement_rollback_restores_commit_probes_and_writes_governance(tmp_path):
    stable_commit = _seed_probe_ready_git_repo(tmp_path)
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    prepared = await _prepare_body_slot(supervisor, "slot-B")
    worktree = Path(prepared["slot"]["worktree_path"])
    (worktree / "src" / "voidcube" / "runtime" / "agent" / "stream_handler.py").write_text(
        "VERSION = 'broken'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "src/voidcube/runtime/agent/stream_handler.py"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "commit", "-m", "breaking body improvement"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    )
    broken_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    slot_meta.health_score = 80.0
    slot_meta.current_healthy_commit = broken_commit
    slot_meta.previous_healthy_commit = stable_commit
    slot_meta.candidate_commit = broken_commit
    slot_meta.build_from_commit = broken_commit
    slot_meta.changed_files = ["src/voidcube/runtime/agent/stream_handler.py"]
    supervisor._body_registry.save_slot_meta(slot_meta)

    result = await supervisor.rollback_body_improvement(
        "slot-B",
        {
            "regression_detected": True,
            "failure_reason": "stream handler regression",
            "request_id": "rollback-body-improvement-1",
            "trace_id": "trace-body-improvement-rollback-1",
        },
    )

    assert result["status"] == "body_improvement_rollback_verified"
    assert result["governor_response"]["decision"] == "rollback_required"
    assert result["probe"]["report"]["overall_passed"] is True
    assert result["execution_report"]["action_results"][-1]["action_type"] == (
        "verify_healthy_commit_rollback"
    )
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == stable_commit
    restored = supervisor._body_registry.load_slot_meta("slot-B")
    assert restored.body_state == "shell"
    assert restored.current_healthy_commit == stable_commit
    assert restored.previous_healthy_commit is None
    assert restored.health_score == pytest.approx(56.0)
    assert restored.last_improvement_rollback["source_commit"] == broken_commit
    assert restored.last_improvement_rollback["target_commit"] == stable_commit

    latest_governance = supervisor._governor.get_latest()
    assert latest_governance["kind"] == "execution_outcome"
    assert latest_governance["request"]["event_type"] == "improvement_rollback_request"
    assert latest_governance["execution_report"]["action_results"][-1]["details"][
        "probe_passed"
    ] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_improvement_report_rejects_unverifiable_commit_without_mutation(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    governed_task = _create_running_body_improvement_task(
        supervisor,
        target_paths=["src/voidcube/runtime/agent/stream_handler.py"],
    )

    result = await supervisor.receive_improvement_report(
        {
            **_body_improvement_execution_context(governed_task),
            "slot_id": "slot-B",
            "task_id": governed_task.task_id,
            "baseline_commit": "b" * 40,
            "commit_hash": "not-a-commit",
            "diff_summary": "Unverifiable change",
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "improvement_description": "Must be rejected.",
        }
    )

    assert result == {
        "status": "reviewed",
        "score_delta": 0,
        "reject_reason": "invalid_commit_hash",
    }
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    assert slot_meta.health_score == 0
    assert slot_meta.improvement_count == 0
    assert slot_meta.health_history == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_improvement_report_rejects_unknown_governance_task(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))

    with pytest.raises(HTTPException) as exc_info:
        await supervisor.receive_improvement_report(
            {
                "slot_id": "slot-B",
                "task_id": "unknown-body-improvement",
                "session_id": "unknown-session",
                "execution_lease": {
                    "generation": 1,
                    "attempt_id": "unknown-attempt",
                },
                "baseline_commit": "b" * 40,
                "commit_hash": "a" * 40,
                "diff_summary": "Ungoverned change",
                "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
                "improvement_description": "Must not affect health.",
            }
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "stale_execution_lease"
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    assert slot_meta.health_score == 0
    assert slot_meta.health_history == []


@pytest.mark.unit
def test_body_improvement_commit_inspection_uses_verified_baseline_to_head_diff(tmp_path):
    repo = tmp_path / "body-worktree"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    git("init")
    git("config", "user.email", "voidcube-test@example.invalid")
    git("config", "user.name", "VoidCube Test")
    agent_dir = repo / "src" / "voidcube" / "runtime" / "agent"
    agent_dir.mkdir(parents=True)
    stream_handler = agent_dir / "stream_handler.py"
    stream_handler.write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "src/voidcube/runtime/agent/stream_handler.py")
    git("commit", "-m", "baseline")
    baseline_commit = git("rev-parse", "HEAD")

    stream_handler.write_text("VALUE = 2\n", encoding="utf-8")
    git("add", "src/voidcube/runtime/agent/stream_handler.py")
    git("commit", "-m", "improvement")
    improvement_commit = git("rev-parse", "HEAD")

    supervisor_root = tmp_path / "supervisor-runtime"
    supervisor_root.mkdir()
    supervisor = Supervisor(_make_supervisor_config(supervisor_root))
    inspection = supervisor._body_improvement_review_service._inspect_body_improvement_commit(
        worktree_path=str(repo),
        baseline_commit=baseline_commit,
        commit_hash=improvement_commit,
    )

    assert inspection["ok"] is True
    assert inspection["changed_files"] == ["src/voidcube/runtime/agent/stream_handler.py"]
    assert "src/voidcube/runtime/agent/stream_handler.py" in inspection["diff_text"]

    uncommitted = agent_dir / "uncommitted.py"
    uncommitted.write_text("VALUE = 3\n", encoding="utf-8")
    dirty = supervisor._body_improvement_review_service._inspect_body_improvement_commit(
        worktree_path=str(repo),
        baseline_commit=baseline_commit,
        commit_hash=improvement_commit,
    )
    assert dirty == {"ok": False, "reject_reason": "worktree_not_clean"}
    uncommitted.unlink()

    stale = supervisor._body_improvement_review_service._inspect_body_improvement_commit(
        worktree_path=str(repo),
        baseline_commit=baseline_commit,
        commit_hash=baseline_commit,
    )
    assert stale == {"ok": False, "reject_reason": "commit_is_not_worktree_head"}


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("actual_files", "declared_files", "approved_targets", "reject_reason"),
    [
        (
            ["src/voidcube/runtime/agent/stream_handler.py"],
            ["src/voidcube/runtime/agent/other.py"],
            ["src/voidcube/runtime/agent/other.py"],
            "changed_files_mismatch",
        ),
        (
            ["src/voidcube/systems/supervisor/planning_runtime.py"],
            ["src/voidcube/systems/supervisor/planning_runtime.py"],
            ["src/voidcube/systems/supervisor/planning_runtime.py"],
            "evolution_boundary_violation",
        ),
        (
            ["src/voidcube/runtime/agent/stream_handler.py"],
            ["src/voidcube/runtime/agent/stream_handler.py"],
            ["src/voidcube/runtime/agent/display.py"],
            "changed_files_outside_governed_targets",
        ),
    ],
)
async def test_body_improvement_report_rejects_untrusted_file_scope(
    tmp_path,
    actual_files,
    declared_files,
    approved_targets,
    reject_reason,
):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    governed_task = _create_running_body_improvement_task(
        supervisor,
        target_paths=approved_targets,
    )
    supervisor._body_improvement_review_service._inspect_body_improvement_commit = Mock(
        return_value={
            "ok": True,
            "changed_files": actual_files,
            "diff_text": "verified diff",
        }
    )

    result = await supervisor.receive_improvement_report(
        {
            **_body_improvement_execution_context(governed_task),
            "slot_id": "slot-B",
            "task_id": governed_task.task_id,
            "baseline_commit": "b" * 40,
            "commit_hash": "a" * 40,
            "diff_summary": "Untrusted scope",
            "changed_files": declared_files,
            "improvement_description": "Must be rejected before scoring.",
        }
    )

    assert result["score_delta"] == 0
    assert result["reject_reason"] == reject_reason
    slot_meta = supervisor._body_registry.load_slot_meta("slot-B")
    assert slot_meta.improvement_count == 0
    assert slot_meta.health_history == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_improvement_report_rejects_commit_not_named_by_experiment_result(
    tmp_path,
):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    governed_task = _create_running_body_improvement_task(
        supervisor,
        target_paths=["src/voidcube/runtime/agent/stream_handler.py"],
    )
    supervisor._body_improvement_review_service._inspect_body_improvement_commit = Mock(
        return_value={
            "ok": True,
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "diff_text": "verified diff",
        }
    )

    result = await supervisor.receive_improvement_report(
        {
            **_body_improvement_execution_context(governed_task),
            "slot_id": "slot-B",
            "task_id": governed_task.task_id,
            "baseline_commit": "b" * 40,
            "commit_hash": "c" * 40,
            "diff_summary": "Different unevaluated commit",
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "improvement_description": "Must be rejected before Governor review.",
        }
    )

    assert result["score_delta"] == 0
    assert result["reject_reason"] == "evaluated_candidate_commit_mismatch"
    assert supervisor._body_registry.load_slot_meta("slot-B").health_history == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_body_improvement_report_reloads_and_rejects_corrupted_result(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    governed_task = _create_running_body_improvement_task(
        supervisor,
        target_paths=["src/voidcube/runtime/agent/stream_handler.py"],
    )
    result_id = str(governed_task.evidence["experiment_result_id"])
    result_path = (
        supervisor._evolution_evaluation_governance_verifier.evaluation_repository.experiment_results_root
        / f"{result_id}.json"
    )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["confidence"] = 0.1
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    supervisor._body_improvement_review_service._inspect_body_improvement_commit = Mock(
        return_value={
            "ok": True,
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "diff_text": "verified diff",
        }
    )

    result = await supervisor.receive_improvement_report(
        {
            **_body_improvement_execution_context(governed_task),
            "slot_id": "slot-B",
            "task_id": governed_task.task_id,
            "baseline_commit": "b" * 40,
            "commit_hash": "a" * 40,
            "diff_summary": "Corrupted authorization",
            "changed_files": ["src/voidcube/runtime/agent/stream_handler.py"],
            "improvement_description": "Must be rejected before Governor review.",
        }
    )

    assert result["score_delta"] == 0
    assert result["reject_reason"] == "experiment_result_unreadable"
    assert supervisor._body_registry.load_slot_meta("slot-B").health_history == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_mark_body_candidate_preserves_execution_result_from_executor(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    request = {"body_version": "v2"}
    expected = {
        "status": "candidate_marked",
        "slot": {"slot_id": "slot-B", "body_state": "candidate", "body_version": "v2"},
    }

    mark_body_candidate = AsyncMock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        mark_body_candidate=mark_body_candidate
    )
    try:
        result = await _mark_body_candidate(supervisor, "slot-B", request)
    finally:
        supervisor._execution_facade = original_facade

    assert result["status"] == "candidate_marked"
    assert result["slot"]["body_state"] == "candidate"
    assert result["slot"]["body_version"] == "v2"
    assert result == expected
    mark_body_candidate.assert_awaited_once_with("slot-B", request)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_prepare_body_slot_preserves_execution_result_from_executor(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    expected = {
        "status": "slot_prepared",
        "slot": {"slot_id": "slot-B", "materialized_from": "repo_root"},
        "runtime_manifest_path": str(tmp_path / "body-state" / "slots" / "slot-B" / "runtime" / "slot-runtime.json"),
    }

    prepare_body_slot = AsyncMock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        prepare_body_slot=prepare_body_slot
    )
    try:
        result = await _prepare_body_slot(supervisor, "slot-B")
    finally:
        supervisor._execution_facade = original_facade

    assert result["status"] == "slot_prepared"
    assert result["slot"]["materialized_from"] == "repo_root"
    assert result == expected
    prepare_body_slot.assert_awaited_once_with("slot-B", None)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_record_body_probe_report_preserves_execution_result_from_executor(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    request = {
        "slot_id": "slot-B",
        "checks": [],
    }
    expected = {
        "status": "probe_report_recorded",
        "result": {"status": "applied"},
        "report": {"slot_id": "slot-B"},
    }

    record_body_probe_report = AsyncMock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        record_body_probe_report=record_body_probe_report
    )
    try:
        result = await _record_body_probe_report(supervisor, request)
    finally:
        supervisor._execution_facade = original_facade

    assert result["status"] == "probe_report_recorded"
    assert result["result"]["status"] == "applied"
    assert result == expected
    record_body_probe_report.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_list_body_slots_preserves_facade_slots_and_count(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    expected = {
        "slots": {
            "slot-A": {"slot_id": "slot-A", "body_state": "active"},
            "slot-B": {"slot_id": "slot-B", "body_state": "shell"},
        }
    }

    list_body_slots = Mock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        list_body_slots=list_body_slots
    )
    try:
        result = await supervisor.list_body_slots()
    finally:
        supervisor._execution_facade = original_facade

    assert result == {
        "slots": [
            {"slot_id": "slot-A", "body_state": "active"},
            {"slot_id": "slot-B", "body_state": "shell"},
        ],
        "count": 2,
    }
    list_body_slots.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_run_body_probe_preserves_execution_result_from_executor(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    request = {"slot_id": "slot-B"}
    expected = {
        "status": "probe_executed",
        "report": {"overall_passed": True},
        "persistence": {"status": "applied"},
    }

    run_body_probe = AsyncMock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        run_body_probe=run_body_probe
    )
    try:
        result = await _run_body_probe(supervisor, request)
    finally:
        supervisor._execution_facade = original_facade

    assert result["status"] == "probe_executed"
    assert result["report"]["overall_passed"] is True
    assert result == expected
    run_body_probe.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_can_record_probe_report_and_review_probe_transition(tmp_path):
    supervisor = Supervisor(
        _make_supervisor_config(tmp_path)
    )
    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})

    review = await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )

    assert review["governor_response"]["decision"] == "approve"
    assert review["execution_report"]["action_results"][0]["status"] == "applied"
    slot = await supervisor.get_body_slot("slot-B")
    assert slot["body_state"] == "probe"
    assert slot["lease"] == "probe"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_can_activate_candidate_after_probe_report(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)
    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-2",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    probe_result = await _run_body_probe(
        supervisor,
        {
            "slot_id": "slot-B",
        }
    )
    assert probe_result["report"]["overall_passed"] is True

    review = await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-2",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )

    assert review["governor_response"]["decision"] == "awaiting_user_consent"
    assert review["registry"]["active_slot"] == "slot-A"
    slot_b = await supervisor.get_body_slot("slot-B")
    assert slot_b["body_state"] == "awaiting_user_consent"

    confirmation = await _confirm_body_switch(
        supervisor,
        {"slot_id": "slot-B", "approved": True, "watch_window_seconds": 120},
    )

    assert confirmation["status"] == "body_switch_activated"
    assert confirmation["registry"]["active_slot"] == "slot-B"
    slot_a = await supervisor.get_body_slot("slot-A")
    slot_b = await supervisor.get_body_slot("slot-B")
    assert slot_a["body_state"] == "retired"
    assert slot_b["body_state"] == "active"
    active_target = await supervisor.get_active_body_target()
    assert active_target["slot_id"] == "slot-B"
    assert active_target["body_version"] == "v2"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_records_governor_history_via_mem_bridge(tmp_path):
    supervisor = Supervisor(
        _make_supervisor_config(tmp_path)
    )
    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})

    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "history-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )

    history = await supervisor.get_governor_history(limit=10)
    assert len(history["history"]) >= 2
    assert history["latest"] is not None
    assert history["latest"]["kind"] in {"review", "execution_outcome"}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_watch_window_pass_recycles_retired_slot(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-watch-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-watch-1",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )
    await _confirm_body_switch(supervisor, {"slot_id": "slot-B", "approved": True})

    result = await _evaluate_watch_window(supervisor, {"healthy_override": True})

    assert result["status"] == "watch_window_evaluated"
    assert result["evaluation"]["healthy"] is True
    assert result["governor_response"]["decision"] == "approve"
    slot_a = await supervisor.get_body_slot("slot-A")
    assert slot_a["body_state"] == "shell"
    assert result["registry"]["retired_slot"] is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_watch_window_path_uses_governor_review_executor_directly(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-watch-direct-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-watch-direct-1",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )
    await _confirm_body_switch(supervisor, {"slot_id": "slot-B", "approved": True})

    with patch.object(
        supervisor._governor_review_executor,
        "execute_governor_request",
        wraps=supervisor._governor_review_executor.execute_governor_request,
    ) as execute_governor_request:
        result = await _evaluate_watch_window(supervisor, {"healthy_override": True})

    assert result["governor_response"]["decision"] == "approve"
    execute_governor_request.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.operational
async def test_supervisor_watch_window_failure_triggers_rollback(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-watch-2",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-watch-2",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )
    await _confirm_body_switch(supervisor, {"slot_id": "slot-B", "approved": True})

    result = await _evaluate_watch_window(
        supervisor,
        {
            "healthy_override": False,
            "metrics": {"reason": "health probe timeout"},
        }
    )

    assert result["status"] == "watch_window_evaluated"
    assert result["evaluation"]["healthy"] is False
    assert result["governor_response"]["decision"] == "rollback_required"
    assert result["registry"]["active_slot"] == "slot-A"
    slot_a = await supervisor.get_body_slot("slot-A")
    slot_b = await supervisor.get_body_slot("slot-B")
    assert slot_a["body_state"] == "active"
    assert slot_b["body_state"] == "retired"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_starts_watch_window_task_after_switch(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-auto-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-auto-1",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )
    await _confirm_body_switch(supervisor, {"slot_id": "slot-B", "approved": True})

    assert supervisor._watch_window_task is not None
    assert supervisor._watch_window_task.done() is False

    status = await _get_watch_window_status(supervisor)
    assert status["task_running"] is True
    assert status["watch_window"]["status"] == "active"

    supervisor._watch_window_task.cancel()
    try:
        await supervisor._watch_window_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_delegates_watch_window_runtime_followup_to_execution_facade(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-runtime-followup-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})

    recorded_responses = []

    def record_runtime_followup(governor_response):
        recorded_responses.append(governor_response)
        return {
            "status": (
                "watch_window_runtime_ensured"
                if governor_response.decision == "approve_with_watch"
                else "no_watch_window_runtime_change"
            ),
            "decision": governor_response.decision,
        }

    with patch.object(
        supervisor._watch_window_executor,
        "sync_runtime_after_governor_response",
        side_effect=record_runtime_followup,
    ):
        review = await _execute_governor_review_request(
            supervisor,
            {
                "request_id": "switch-runtime-followup-1",
                "event_type": "switch_request",
                "body_id": "slot-B",
                "source_actor": "gateway",
                "summary": "Promote body after probe pass",
                "evidence": {},
                "constraints": {"watch_window_seconds": 120},
            }
        )

    assert len(recorded_responses) == 1
    assert recorded_responses[0].decision == "awaiting_user_consent"
    assert review["runtime_followup"] == {
        "status": "no_watch_window_runtime_change",
        "decision": "awaiting_user_consent",
    }

    with patch.object(
        supervisor._watch_window_executor,
        "sync_runtime_after_governor_response",
        side_effect=record_runtime_followup,
    ):
        confirmation = await _confirm_body_switch(
            supervisor,
            {"slot_id": "slot-B", "approved": True},
        )

    assert len(recorded_responses) == 2
    assert recorded_responses[1].decision == "approve_with_watch"
    assert confirmation["switch_activation"]["runtime_followup"] == {
        "status": "watch_window_runtime_ensured",
        "decision": "approve_with_watch",
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_loop_auto_recycles_when_window_expires_cleanly(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-auto-2",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-auto-2",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )
    await _confirm_body_switch(supervisor, {"slot_id": "slot-B", "approved": True})

    registry = supervisor._body_registry.load_registry()
    registry.watch_window.expires_at = datetime.utcnow() - timedelta(seconds=1)
    supervisor._body_registry.save_registry(registry)

    supervisor._watch_window_task.cancel()
    try:
        await supervisor._watch_window_task
    except asyncio.CancelledError:
        pass

    loop_task = asyncio.create_task(supervisor._watch_window_loop())
    await asyncio.sleep(1.2)
    loop_task.cancel()
    try:
        await loop_task
    except asyncio.CancelledError:
        pass

    slot_a = await supervisor.get_body_slot("slot-A")
    status = await _get_watch_window_status(supervisor)
    assert slot_a["body_state"] == "shell"
    assert status["last_outcome"]["evaluation"]["healthy"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_watch_window_success_syncs_retired_slot_back_to_shell_from_active_slot(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    await _mark_body_candidate(supervisor, "slot-B", {"body_version": "v2"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "health-sync-1",
            "event_type": "health_review_request",
            "body_id": "slot-B",
            "source_actor": "active_body",
            "summary": "Candidate build complete",
            "evidence": {"build_complete": True},
            "constraints": {"target_transition": "candidate_to_probe"},
        }
    )
    await _run_body_probe(supervisor, {"slot_id": "slot-B"})
    await _execute_governor_review_request(
        supervisor,
        {
            "request_id": "switch-sync-1",
            "event_type": "switch_request",
            "body_id": "slot-B",
            "source_actor": "gateway",
            "summary": "Promote body after probe pass",
            "evidence": {},
            "constraints": {"watch_window_seconds": 120},
        }
    )
    await _confirm_body_switch(supervisor, {"slot_id": "slot-B", "approved": True})

    slot_b = await supervisor.get_body_slot("slot-B")
    (Path(slot_b["worktree_path"]) / "stable.marker").write_text("stable\n", encoding="utf-8")

    result = await _evaluate_watch_window(supervisor, {"healthy_override": True})
    slot_a = await supervisor.get_body_slot("slot-A")

    assert result["governor_response"]["decision"] == "approve"
    assert slot_a["body_state"] == "shell"
    assert slot_a["materialized_from"] == "slot:slot-B"
    assert (Path(slot_a["worktree_path"]) / "stable.marker").exists()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_execute_body_upgrade_runs_pipeline_to_switch(tmp_path):
    supervisor = _make_probe_ready_supervisor(tmp_path)

    result = await _execute_body_upgrade(
        supervisor,
        {
            "body_version": "v2",
            "watch_window_seconds": 90,
        }
    )

    assert result["status"] == "upgrade_awaiting_user_consent"
    assert result["slot_id"] == "slot-B"
    assert result["probe_review"]["governor_response"]["decision"] == "approve"
    assert result["probe_execution"]["report"]["overall_passed"] is True
    assert result["switch_review"]["governor_response"]["decision"] == "awaiting_user_consent"
    assert result["requires_user_consent"] is True
    assert result["active_target"]["slot_id"] == "slot-A"

    registry = await supervisor.get_body_registry()
    assert registry["registry"]["active_slot"] == "slot-A"
    assert registry["registry"]["retired_slot"] is None
    assert registry["slots"]["slot-B"]["body_state"] == "awaiting_user_consent"
    assert registry["slots"]["slot-A"]["body_state"] == "active"

    confirmation = await _confirm_body_switch(
        supervisor,
        {"slot_id": "slot-B", "approved": True, "watch_window_seconds": 90},
    )
    assert confirmation["status"] == "body_switch_activated"
    registry = await supervisor.get_body_registry()
    assert registry["registry"]["active_slot"] == "slot-B"
    assert registry["registry"]["retired_slot"] == "slot-A"
    assert registry["slots"]["slot-B"]["body_state"] == "active"
    assert registry["slots"]["slot-A"]["body_state"] == "retired"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_execute_body_upgrade_delegates_to_execution_facade(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    request = {
        "body_version": "v2",
        "execution_request": {
            "trace_id": "trace-formal-1",
            "task_type": "self_evolution",
            "decision_id": "decision-formal-1",
            "git_lineage": {
                "source_branch": "main",
                "source_commit": "aaa111",
                "candidate_branch": "evolution/task-1",
                "candidate_commit": "bbb222",
                "active_ref": "stable/v2",
                "rollback_ref": "body/slot-A",
                "rollback_commit": "aaa111",
                "diff_summary": "Formal lineage handoff.",
                "changed_files": ["systems/execution/adapters.py"],
            }
        },
    }
    expected = {
        "status": "upgrade_awaiting_user_consent",
        "requires_user_consent": True,
        "active_target": {
            "slot_id": "slot-A",
        },
        "probe_execution": {
            "report": {
                "candidate_commit": "bbb222",
                "changed_files": ["systems/execution/adapters.py"],
            }
        },
        "execution_request": request["execution_request"],
        "switch_review": {"governor_response": {"decision": "awaiting_user_consent"}},
    }

    execute_body_upgrade = AsyncMock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        execute_body_upgrade=execute_body_upgrade
    )
    try:
        result = await _execute_body_upgrade(supervisor, request)
    finally:
        supervisor._execution_facade = original_facade

    assert result == expected
    execute_body_upgrade.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_supervisor_evaluate_watch_window_delegates_to_execution_facade(tmp_path):
    supervisor = Supervisor(_make_supervisor_config(tmp_path))
    request = {
        "healthy_override": False,
        "metrics": {"reason": "simulated runtime failure"},
    }

    expected = {
        "status": "watch_window_evaluated",
        "governor_response": {"decision": "rollback_required"},
        "execution_followup": {
            "action": "failed_slot_drained",
            "slot_id": "slot-B",
            "stopped_instance_ids": ["failed-new"],
        },
    }

    evaluate_watch_window = AsyncMock(return_value=expected)
    original_facade = supervisor._execution_facade
    supervisor._execution_facade = SimpleNamespace(
        evaluate_watch_window=evaluate_watch_window
    )
    try:
        result = await _evaluate_watch_window(supervisor, request)
    finally:
        supervisor._execution_facade = original_facade

    assert result == expected
    evaluate_watch_window.assert_awaited_once_with(request)
