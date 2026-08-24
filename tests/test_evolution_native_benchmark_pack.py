from __future__ import annotations

import json
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from voidcube.systems.evolution_evaluation import (
    NATIVE_COMPATIBILITY_METRIC,
    BenchmarkRunRequest,
    ExperimentSpec,
    MetricTarget,
    benchmark_case_supports_platform,
    create_native_first_benchmark_pack,
    create_native_first_executor_factory,
    create_native_first_scoring_policy,
    native_first_benchmark_evaluators,
    select_benchmark_platforms,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]
NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).parents[1]
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


def test_standard_pack_is_content_addressed_and_platform_sensitive():
    first = create_native_first_benchmark_pack(created_at=NOW)
    second = create_native_first_benchmark_pack(created_at=NOW)

    assert first == second
    assert len(first.cases) == 6
    assert all(
        benchmark_case_supports_platform(case, "windows") for case in first.cases
    )
    assert [
        case.case_id
        for case in first.cases
        if benchmark_case_supports_platform(case, "linux")
    ] == ["python-import"]
    assert first.cases[0].input_ref == "src/voidcube/systems/evolution_evaluation"
    assert all(
        case.input_ref.startswith("src/voidcube/systems/evolution_evaluation")
        for case in first.cases
    )

    with pytest.raises(ValidationError, match="platform.*unique"):
        type(first.cases[0]).model_validate(
            {
                **first.cases[0].model_dump(),
                "tags": ("platform:windows", "platform:Windows"),
            }
        )


def test_native_evaluator_records_failed_probe_as_gate_evidence(monkeypatch):
    pack = create_native_first_benchmark_pack(created_at=NOW)
    case = next(item for item in pack.cases if item.case_id == "windows-file-lock")
    request = BenchmarkRunRequest(
        subject="candidate",
        subject_snapshot_id="self-cognition-" + "1" * 64,
        candidate_commit="a" * 40,
        benchmark_pack_id=pack.benchmark_pack_id,
        case=case,
        timeout_seconds=30,
        validation_platform="windows",
    )
    monkeypatch.setattr(
        "voidcube.infrastructure.execution.terminal_tool.terminal_tool",
        lambda *_args, **_kwargs: json.dumps(
            {
                "exit_code": 9,
                "error": "lock was not enforced",
                "security_scanner_status": "available",
                "container_disk_quota_status": "not_applicable",
            }
        ),
    )

    evaluation = native_first_benchmark_evaluators()[case.runner](
        request,
        "native-pack-failure",
        object(),
    )

    assert evaluation.metrics[0].value == 0.0
    assert not evaluation.hard_gate_results[0].passed
    assert evaluation.command_evidence[0].exit_code == 9
    assert "lock was not enforced" in evaluation.command_evidence[0].output_summary


@pytest.mark.integration
@pytest.mark.skipif(
    platform.system().lower() != "windows"
    or not PROJECT_PYTHON.is_file()
    or shutil.which("tirith") is None,
    reason="requires Windows, the project virtual environment, and Tirith",
)
def test_standard_pack_runs_in_real_windows_linked_worktrees(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    probe_package = repository / "src" / "voidcube" / "systems" / "evolution_evaluation"
    probe_package.mkdir(parents=True)
    (repository / "src" / "voidcube" / "systems" / "__init__.py").write_text("", encoding="utf-8")
    (probe_package / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(
        PROJECT_ROOT / "src" / "voidcube" / "systems" / "evolution_evaluation" / "windows_probes.py",
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
        "desktop/node_modules/\n",
        encoding="utf-8",
    )
    (repository / "marker.txt").write_text("baseline\n", encoding="utf-8")
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    _git("add", ".", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    baseline = _git("rev-parse", "HEAD", cwd=repository)
    (repository / "marker.txt").write_text("candidate\n", encoding="utf-8")
    _git("add", "marker.txt", cwd=repository)
    _git("commit", "-m", "candidate", cwd=repository)
    candidate = _git("rev-parse", "HEAD", cwd=repository)

    pack = create_native_first_benchmark_pack(created_at=NOW)
    policy = create_native_first_scoring_policy(("windows",), created_at=NOW)
    selection = select_benchmark_platforms(
        ("marker.txt",),
        "f" * 64,
        created_at=NOW,
    )
    spec = ExperimentSpec.create(
        platform_selection=selection,
        baseline_snapshot_id="self-cognition-" + "1" * 64,
        candidate_commit=candidate,
        candidate_snapshot_id="self-cognition-" + "2" * 64,
        hypothesis="Windows-native behavior remains compatible.",
        target_metrics=(
            MetricTarget(metric=NATIVE_COMPATIBILITY_METRIC, objective="maintain"),
        ),
        benchmark_pack_id=pack.benchmark_pack_id,
        scoring_policy_id=policy.scoring_policy_id,
        created_at=NOW,
    )
    executor = create_native_first_executor_factory(
        repository,
        worktree_root=tmp_path / "validation-worktrees",
        python_executable=PROJECT_PYTHON,
        case_timeout_seconds=60,
    )(
        selection=selection,
        baseline_commit=baseline,
        candidate_commit=candidate,
    )

    result = executor.execute(
        benchmark_pack=pack,
        experiment_spec=spec,
        scoring_policy=policy,
        completed_at=NOW,
    )

    assert result.verdict == "promote"
    assert result.confidence == 1.0
    assert len(result.benchmark_case_evidence or ()) == 12
    assert any(
        "node_pty_version" in command.output_summary
        for evidence in result.benchmark_case_evidence or ()
        for command in evidence.commands
    )
    assert (desktop / "node_modules" / "node-pty" / "package.json").is_file()
    assert not (tmp_path / "validation-worktrees" / "windows").exists()
