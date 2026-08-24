"""Register endogenous body-improvement requests after runtime validation."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..body_registry import BodyRegistryManager
from ..evolution_candidate_generation import (
    CandidateLearningReference,
    EvolutionCandidateGenerationRequest,
)
from ..evolution_evaluation import MetricTarget
from .body_execution_readiness import inspect_body_execution_readiness
from .evolution_candidate_generation_scheduler import (
    EvolutionCandidateGenerationScheduler,
)


class EvolutionCandidateGenerationRequestService:
    """Turn a read-only endogenous projection into an idempotent request.

    This service is deliberately the only bridge from drive evaluation to the
    candidate-generation repository. It never edits a body worktree.
    """

    def __init__(
        self,
        *,
        body_registry: BodyRegistryManager,
        scheduler: EvolutionCandidateGenerationScheduler,
        test_commands: tuple[str, ...] | None = None,
        target_metrics: tuple[MetricTarget, ...] | None = None,
        command_timeout_seconds: int = 300,
    ) -> None:
        self._body_registry = body_registry
        self._scheduler = scheduler
        self._test_commands = tuple(test_commands or (
            "python -m compileall -q src/voidcube/runtime/agent src/voidcube/extensions/tools skills",
        ))
        self._target_metrics = tuple(target_metrics or (
            MetricTarget(metric="correctness", objective="increase"),
        ))
        self._command_timeout_seconds = max(1, min(int(command_timeout_seconds), 3600))

    def register_from_evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        """Register the ready projection, or return a structured refusal."""

        drive_input = dict(evaluation.get("drive_input") or {})
        projection = dict(drive_input.get("candidate_generation") or {})
        if not projection.get("candidate_generation_ready"):
            return {
                "status": "skipped",
                "reason": str(
                    projection.get("reason") or "candidate_generation_not_ready"
                ),
            }

        try:
            request = self._build_request(projection)
        except (OSError, RuntimeError, ValueError) as exc:
            return {
                "status": "rejected",
                "reason": "candidate_generation_request_rejected",
                "error_code": type(exc).__name__,
                "detail": str(exc),
            }
        registered = self._scheduler.register(request)
        return {
            "status": "registered",
            "request_id": request.request_id,
            "mapping_key": request.mapping_key,
            "state": registered.get("state"),
        }

    def _build_request(
        self, projection: dict[str, Any]
    ) -> EvolutionCandidateGenerationRequest:
        registry = self._body_registry.load_registry()
        target_slot_id = str(
            projection.get("target_slot_id") or projection.get("target_body_slot_id") or ""
        ).strip()
        if not target_slot_id or target_slot_id != registry.shell_slot:
            raise ValueError("candidate generation target must be the registered shell slot")
        meta = self._body_registry.load_slot_meta(target_slot_id)
        if meta.body_state != "shell":
            raise ValueError("candidate generation target slot is not in shell state")

        worktree = Path(str(projection.get("worktree_path") or "")).resolve()
        canonical_worktree = Path(meta.worktree_path).resolve()
        if worktree != canonical_worktree:
            raise ValueError("candidate generation worktree is not the canonical shell worktree")
        readiness = inspect_body_execution_readiness(
            slot_id=target_slot_id,
            worktree_path=str(canonical_worktree),
            expected_body_state="shell",
        )
        if not readiness.get("ready"):
            raise ValueError(f"shell worktree is not ready: {readiness.get('reason')}")

        baseline = self._git(canonical_worktree, "rev-parse", "--verify", "HEAD")
        status = self._git(
            canonical_worktree,
            "status",
            "--porcelain",
            "--untracked-files=all",
            require_output=False,
        )
        if status:
            raise ValueError("shell worktree must be clean before candidate generation")
        if str(meta.candidate_commit or "").strip() and str(meta.candidate_commit).lower() != baseline.lower():
            raise ValueError("shell metadata candidate_commit does not match its current HEAD")

        allowed_paths = tuple(
            str(path).replace("\\", "/").strip().rstrip("/")
            for path in list(projection.get("target_paths") or [])
            if str(path).strip()
        )
        if not allowed_paths:
            raise ValueError("candidate generation projection has no allowed target paths")
        source_learning_refs = tuple(
            self._learning_reference(item, allowed_paths)
            for item in list(projection.get("learning_refs") or [])
        )
        if not source_learning_refs:
            raise ValueError("candidate generation projection has no valid learning evidence")

        objective = str(projection.get("objective") or "").strip()
        if not objective:
            objective = (
                "Improve the shell body according to completed learning evidence while "
                "preserving the existing runtime contract."
            )
        hypothesis = str(projection.get("improvement_hypothesis") or "").strip()
        if not hypothesis:
            domains = ", ".join(str(item) for item in projection.get("structure_domains") or [])
            hypothesis = (
                "A focused, evidence-mapped change"
                + (f" in {domains}" if domains else "")
                + " can improve the body without changing unrelated behavior."
            )
        forbidden = tuple(
            str(pattern).strip()
            for pattern in list(projection.get("forbidden_patterns") or [])
            if str(pattern).strip()
        )
        max_files = max(1, min(int(projection.get("max_files_changed") or len(allowed_paths)), len(allowed_paths)))
        return EvolutionCandidateGenerationRequest.create(
            mapping_key=str(projection.get("mapping_key") or "").strip(),
            mapping_source=str(projection.get("mapping_source") or "").strip(),
            target_body_slot_id=target_slot_id,
            objective=objective,
            improvement_hypothesis=hypothesis,
            baseline_commit=baseline,
            source_learning_refs=source_learning_refs,
            allowed_paths=allowed_paths,
            forbidden_patterns=forbidden,
            max_files_changed=max_files,
            test_commands=self._test_commands,
            command_timeout_seconds=self._command_timeout_seconds,
            target_metrics=self._target_metrics,
        )

    @staticmethod
    def _learning_reference(
        item: Any,
        allowed_paths: tuple[str, ...],
    ) -> CandidateLearningReference:
        row = dict(item) if isinstance(item, dict) else {}
        completed_at = str(row.get("timestamp") or row.get("completed_at") or "").strip()
        parsed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        target_paths = tuple(
            str(path).replace("\\", "/").strip().rstrip("/")
            for path in list(row.get("target_paths") or [])
            if str(path).strip() and str(path).replace("\\", "/").strip().rstrip("/") in allowed_paths
        )
        if not target_paths:
            raise ValueError("learning evidence has no target path in the allowed scope")
        return CandidateLearningReference(
            learning_id=str(row.get("mem_id") or row.get("learning_id") or "").strip(),
            completed_at=parsed,
            relevance=max(0.0, min(1.0, float(row.get("relevance") or 0.0))),
            title=str(row.get("title") or "Completed learning evidence").strip(),
            target_paths=target_paths,
        )

    @staticmethod
    def _git(
        cwd: Path,
        *args: str,
        require_output: bool = True,
    ) -> str:
        result = subprocess.run(
            ("git", *args),
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(args)}")
        if require_output and not output:
            raise RuntimeError(f"Git command returned no output: {' '.join(args)}")
        return output


__all__ = ["EvolutionCandidateGenerationRequestService"]
