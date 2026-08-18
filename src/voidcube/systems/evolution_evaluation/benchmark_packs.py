"""Versioned standard BenchmarkPacks for native-first evolution."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

from .models import (
    BenchmarkCase,
    BenchmarkCommandEvidence,
    BenchmarkPack,
    HardGateResult,
    MetricValue,
    ScoringDimension,
    ScoringPolicy,
)
from .runners import (
    BenchmarkCaseEvaluation,
    NativeFirstBenchmarkExecutorFactory,
    ValidationCaseEvaluator,
)


NATIVE_FIRST_PACK_VERSION = "native-first-platform/1"
NATIVE_COMPATIBILITY_METRIC = "native_compatibility"
NATIVE_COMPATIBILITY_GATE = "native_compatibility"

_RUNNER_PROBES = {
    "python-import": "python-import",
    "windows-path": "windows-path",
    "windows-readonly": "windows-readonly",
    "windows-subprocess": "windows-subprocess",
    "windows-file-lock": "windows-file-lock",
    "windows-node-native": "windows-node-native",
}


def create_native_first_benchmark_pack(*, created_at: datetime) -> BenchmarkPack:
    """Create the immutable standard pack used by native-first validation."""

    cases = [
        BenchmarkCase(
            case_id="python-import",
            runner="python-import",
            input_ref="systems/evolution_evaluation",
            tags=("risk:python-runtime",),
        )
    ]
    cases.extend(
        BenchmarkCase(
            case_id=runner,
            runner=runner,
            input_ref="systems/evolution_evaluation/windows_probes.py",
            tags=("platform:windows", f"risk:{runner.removeprefix('windows-')}"),
        )
        for runner in _RUNNER_PROBES
        if runner.startswith("windows-")
    )
    return BenchmarkPack.create(
        name="VoidCube native-first platform compatibility",
        pack_version=NATIVE_FIRST_PACK_VERSION,
        cases=tuple(cases),
        created_at=created_at,
    )


def create_native_first_scoring_policy(
    required_platforms: Iterable[str],
    *,
    created_at: datetime,
) -> ScoringPolicy:
    return ScoringPolicy.create(
        policy_version=NATIVE_FIRST_PACK_VERSION,
        dimensions=(ScoringDimension(name=NATIVE_COMPATIBILITY_METRIC, weight=1.0),),
        required_hard_gates=(NATIVE_COMPATIBILITY_GATE,),
        required_validation_platforms=tuple(required_platforms),
        promote_threshold=1.0,
        observe_threshold=1.0,
        created_at=created_at,
    )


def native_first_benchmark_evaluators() -> Mapping[str, ValidationCaseEvaluator]:
    return {name: _evaluate_native_case for name in _RUNNER_PROBES}


def create_native_first_executor_factory(
    repository: str | Path,
    *,
    worktree_root: str | Path,
    python_executable: str | Path | None = None,
    case_timeout_seconds: float = 60.0,
) -> NativeFirstBenchmarkExecutorFactory:
    root = Path(repository).expanduser().resolve()
    return NativeFirstBenchmarkExecutorFactory(
        root,
        worktree_root=worktree_root,
        evaluators=native_first_benchmark_evaluators(),
        python_executable=python_executable,
        workspace_dependencies={
            "desktop/node_modules": root / "desktop" / "node_modules"
        },
        case_timeout_seconds=case_timeout_seconds,
    )


def _evaluate_native_case(request, task_id, _environment) -> BenchmarkCaseEvaluation:
    probe = _RUNNER_PROBES.get(request.case.runner)
    if probe is None:
        raise ValueError(f"unsupported native benchmark runner: {request.case.runner}")
    command = f"python -m systems.evolution_evaluation.windows_probes {probe}"
    from ...infrastructure.execution.terminal_tool import terminal_tool

    payload = json.loads(
        terminal_tool(command, task_id=task_id, timeout=request.timeout_seconds)
    )
    exit_code = int(payload.get("exit_code", -1))
    timed_out = bool(payload.get("timed_out"))
    passed = exit_code == 0 and not timed_out
    summary = str(
        payload.get("output")
        or payload.get("error")
        or "native compatibility probe returned no output"
    ).strip()[:50_000] or "native compatibility probe returned empty output"
    return BenchmarkCaseEvaluation(
        case_id=request.case.case_id,
        metrics=(
            MetricValue(
                metric=NATIVE_COMPATIBILITY_METRIC,
                value=1.0 if passed else 0.0,
                unit="ratio",
            ),
        ),
        hard_gate_results=(
            HardGateResult(
                gate=NATIVE_COMPATIBILITY_GATE,
                passed=passed,
                evidence_refs=(f"probe:{probe}",),
            ),
        ),
        command_evidence=(
            BenchmarkCommandEvidence(
                command=command,
                exit_code=exit_code,
                output_summary=summary,
                timed_out=timed_out,
                security_scanner_status=str(
                    payload.get("security_scanner_status") or "error"
                ),
                container_disk_quota_status=str(
                    payload.get("container_disk_quota_status")
                    or (
                        "not_applicable"
                        if request.validation_platform == "windows"
                        else "not_requested"
                    )
                ),
            ),
        ),
        evidence_refs=(f"native-first-pack:{NATIVE_FIRST_PACK_VERSION}",),
    )


__all__ = [
    "NATIVE_COMPATIBILITY_GATE",
    "NATIVE_COMPATIBILITY_METRIC",
    "NATIVE_FIRST_PACK_VERSION",
    "create_native_first_benchmark_pack",
    "create_native_first_executor_factory",
    "create_native_first_scoring_policy",
    "native_first_benchmark_evaluators",
]
