from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from systems.evolution_evaluation import (
    BenchmarkCase,
    BenchmarkCaseEvaluation,
    BenchmarkCaseFailed,
    BenchmarkCommandEvidence,
    BenchmarkRunRequest,
    HardGateResult,
    MetricValue,
    build_container_environment_manifest,
    build_native_first_platform_runners,
    capture_host_environment_manifest,
)


pytestmark = [pytest.mark.unit, pytest.mark.smoke]


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


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git("init", cwd=repository)
    _git("config", "user.name", "VoidCube Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    (repository / "value.txt").write_text("baseline\n", encoding="utf-8")
    _git("add", "value.txt", cwd=repository)
    _git("commit", "-m", "baseline", cwd=repository)
    baseline = _git("rev-parse", "HEAD", cwd=repository)
    (repository / "value.txt").write_text("candidate\n", encoding="utf-8")
    _git("add", "value.txt", cwd=repository)
    _git("commit", "-m", "candidate", cwd=repository)
    return repository, baseline, _git("rev-parse", "HEAD", cwd=repository)


def _request(*, subject: str, candidate: str, platform: str) -> BenchmarkRunRequest:
    return BenchmarkRunRequest(
        subject=subject,
        subject_snapshot_id="self-cognition-" + "1" * 64,
        candidate_commit=candidate if subject == "candidate" else None,
        benchmark_pack_id="benchmark-pack-" + "2" * 64,
        case=BenchmarkCase(case_id="quality", runner="quality", input_ref="value.txt"),
        timeout_seconds=30,
        validation_platform=platform,
    )


def _preparer(platform: str, calls: list[tuple[str, str]]):
    def prepare(task_id: str, worktree: str, *, expected_head: str, **_kwargs):
        calls.append((platform, expected_head))
        if platform == "windows":
            return capture_host_environment_manifest(
                worktree,
                repository_head=expected_head,
            ).model_dump(mode="json")
        return build_container_environment_manifest(
            worktree,
            backend="podman",
            execution_workspace_path="/workspace",
            probe={
                "os_name": "Linux",
                "os_release": "test",
                "architecture": "x86_64",
                "repository_head": expected_head,
                "image_reference": "localhost/voidcube-test:1",
                "image_digest": "sha256:" + "3" * 64,
                "tools": {},
            },
        ).model_dump(mode="json")

    return prepare


def _evaluation(request, _task_id, environment):
    value = 0.8 if request.subject == "baseline" else 0.9
    return BenchmarkCaseEvaluation(
        case_id=request.case.case_id,
        metrics=(MetricValue(metric="correctness", value=value, unit="ratio"),),
        hard_gate_results=(HardGateResult(gate="tests", passed=True),),
        command_evidence=(
            BenchmarkCommandEvidence(
                command="check value.txt",
                exit_code=0,
                output_summary=environment.validated_platforms[0],
                security_scanner_status="available",
                container_disk_quota_status="not_applicable",
            ),
        ),
    )


def test_windows_only_factory_never_constructs_or_calls_podman_runner(tmp_path: Path):
    repository, baseline, candidate = _repository(tmp_path)
    calls: list[tuple[str, str]] = []
    runners = build_native_first_platform_runners(
        repository,
        worktree_root=tmp_path / "validation",
        baseline_commit=baseline,
        candidate_commit=candidate,
        required_platforms=("windows",),
        evaluators={"quality": _evaluation},
        prepare_environments={
            "windows": _preparer("windows", calls),
            "linux": _preparer("linux", calls),
        },
        release_environment=lambda _task_id: None,
    )

    result = runners["windows"]["quality"](
        _request(subject="candidate", candidate=candidate, platform="windows")
    )

    assert tuple(runners) == ("windows",)
    assert calls == [("windows", candidate)]
    assert result.execution_environment.validated_platforms == ("windows",)
    assert not (tmp_path / "validation" / "windows").exists()


def test_dual_platform_runners_bind_each_subject_and_clean_worktrees(tmp_path: Path):
    repository, baseline, candidate = _repository(tmp_path)
    calls: list[tuple[str, str]] = []
    runners = build_native_first_platform_runners(
        repository,
        worktree_root=tmp_path / "validation",
        baseline_commit=baseline,
        candidate_commit=candidate,
        required_platforms=("linux", "windows"),
        evaluators={"quality": _evaluation},
        prepare_environments={
            "windows": _preparer("windows", calls),
            "linux": _preparer("linux", calls),
        },
        release_environment=lambda _task_id: None,
    )

    results = [
        runners[platform]["quality"](
            _request(subject=subject, candidate=candidate, platform=platform)
        )
        for platform in ("linux", "windows")
        for subject in ("baseline", "candidate")
    ]

    assert calls == [
        ("linux", baseline),
        ("linux", candidate),
        ("windows", baseline),
        ("windows", candidate),
    ]
    assert {result.execution_environment.validated_platforms for result in results} == {
        ("linux",),
        ("windows",),
    }
    assert all(result.subject_checkout is not None for result in results)
    assert not (tmp_path / "validation" / "linux").exists()
    assert not (tmp_path / "validation" / "windows").exists()


def test_prepare_failure_removes_disposable_worktree(tmp_path: Path):
    repository, baseline, candidate = _repository(tmp_path)

    def blocked_prepare(*_args, **_kwargs):
        raise RuntimeError("Podman is unavailable")

    runner = build_native_first_platform_runners(
        repository,
        worktree_root=tmp_path / "validation",
        baseline_commit=baseline,
        candidate_commit=candidate,
        required_platforms=("linux",),
        evaluators={"quality": _evaluation},
        prepare_environments={"linux": blocked_prepare},
        release_environment=lambda _task_id: None,
    )["linux"]["quality"]

    with pytest.raises(RuntimeError, match="Podman is unavailable"):
        runner(_request(subject="baseline", candidate=candidate, platform="linux"))

    assert not (tmp_path / "validation" / "linux").exists()


def test_cleanup_failure_rejects_otherwise_successful_case(tmp_path: Path):
    repository, baseline, candidate = _repository(tmp_path)

    def failed_release(_task_id: str) -> None:
        raise RuntimeError("release failed")

    runner = build_native_first_platform_runners(
        repository,
        worktree_root=tmp_path / "validation",
        baseline_commit=baseline,
        candidate_commit=candidate,
        required_platforms=("windows",),
        evaluators={"quality": _evaluation},
        prepare_environments={"windows": _preparer("windows", [])},
        release_environment=failed_release,
    )["windows"]["quality"]

    with pytest.raises(BenchmarkCaseFailed, match="cleanup failed"):
        runner(_request(subject="baseline", candidate=candidate, platform="windows"))

    assert not (tmp_path / "validation" / "windows").exists()


def test_workspace_dependency_cannot_escape_repository(tmp_path: Path):
    repository, baseline, candidate = _repository(tmp_path)
    outside = tmp_path / "outside-dependency"
    outside.mkdir()

    with pytest.raises(ValueError, match="must belong to the repository"):
        build_native_first_platform_runners(
            repository,
            worktree_root=tmp_path / "validation",
            baseline_commit=baseline,
            candidate_commit=candidate,
            required_platforms=("windows",),
            evaluators={"quality": _evaluation},
            workspace_dependencies={"vendor/dependency": outside},
            prepare_environments={"windows": _preparer("windows", [])},
            release_environment=lambda _task_id: None,
        )
