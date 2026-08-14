from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from systems.evolution_candidate_generation import (
    CandidateLearningReference,
    EvolutionCandidateGenerationRequest,
)
from systems.evolution_evaluation import MetricTarget
from systems.supervisor.evolution_candidate_generation_service import (
    EvolutionCandidateGenerationService,
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
async def test_real_application_service_authoring_and_native_first_evaluation(
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

    class Agent:
        def author(self, context):
            result = write_file_tool(
                "agent/demo.py",
                "VALUE = 'candidate'\n",
                task_id=context.task_id,
            )
            return {
                "completed": not result.startswith("Error"),
                "summary": result,
            }

    foundation_root = tmp_path / "foundation"
    service = EvolutionCandidateGenerationService.from_root(
        repository,
        foundation_root,
        authoring_agent=Agent(),
        python_executable=PROJECT_PYTHON,
    )
    service.candidate_repository.register(request, requested_at=now)

    outcome = await service.execute(
        request.request_id,
        lease_owner="integration-worker",
    )

    assert outcome.state.status == "authorized", (
        outcome.state.error_code,
        outcome.state.error_reason,
        outcome.authoring_result,
    )
    assert outcome.authoring_result is not None
    assert outcome.evaluation_outcome is not None
    assert outcome.evaluation_outcome.experiment_result.verdict == "promote"
    assert outcome.evaluation_outcome.governance_authorization["authorized"] is True
    assert len(
        outcome.evaluation_outcome.experiment_result.benchmark_case_evidence or ()
    ) == 12
    assert _git("rev-parse", "HEAD", cwd=repository) == baseline
    assert _git("status", "--porcelain", cwd=repository) == ""
    candidate = str(outcome.authoring_result.candidate_commit)
    assert _git(
        "show",
        f"{candidate}:agent/demo.py",
        cwd=repository,
    ) == "VALUE = 'candidate'"
    assert service.candidate_repository.state_history(request.request_id)[-1] == (
        outcome.state
    )
