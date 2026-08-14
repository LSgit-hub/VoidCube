from __future__ import annotations

import asyncio
import platform
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from systems.evolution_candidate_generation import (
    CandidateLearningReference,
    EvolutionCandidateGenerationRequest,
)
from systems.evolution_authoring import AIAgentAuthoringAdapter
from systems.evolution_evaluation import MetricTarget
from systems.supervisor.evolution_candidate_generation_service import (
    EvolutionCandidateGenerationService,
)
from systems.supervisor.evolution_candidate_generation_scheduler import (
    EvolutionCandidateGenerationScheduler,
)
from systems.supervisor.endogenous_body_projection import (
    build_body_improvement_projection,
)
from systems.supervisor.endogenous_candidate_factories import (
    build_body_improvement_candidate,
)
from systems.supervisor.endogenous_foundation_bridge import (
    EndogenousFoundationReadOnlyProjection,
)
from tools.file_tools import write_file_tool


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        platform.system().lower() != "windows",
        reason="native candidate generation requires Windows",
    ),
]
PROJECT_ROOT = Path(__file__).parents[1].resolve()
PROJECT_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"


class _ControlledConversationAgent:
    valid_tool_names = {
        "read_file",
        "write_file",
        "patch",
        "search_files",
        "terminal",
        "process",
    }

    def run_conversation(self, _user_message: str, **kwargs: object):
        result = write_file_tool(
            "agent/demo.py",
            "VALUE = 'candidate'\n",
            task_id=str(kwargs["task_id"]),
        )
        return {
            "completed": not result.startswith("Error"),
            "interrupted": False,
            "final_response": result,
        }


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "agent").mkdir()
    (repository / "agent" / "demo.py").write_text(
        "VALUE = 'baseline'\n",
        encoding="utf-8",
    )
    probe_package = repository / "systems" / "evolution_evaluation"
    probe_package.mkdir(parents=True)
    (repository / "systems" / "__init__.py").write_text("", encoding="utf-8")
    (probe_package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(
        PROJECT_ROOT / "systems" / "evolution_evaluation" / "windows_probes.py",
        probe_package / "windows_probes.py",
    )
    desktop = repository / "desktop"
    desktop.mkdir()
    shutil.copy2(PROJECT_ROOT / "desktop" / "package-lock.json", desktop)
    shutil.copytree(
        PROJECT_ROOT / "desktop" / "node_modules" / "node-pty",
        desktop / "node_modules" / "node-pty",
    )
    (repository / ".gitignore").write_text(
        "desktop/node_modules/\n__pycache__/\n",
        encoding="utf-8",
    )
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    _git("add", ".", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


@pytest.mark.asyncio
async def test_real_scheduler_shadow_then_manual_native_first_candidate_cycle(
    tmp_path: Path,
):
    if not PROJECT_PYTHON.is_file():
        pytest.skip("project virtual environment is unavailable")
    repository, baseline = _repository(tmp_path)
    now = datetime.now(timezone.utc)
    request = EvolutionCandidateGenerationRequest.create(
        mapping_key="mapping-real-native",
        mapping_source="integration-test",
        target_body_slot_id="slot-B",
        objective="Update the demo value without breaking native behavior.",
        improvement_hypothesis="The focused edit remains compatible on Windows.",
        baseline_commit=baseline,
        source_learning_refs=(
            CandidateLearningReference(
                learning_id="learning-real-native",
                completed_at=now - timedelta(minutes=1),
                relevance=1.0,
                title="Native compatibility evidence",
                target_paths=("agent/demo.py",),
            ),
        ),
        allowed_paths=("agent/demo.py",),
        max_files_changed=1,
        test_commands=("python -m py_compile agent/demo.py",),
        command_timeout_seconds=120,
        target_metrics=(MetricTarget(metric="correctness", objective="increase"),),
    )

    authoring_agent = AIAgentAuthoringAdapter(
        runtime_provider_resolver=lambda **_kwargs: {},
        model_resolver=lambda: "acceptance-model",
        ai_agent_factory=lambda **_kwargs: _ControlledConversationAgent(),
    )

    foundation_root = tmp_path / "foundation"
    service = EvolutionCandidateGenerationService.from_root(
        repository,
        foundation_root,
        authoring_agent=authoring_agent,
        python_executable=PROJECT_PYTHON,
    )
    async def load_quiet_runtime_observation():
        return {
            "observation_input": {
                "activity": {"active_sessions": 0},
                "user_chain_signal": {"is_quiet": True},
            }
        }

    scheduler = EvolutionCandidateGenerationScheduler(
        repository=service.candidate_repository,
        execute=service.execute,
        automatic_enabled=lambda: False,
        load_runtime_observation=load_quiet_runtime_observation,
        has_active_body_task=lambda: False,
        lease_owner="native-acceptance-scheduler",
    )
    registered = scheduler.register(request, requested_at=now)

    shadow = await scheduler.trigger(mode="shadow", request_id=request.request_id)

    assert registered["state"]["status"] == "pending"
    assert shadow["status"] == "shadow_ready"
    assert shadow["would_start"] is True
    assert service.candidate_repository.get_current_state(request.request_id).status == (
        "pending"
    )
    assert _git(
        "for-each-ref",
        "--format=%(refname)",
        "refs/evolution/candidates",
        cwd=repository,
    ) == ""

    started = await scheduler.trigger(
        mode="manual",
        request_id=request.request_id,
    )

    async def wait_for_background_cycle() -> None:
        while scheduler.status()["background_task_running"]:
            await asyncio.sleep(0.05)

    assert started["status"] == "started"
    await asyncio.wait_for(wait_for_background_cycle(), timeout=180)
    status = scheduler.status()
    state = service.candidate_repository.get_current_state(request.request_id)
    assert state is not None
    authoring = service.authoring_repository.get(str(state.authoring_result_id))
    result = service.evaluation_service.evaluation_repository.get_experiment_result(
        str(state.experiment_result_id)
    )
    authorization = service.evaluation_service.governance_verifier.verify(
        str(state.experiment_result_id)
    )

    assert state.status == "authorized", (
        state.error_code,
        state.error_reason,
        authoring,
    )
    assert status["latest_run"]["result_state"] == "authorized"
    assert status["latest_run"]["authoring_result_id"] == state.authoring_result_id
    assert status["latest_run"]["experiment_result_id"] == state.experiment_result_id
    assert authoring is not None
    assert result is not None and result.verdict == "promote"
    assert authorization["authorized"] is True
    assert len(result.benchmark_case_evidence or ()) == 12
    assert _git("rev-parse", "HEAD", cwd=repository) == baseline
    assert _git("status", "--porcelain", cwd=repository) == ""
    candidate = str(authoring.candidate_commit)
    assert _git(
        "show",
        f"{candidate}:agent/demo.py",
        cwd=repository,
    ) == "VALUE = 'candidate'"
    assert service.candidate_repository.state_history(request.request_id)[-1] == (
        state
    )

    foundation = EndogenousFoundationReadOnlyProjection.from_root(
        foundation_root
    ).load()
    body_projection = build_body_improvement_projection(
        drive_context={
            "policy": {
                "body_improvement_editable_dirs": ["agent/"],
                "body_improvement_forbidden_patterns": ["**/credential*"],
                "body_improvement_max_files": 1,
            },
            "completed_learning_tasks": [
                {
                    "task_id": "learning-real-native",
                    "title": "Native demo learning",
                    "conclusion": "Update agent/demo.py using the evaluated candidate.",
                    "completed_at": now.isoformat(),
                    "quality_score": 1.0,
                }
            ],
            "api_b_judgement_tasks": [],
            "evolution_foundation": foundation,
        },
        shell_slot_meta={
            "slot_id": "slot-B",
            "worktree_path": str(repository),
        },
    )
    body_candidate = build_body_improvement_candidate(
        body_projection=body_projection,
        backlog_pressure_penalty=0.0,
        adaptive_policy=SimpleNamespace(
            body_growth_bias=0.65,
            candidate_throttle=0.0,
            preferred_focus="body_growth",
        ),
        drive_judgement={"decision": "candidate_authorized"},
    )

    assert foundation["evaluation"]["body_improvement_authorization"][
        "authorized"
    ] is True
    assert body_projection["available"] is True
    assert body_candidate.execution_kind == "body_improvement"
    assert body_candidate.constraints["requires_user_consent"] is True
    assert body_candidate.constraints["evaluated_candidate_commit"] == candidate
    assert body_candidate.evidence["experiment_result_id"] == (
        state.experiment_result_id
    )
