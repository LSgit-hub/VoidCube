"""Governed review boundary for body-improvement reports."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

from .evolution_evaluation_governance import (
    validate_body_improvement_authorization_binding,
)
from systems.evolution_evaluation.models import ExecutionEnvironmentManifest


logger = logging.getLogger("supervisor")


class BodyImprovementReviewService:
    """Validate, score, and persist body-improvement review outcomes."""

    def __init__(
        self,
        *,
        body_registry: Any,
        task_store: Any,
        task_profile_policy: Any,
        execution_facade_provider: Callable[[], Any],
        evaluation_governance_verifier: Any,
    ) -> None:
        self._body_registry = body_registry
        self._autonomous_chain_store = task_store
        self._task_profile_policy = task_profile_policy
        self._execution_facade_provider = execution_facade_provider
        self._evaluation_governance_verifier = evaluation_governance_verifier


    def _calc_file_repeat_penalty(self, slot_id: str, changed_files: list[str]) -> float:
        penalty = 0.0
        try:
            meta = self._body_registry.load_slot_meta(slot_id)
        except (FileNotFoundError, ValueError):
            return penalty

        file_change_counts: dict[str, int] = {}
        for history in meta.health_history:
            if history.get("reason") == "time_decay":
                continue
            report_files = history.get("changed_files", [])
            for f in report_files:
                file_change_counts[f] = file_change_counts.get(f, 0) + 1

        for f in changed_files:
            count = file_change_counts.get(f, 0)
            if count > 0:
                penalty += count * 5.0

        return penalty

    def _calc_learning_freshness(self, learning_refs: list[dict[str, Any]]) -> float:
        if not learning_refs:
            return 0.0

        now = datetime.now(timezone.utc)
        total_freshness = 0.0

        for ref in learning_refs:
            if not isinstance(ref, dict):
                continue
            try:
                timestamp = str(
                    ref.get("timestamp")
                    or ref.get("created_at")
                    or ref.get("completed_at")
                    or ""
                ).strip()
                if not timestamp:
                    continue
                learned_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if learned_at.tzinfo is None:
                    learned_at = learned_at.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - learned_at).total_seconds() / 86400.0)
                freshness = max(0.0, 1.0 - age_days / 90.0)
                relevance = max(0.0, min(1.0, float(ref.get("relevance", 1.0))))
                total_freshness += freshness * relevance
            except (TypeError, ValueError, OverflowError):
                continue

        avg_freshness = total_freshness / len(learning_refs)
        return avg_freshness * 20.0

    def _inspect_body_improvement_commit(
        self,
        *,
        worktree_path: str,
        baseline_commit: str,
        commit_hash: str,
    ) -> Dict[str, Any]:
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", baseline_commit):
            return {"ok": False, "reject_reason": "invalid_baseline_commit"}
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit_hash):
            return {"ok": False, "reject_reason": "invalid_commit_hash"}
        if not worktree_path or not Path(worktree_path).is_dir():
            return {"ok": False, "reject_reason": "worktree_not_found"}

        try:
            resolved = subprocess.run(
                ["git", "rev-parse", "--verify", f"{commit_hash}^{{commit}}"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            baseline = subprocess.run(
                ["git", "rev-parse", "--verify", f"{baseline_commit}^{{commit}}"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if resolved.returncode != 0:
                return {"ok": False, "reject_reason": "commit_not_found"}
            if baseline.returncode != 0:
                return {"ok": False, "reject_reason": "baseline_commit_not_found"}

            head = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            resolved_commit = resolved.stdout.strip().lower()
            resolved_baseline = baseline.stdout.strip().lower()
            if head.returncode != 0 or head.stdout.strip().lower() != resolved_commit:
                return {"ok": False, "reject_reason": "commit_is_not_worktree_head"}

            worktree_status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if worktree_status.returncode != 0:
                return {"ok": False, "reject_reason": "worktree_status_unavailable"}
            if worktree_status.stdout.strip():
                return {
                    "ok": False,
                    "reject_reason": "worktree_not_clean",
                }

            ancestry = subprocess.run(
                ["git", "merge-base", "--is-ancestor", resolved_baseline, resolved_commit],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if ancestry.returncode != 0:
                return {"ok": False, "reject_reason": "baseline_not_ancestor"}

            changed = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    f"{resolved_baseline}..{resolved_commit}",
                    "--",
                ],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if changed.returncode != 0:
                return {"ok": False, "reject_reason": "commit_diff_unavailable"}

            from ..evolution_boundary import normalize_repo_path

            changed_files = [
                normalized
                for line in changed.stdout.splitlines()
                if (normalized := normalize_repo_path(line))
            ]
            diff = subprocess.run(
                ["git", "diff", "--stat", f"{resolved_baseline}..{resolved_commit}", "--"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return {
                "ok": True,
                "changed_files": list(dict.fromkeys(changed_files)),
                "diff_text": diff.stdout if diff.returncode == 0 else "",
            }
        except (OSError, subprocess.SubprocessError):
            logger.warning(
                "Failed to inspect body improvement commit %s in %s",
                commit_hash,
                worktree_path,
                exc_info=True,
            )
            return {"ok": False, "reject_reason": "commit_inspection_failed"}

    def _get_probe_score(self, slot_id: str, slot_meta) -> float:
        if slot_meta.last_probe_result:
            probe = slot_meta.last_probe_result
            if probe.get("overall_passed") is False:
                return 0.0
            checks_total = len(probe.get("checks", []))
            checks_passed = sum(1 for c in probe.get("checks", []) if c.get("passed"))
            if checks_total > 0:
                return (checks_passed / checks_total) * 20.0

        parent_slot_id = str(slot_meta.materialized_from or "").removeprefix("slot:")
        if parent_slot_id in set(self._body_registry.slot_ids):
            try:
                parent_meta = self._body_registry.load_slot_meta(parent_slot_id)
                if parent_meta.last_probe_result:
                    probe = parent_meta.last_probe_result
                    if probe.get("overall_passed") is False:
                        return 0.0
                    checks_total = len(probe.get("checks", []))
                    checks_passed = sum(1 for c in probe.get("checks", []) if c.get("passed"))
                    if checks_total > 0:
                        return (checks_passed / checks_total) * 15.0
            except (FileNotFoundError, ValueError):
                pass

        return 10.0

    @staticmethod
    def _calc_stability_factor(slot_meta) -> float:
        baseline_at = slot_meta.runtime_bootstrapped_at or slot_meta.last_materialized_at
        if baseline_at is None:
            return 0.0
        try:
            if not isinstance(baseline_at, datetime):
                baseline_at = datetime.fromisoformat(str(baseline_at).replace("Z", "+00:00"))
            if baseline_at.tzinfo is None:
                baseline_at = baseline_at.replace(tzinfo=timezone.utc)
            stable_days = max(
                0.0,
                (datetime.now(timezone.utc) - baseline_at).total_seconds() / 86400.0,
            )
            return min(20.0, stable_days / 30.0 * 20.0)
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _apply_cumulative_decay(self, slot_meta) -> None:
        if slot_meta.decay_applied_at is None:
            slot_meta.decay_applied_at = datetime.now(timezone.utc).isoformat()
            return

        try:
            last_decay = datetime.fromisoformat(slot_meta.decay_applied_at)
            if last_decay.tzinfo is None:
                last_decay = last_decay.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            slot_meta.decay_applied_at = datetime.now(timezone.utc).isoformat()
            return

        now = datetime.now(timezone.utc)
        days_since_decay = (now - last_decay).days

        if days_since_decay <= 0:
            return

        if slot_meta.last_improvement_at is None:
            days_since_improvement = days_since_decay
        else:
            try:
                last_improvement = datetime.fromisoformat(slot_meta.last_improvement_at)
                if last_improvement.tzinfo is None:
                    last_improvement = last_improvement.replace(tzinfo=timezone.utc)
                days_since_improvement = (now - last_improvement).days
            except (TypeError, ValueError, OverflowError):
                days_since_improvement = days_since_decay

        if days_since_improvement <= 30:
            total_decay = 0.0
        elif days_since_improvement <= 90:
            daily_decay = ((days_since_improvement - 30) / 60) * 2.0
            total_decay = daily_decay * min(days_since_decay, days_since_improvement - 30)
        else:
            total_decay = 2.0 * days_since_decay

        slot_meta.health_score = max(0.0, slot_meta.health_score - total_decay)
        slot_meta.decay_applied_at = now.isoformat()

        if total_decay > 0:
            slot_meta.health_history.append({
                "score_delta": -total_decay,
                "reason": "time_decay",
                "reviewed_at": now.isoformat(),
            })

    async def _llm_review_diff(
        self,
        diff_text: str,
        description: str,
        learning_refs: list[dict[str, Any]],
    ) -> float:
        learning_context = json.dumps(learning_refs, ensure_ascii=False)

        prompt = (
            f"评估以下替身 Agent 的代码改进质量（0-20分）。\n\n"
            f"【Agent 自述】{description}\n"
            f"【引用的学习成果】{learning_context}\n"
            f"【代码 Diff】\n{diff_text[:3000]}\n\n"
            f"评分维度：\n"
            f"- 改动是否实质性（非格式化/非注释修改）\n"
            f"- 改动是否有学习成果支撑\n"
            f"- 改动是否在合理范围内（非破坏性变更）\n"
            f"- 代码质量是否提升\n"
            f"输出JSON: {{\"score\": 0-20, \"reason\": \"...\"}}"
        )

        try:
            from memai.model_config import resolve_mem_llm_client
            llm_client, _ = resolve_mem_llm_client(role="governance_reasoner")
            if llm_client is None:
                return 10.0  # no LLM → default score
            result = llm_client.complete_json(
                system_prompt="你是代码审查专家。客观评估代码改进质量。",
                user_payload={"task": prompt},
                task="scholar.revision",
            )
            if isinstance(result, dict):
                return float(result.get("score", 10))
        except Exception:
            pass

        return 10.0
    async def review(self, report):
        if hasattr(report, "model_dump"):
            report_dict = report.model_dump()
        elif isinstance(report, dict):
            report_dict = report
        else:
            return {"score_delta": 0, "reject_reason": "invalid_report_type"}

        slot_id = report_dict.get("slot_id")
        if not slot_id:
            return {"score_delta": 0, "reject_reason": "missing_slot_id"}

        changed_files = report_dict.get("changed_files", [])
        baseline_commit = report_dict.get("baseline_commit")
        commit_hash = report_dict.get("commit_hash")

        if not changed_files or not baseline_commit or not commit_hash:
            return {"score_delta": 0, "reject_reason": "empty_improvement"}

        try:
            execution_environment = ExecutionEnvironmentManifest.model_validate(
                report_dict.get("execution_environment")
            )
        except Exception:
            return {
                "score_delta": 0,
                "reject_reason": "execution_environment_manifest_invalid",
            }
        if execution_environment.validation_scope != "container":
            return {
                "score_delta": 0,
                "reject_reason": "body_task_container_environment_required",
            }

        task_id = str(report_dict.get("task_id") or "").strip()
        governed_task = self._autonomous_chain_store.get_task(task_id)
        if governed_task is None:
            return {"score_delta": 0, "reject_reason": "governed_task_not_found"}
        if self._task_profile_policy.execution_kind(governed_task) != "body_improvement":
            return {"score_delta": 0, "reject_reason": "governed_task_kind_mismatch"}
        governed_constraints = dict(governed_task.constraints or {})
        governed_evidence = dict(governed_task.evidence or {})
        governed_slot_id = str(
            governed_constraints.get("target_slot_id") or ""
        ).strip()
        if governed_slot_id != str(slot_id).strip():
            return {"score_delta": 0, "reject_reason": "governed_slot_mismatch"}

        try:
            slot_meta = self._body_registry.load_slot_meta(slot_id)
        except (FileNotFoundError, ValueError):
            return {"score_delta": 0, "reject_reason": "slot_not_found"}

        governed_worktree = str(
            governed_constraints.get("worktree_path") or ""
        ).strip()
        try:
            worktree_matches = (
                bool(governed_worktree)
                and Path(governed_worktree).resolve()
                == Path(slot_meta.worktree_path).resolve()
            )
        except (OSError, ValueError):
            worktree_matches = False
        if not worktree_matches:
            return {"score_delta": 0, "reject_reason": "governed_worktree_mismatch"}

        commit_hash = str(commit_hash).strip()
        duplicate_report = next(
            (
                entry
                for entry in slot_meta.health_history
                if entry.get("reason") == "body_improvement"
                and (
                    (task_id and str(entry.get("task_id") or "") == task_id)
                    or str(entry.get("commit_hash") or "") == commit_hash
                )
            ),
            None,
        )
        if duplicate_report is not None:
            return {
                "score_delta": 0,
                "health_score": slot_meta.health_score,
                "improvement_count": slot_meta.improvement_count,
                "duplicate": True,
                "original_reviewed_at": duplicate_report.get("reviewed_at"),
            }
        if str(governed_task.status or "").strip().lower() != "running":
            return {
                "score_delta": 0,
                "reject_reason": "governed_task_not_running",
            }

        self._apply_cumulative_decay(slot_meta)

        commit_inspection = self._inspect_body_improvement_commit(
            worktree_path=str(slot_meta.worktree_path or ""),
            baseline_commit=str(baseline_commit),
            commit_hash=commit_hash,
        )
        if not commit_inspection.get("ok"):
            return {
                "score_delta": 0,
                "reject_reason": commit_inspection.get("reject_reason")
                or "commit_inspection_failed",
            }

        experiment_result_id = str(
            governed_evidence.get("experiment_result_id") or ""
        ).strip()
        evaluation_authorization = self._evaluation_governance_verifier.verify(
            experiment_result_id
        )
        authorization_binding = validate_body_improvement_authorization_binding(
            evidence=governed_evidence,
            constraints=governed_constraints,
            authorization=evaluation_authorization,
            actual_commit=commit_hash,
            actual_baseline_commit=str(baseline_commit),
        )
        if not authorization_binding.get("valid"):
            return {
                "score_delta": 0,
                "reject_reason": authorization_binding.get("reason")
                or "evaluation_authorization_invalid",
            }

        from ..evolution_boundary import (
            classify_agent_evolution_changes,
            normalize_repo_path,
        )

        actual_changed_files = list(commit_inspection.get("changed_files") or [])
        declared_changed_files = [
            normalized
            for path in changed_files
            if (normalized := normalize_repo_path(str(path)))
        ]
        if set(actual_changed_files) != set(declared_changed_files):
            return {
                "score_delta": 0,
                "reject_reason": "changed_files_mismatch",
                "actual_changed_files": actual_changed_files,
            }
        approved_target_paths = {
            normalize_repo_path(str(path))
            for path in list(governed_constraints.get("target_paths") or [])
            if normalize_repo_path(str(path))
        }
        if not approved_target_paths:
            return {
                "score_delta": 0,
                "reject_reason": "governed_target_paths_missing",
            }
        if not set(actual_changed_files).issubset(approved_target_paths):
            return {
                "score_delta": 0,
                "reject_reason": "changed_files_outside_governed_targets",
                "approved_target_paths": sorted(approved_target_paths),
                "actual_changed_files": actual_changed_files,
            }
        max_files_changed = max(
            1,
            int(governed_constraints.get("max_files_changed") or 5),
        )
        if len(actual_changed_files) > max_files_changed:
            return {
                "score_delta": 0,
                "reject_reason": "changed_files_limit_exceeded",
                "max_files_changed": max_files_changed,
            }

        boundary = classify_agent_evolution_changes(actual_changed_files)
        if not boundary.ok:
            return {
                "score_delta": 0,
                "reject_reason": "evolution_boundary_violation",
                "evolution_boundary": boundary.model_dump(),
            }
        evaluated_changed_files = [
            normalized
            for path in list(evaluation_authorization.get("changed_files") or [])
            if (normalized := normalize_repo_path(str(path)))
        ]
        if set(actual_changed_files) != set(evaluated_changed_files):
            return {
                "score_delta": 0,
                "reject_reason": "evaluated_changed_files_mismatch",
                "evaluated_changed_files": evaluated_changed_files,
                "actual_changed_files": actual_changed_files,
            }
        boundary_score = boundary.score

        file_penalty = self._calc_file_repeat_penalty(slot_id, actual_changed_files)

        learning_refs = [
            dict(ref)
            for ref in list(governed_evidence.get("learning_refs") or [])
            if isinstance(ref, dict)
        ]
        if not learning_refs:
            return {
                "score_delta": 0,
                "reject_reason": "governed_learning_refs_missing",
            }
        reported_learning_ids = {
            str(ref.get("mem_id") or "").strip()
            for ref in list(report_dict.get("learning_refs") or [])
            if isinstance(ref, dict) and str(ref.get("mem_id") or "").strip()
        }
        governed_learning_ids = {
            str(ref.get("mem_id") or "").strip()
            for ref in learning_refs
            if str(ref.get("mem_id") or "").strip()
        }
        if reported_learning_ids and reported_learning_ids != governed_learning_ids:
            return {
                "score_delta": 0,
                "reject_reason": "learning_refs_mismatch",
            }
        learning_freshness = self._calc_learning_freshness(learning_refs)

        probe_score = self._get_probe_score(slot_id, slot_meta)
        stability_factor = self._calc_stability_factor(slot_meta)

        llm_score = await self._llm_review_diff(
            str(commit_inspection.get("diff_text") or ""),
            report_dict.get("improvement_description", ""),
            learning_refs,
        )

        score_components = {
            "llm_diff_quality": round(llm_score, 4),
            "evolution_boundary": round(boundary_score, 4),
            "learning_freshness": round(learning_freshness, 4),
            "probe_pass": round(probe_score, 4),
            "stability": round(stability_factor, 4),
            "file_repeat_penalty": round(file_penalty, 4),
        }
        score_delta = (
            llm_score * 0.35
            + boundary_score * 0.20
            + learning_freshness * 0.15
            + probe_score * 0.25
            + stability_factor * 0.05
            - file_penalty
        )
        score_delta = max(-20.0, min(30.0, score_delta))

        if score_delta > 0 and slot_meta.health_score < 100:
            slot_meta.health_score = min(100.0, slot_meta.health_score + score_delta)
        elif score_delta < 0:
            slot_meta.health_score = max(0.0, slot_meta.health_score + score_delta)

        now = datetime.now(timezone.utc)
        slot_meta.health_history.append(
            {
                "score_delta": score_delta,
                "reason": "body_improvement",
                "task_id": task_id,
                "baseline_commit": str(baseline_commit),
                "commit_hash": commit_hash,
                "reviewed_at": now.isoformat(),
                "changed_files": actual_changed_files,
                "evolution_boundary": boundary.model_dump(),
                "score_components": score_components,
            }
        )
        slot_meta.improvement_count += 1
        slot_meta.last_improvement_at = now.isoformat()

        if score_delta > 0:
            prior_healthy_commit = str(
                slot_meta.current_healthy_commit
                or slot_meta.previous_healthy_commit
                or baseline_commit
            ).strip()
            if prior_healthy_commit == commit_hash:
                prior_healthy_commit = str(baseline_commit).strip()
            slot_meta.previous_healthy_commit = prior_healthy_commit or None
            slot_meta.current_healthy_commit = commit_hash
            slot_meta.candidate_commit = commit_hash
            slot_meta.build_from_commit = commit_hash

        self._body_registry.save_slot_meta(slot_meta)

        active_slot = self._body_registry.get_active_slot()
        active_health = active_slot.health_score if active_slot else 0.0

        switch_suggestion = None
        if slot_meta.health_score > active_health:
            switch_suggestion = self._emit_switch_suggestion_event(
                slot_id,
                active_health_score=active_health,
            )

        return {
            "score_delta": score_delta,
            "health_score": slot_meta.health_score,
            "improvement_count": slot_meta.improvement_count,
            "evolution_boundary": boundary.model_dump(),
            "score_components": score_components,
            "switch_suggestion": switch_suggestion,
        }

    def _emit_switch_suggestion_event(
        self,
        slot_id: str,
        *,
        active_health_score: float,
    ) -> Dict[str, Any]:
        from systems.governor import GovernorRequest

        slot_meta = self._body_registry.load_slot_meta(slot_id)
        request = GovernorRequest(
            request_id=str(uuid.uuid4()),
            trace_id=str(uuid.uuid4()),
            event_type="switch_suggestion",
            body_id=slot_id,
            source_actor="supervisor_body_improvement_review",
            summary="Body improvement health score surpassed the active slot.",
            evidence={
                "health_score": slot_meta.health_score,
                "improvement_count": slot_meta.improvement_count,
                "active_health_score": active_health_score,
                "previous_healthy_commit": slot_meta.previous_healthy_commit,
            },
        )
        return self._execution_facade_provider().review_body(request)

__all__ = ["BodyImprovementReviewService"]
