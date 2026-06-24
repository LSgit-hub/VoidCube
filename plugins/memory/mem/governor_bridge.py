from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from VoidCube_core.constants import get_VoidCube_home
from systems.body_registry import BodyRegistry, BodySlotMeta
from systems.evolution_boundary import classify_agent_evolution_changes
from systems.governor import GovernorDecisionEngine, GovernorRequest, GovernorResponse
from systems.lifecycle import LifecycleExecutionReport
from systems.runtime_task_profile import derive_runtime_task_profile
from VoidCube_core.utils import atomic_json_write


class MemGovernorRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kind: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    request: Optional[Dict[str, Any]] = None
    slot_meta: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    execution_report: Optional[Dict[str, Any]] = None
    registry: Optional[Dict[str, Any]] = None
    evolution_lineage: Optional[Dict[str, Any]] = None


class MemGovernorBridge:
    """Mem-side governor bridge — wraps the decision engine and persists
    governance history to BOTH the legacy JSONL store AND (optionally) the
    MemAI ``GovernanceEventRepository``.

    When ``governance_repo`` is provided, every recorded event is mirrored
    into the repository so that MemAI's query, failure-sample, and evidence-
    summary APIs can see VoidCube governance data (M-08).
    """

    def __init__(
        self,
        *,
        storage_root: str | Path | None = None,
        engine: GovernorDecisionEngine | None = None,
        governance_repo: Any | None = None,
    ) -> None:
        root = Path(storage_root) if storage_root else get_VoidCube_home() / "soul"
        self.storage_root = root.resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.history_path = self.storage_root / "governor_history.jsonl"
        self.latest_path = self.storage_root / "governor_latest.json"
        if engine is not None:
            self._engine = engine
        else:
            try:
                from systems.governor import LLMGovernorReasoner
                reasoner = LLMGovernorReasoner()
            except Exception:
                reasoner = None
            self._engine = GovernorDecisionEngine(llm_reasoner=reasoner)
        self._lock = threading.Lock()
        # Auto-create a default GovernanceEventRepository when none provided.
        # Per M-03: the repository should not be an optional dependency that
        # defaults to None — it should always be available for governance writes.
        if governance_repo is not None:
            self._governance_repo = governance_repo
        else:
            try:
                from memai.governance_repository import GovernanceEventRepository
                repo_path = self.storage_root / "mem_governance.jsonl"
                self._governance_repo = GovernanceEventRepository(str(repo_path))
            except Exception:
                self._governance_repo = None

    def review(
        self,
        request: GovernorRequest,
        *,
        slot_meta: BodySlotMeta | None = None,
    ) -> GovernorResponse:
        response = self._engine.evaluate(request, slot_meta=slot_meta)
        self._record(
            MemGovernorRecord(
                kind="review",
                request=self._build_request_payload(request),
                slot_meta=slot_meta.model_dump(mode="json") if slot_meta else None,
                response=response.model_dump(mode="json"),
                evolution_lineage=self._extract_evolution_lineage(
                    request=request,
                    slot_meta=slot_meta,
                ),
            )
        )
        return response

    def record_execution_outcome(
        self,
        *,
        request: GovernorRequest,
        response: GovernorResponse,
        execution_report: LifecycleExecutionReport,
        registry: BodyRegistry | None = None,
    ) -> None:
        self._record(
            MemGovernorRecord(
                kind="execution_outcome",
                request=self._build_request_payload(request),
                response=response.model_dump(mode="json"),
                execution_report=execution_report.model_dump(mode="json"),
                registry=registry.model_dump(mode="json") if registry else None,
                evolution_lineage=self._extract_evolution_lineage(
                    request=request,
                    registry=registry,
                ),
            )
        )

    def record_boundary_defer(
        self,
        *,
        task_id: str,
        trace_id: str | None,
        task_type: str | None,
        governance_task_type: str | None,
        task_family: str | None,
        execution_kind: str | None,
        decision_id: str | None,
        title: str,
        body_id: str | None,
        source_actor: str,
        reason: str,
        git_lineage: Dict[str, Any],
        evolution_boundary: Dict[str, Any],
    ) -> None:
        self._record(
            MemGovernorRecord(
                kind="boundary_defer",
                request={
                    "task_id": task_id,
                    "trace_id": trace_id,
                    "task_type": task_type,
                    "governance_task_type": governance_task_type,
                    "task_family": task_family,
                    "execution_kind": execution_kind,
                    "decision_id": decision_id,
                    "title": title,
                    "event_type": "self_evolution_boundary_defer",
                    "body_id": body_id,
                    "source_actor": source_actor,
                    "summary": reason,
                },
                response={
                    "decision": "defer",
                    "reason": reason,
                    "boundary_ok": evolution_boundary.get("ok"),
                    "violations": list(evolution_boundary.get("violations") or []),
                },
                evolution_lineage={
                    "body_id": body_id,
                    "trace_id": trace_id,
                    "task_type": task_type,
                    "governance_task_type": governance_task_type,
                    "task_family": task_family,
                    "execution_kind": execution_kind,
                    "decision_id": decision_id,
                    "event_type": "self_evolution_boundary_defer",
                    "source_actor": source_actor,
                    "source_branch": git_lineage.get("source_branch"),
                    "source_commit": git_lineage.get("source_commit"),
                    "candidate_branch": git_lineage.get("candidate_branch"),
                    "candidate_commit": git_lineage.get("candidate_commit"),
                    "active_ref": git_lineage.get("active_ref"),
                    "rollback_ref": git_lineage.get("rollback_ref"),
                    "rollback_commit": git_lineage.get("rollback_commit"),
                    "diff_summary": git_lineage.get("diff_summary", ""),
                    "changed_files": list(git_lineage.get("changed_files") or []),
                    "evolution_boundary": evolution_boundary,
                    "probe_report_ref": git_lineage.get("probe_report_ref"),
                    "active_slot": None,
                    "retired_slot": None,
                },
            )
        )

    def record_supervisor_activity(
        self,
        *,
        event: Dict[str, Any],
    ) -> None:
        metadata = dict(event.get("metadata") or {})
        self._record(
            MemGovernorRecord(
                kind="supervisor_activity",
                request={
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "scene": event.get("scene"),
                    "summary": event.get("summary"),
                    "trace_id": metadata.get("trace_id"),
                    "task_id": metadata.get("task_id"),
                    "task_type": metadata.get("task_type"),
                    "governance_task_type": metadata.get("governance_task_type"),
                    "task_family": metadata.get("task_family"),
                    "execution_kind": metadata.get("execution_kind"),
                    "decision_id": metadata.get("decision_id"),
                    "source_actor": metadata.get("source_actor") or "supervisor",
                },
                response={
                    "recorded": True,
                    "source": "supervisor_ui_activity",
                },
                evolution_lineage={
                    "trace_id": metadata.get("trace_id"),
                    "task_type": metadata.get("task_type"),
                    "governance_task_type": metadata.get("governance_task_type"),
                    "task_family": metadata.get("task_family"),
                    "execution_kind": metadata.get("execution_kind"),
                    "decision_id": metadata.get("decision_id"),
                    "event_type": event.get("event_type"),
                    "source_actor": metadata.get("source_actor") or "supervisor",
                },
            )
        )

    def list_history(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.history_path.exists():
            return []
        rows: list[Dict[str, Any]] = []
        with self.history_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        if limit > 0:
            rows = rows[-limit:]
        return rows

    def get_latest(self) -> Optional[Dict[str, Any]]:
        if not self.latest_path.exists():
            return None
        return json.loads(self.latest_path.read_text(encoding="utf-8"))

    def _extract_evolution_lineage(
        self,
        *,
        request: GovernorRequest,
        slot_meta: BodySlotMeta | None = None,
        registry: BodyRegistry | None = None,
    ) -> Optional[Dict[str, Any]]:
        evidence = dict(request.evidence or {})
        runtime_task_profile = self._normalized_runtime_task_profile(request)
        git_lineage = dict(evidence.get("git_lineage") or {})
        probe_report = evidence.get("probe_report")
        if isinstance(probe_report, dict):
            for key in (
                "source_branch",
                "source_commit",
                "candidate_branch",
                "candidate_commit",
                "active_ref",
                "rollback_ref",
                "rollback_commit",
                "diff_summary",
                "changed_files",
                "probe_report_ref",
            ):
                if key in probe_report and key not in git_lineage:
                    git_lineage[key] = probe_report[key]

        if slot_meta is not None:
            slot_payload = slot_meta.model_dump(mode="json")
            for key in (
                "source_branch",
                "source_commit",
                "candidate_branch",
                "candidate_commit",
                "active_ref",
                "rollback_ref",
                "rollback_commit",
                "diff_summary",
                "changed_files",
            ):
                if slot_payload.get(key) and key not in git_lineage:
                    git_lineage[key] = slot_payload[key]

        lineage = {
            "body_id": request.body_id,
            "trace_id": request.trace_id,
            "task_type": request.task_type,
            "governance_task_type": runtime_task_profile.get("governance_task_type"),
            "task_family": runtime_task_profile.get("task_family"),
            "execution_kind": runtime_task_profile.get("execution_kind"),
            "decision_id": request.decision_id,
            "event_type": request.event_type,
            "source_actor": request.source_actor,
            "source_branch": git_lineage.get("source_branch"),
            "source_commit": git_lineage.get("source_commit"),
            "candidate_branch": git_lineage.get("candidate_branch"),
            "candidate_commit": git_lineage.get("candidate_commit"),
            "active_ref": git_lineage.get("active_ref"),
            "rollback_ref": git_lineage.get("rollback_ref"),
            "rollback_commit": git_lineage.get("rollback_commit"),
            "diff_summary": git_lineage.get("diff_summary", ""),
            "changed_files": list(git_lineage.get("changed_files") or []),
            "probe_report_ref": git_lineage.get("probe_report_ref") or evidence.get("probe_report_ref"),
            "active_slot": registry.active_slot if registry else None,
            "retired_slot": registry.retired_slot if registry else None,
        }
        if git_lineage:
            boundary_report = classify_agent_evolution_changes(git_lineage.get("changed_files") or [])
            lineage["evolution_boundary"] = boundary_report.model_dump()
        else:
            lineage["evolution_boundary"] = None
        return lineage

    def _build_request_payload(self, request: GovernorRequest) -> Dict[str, Any]:
        payload = request.model_dump(mode="json")
        runtime_task_profile = self._normalized_runtime_task_profile(request)
        for key in ("governance_task_type", "task_family", "execution_kind"):
            value = runtime_task_profile.get(key)
            if value is not None:
                payload[key] = value
        return payload

    def _normalized_runtime_task_profile(self, request: GovernorRequest) -> Dict[str, Any]:
        runtime_task_profile = dict(request.evidence.get("runtime_task_profile") or {})
        return {
            "task_type": request.task_type,
            **derive_runtime_task_profile(
                task_type=request.task_type,
                governance_task_type=runtime_task_profile.get("governance_task_type"),
                task_family=runtime_task_profile.get("task_family"),
                execution_kind=runtime_task_profile.get("execution_kind"),
                default_task_family="general_self_evolution",
            ),
        }

    def _record(self, record: MemGovernorRecord) -> None:
        payload = record.model_dump(mode="json")
        with self._lock:
            with self.history_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            atomic_json_write(self.latest_path, payload)
        # Mirror into MemAI GovernanceEventRepository when available
        if self._governance_repo is not None:
            try:
                gov_event = self._to_governance_event(record)
                self._governance_repo.append(gov_event)
            except Exception:
                pass  # best-effort mirror; legacy JSONL is the authoritative store

    @staticmethod
    def _to_governance_event(record: MemGovernorRecord) -> Any:
        """Map a legacy ``MemGovernorRecord`` into a MemAI ``GovernanceEvent``.

        Field mapping follows the governance event schema (M-01) so that
        the repository's query / failure-sample / evidence-summary APIs
        can consume VoidCube governance data without schema translation.
        """
        from memai.governance import (
            GovernanceEvent,
            GovernanceEventType,
            GovernanceDecision,
        )
        req = record.request or {}
        resp = record.response or {}
        lineage = record.evolution_lineage or {}

        # ── Event type mapping (all 13 GovernanceEventType values) ──
        kind_map = {
            "review": GovernanceEventType.CANDIDATE_REVIEW,
            "execution_outcome": GovernanceEventType.EXECUTION_OUTCOME,
            "boundary_defer": GovernanceEventType.BOUNDARY_DEFER,
            "supervisor_activity": GovernanceEventType.CANDIDATE_REVIEW,
            # Additional kinds used by lifecycle writeback
            "probe_pass": GovernanceEventType.PROBE_APPROVAL,
            "probe_failure": GovernanceEventType.PROBE_FAILURE,
            "body_switch_approved": GovernanceEventType.SWITCH_APPROVAL,
            "body_switch_rejected": GovernanceEventType.SWITCH_REJECTION,
            "watch_window_pass": GovernanceEventType.WATCH_WINDOW_PASS,
            "watch_window_rollback": GovernanceEventType.WATCH_WINDOW_ROLLBACK,
            "self_evolution_approved": GovernanceEventType.SELF_EVOLUTION_APPROVAL,
            "self_evolution_deferred": GovernanceEventType.SELF_EVOLUTION_DEFER,
            "self_evolution_cancelled": GovernanceEventType.SELF_EVOLUTION_CANCEL,
            "rollback_outcome": GovernanceEventType.ROLLBACK_OUTCOME,
            "memory_maintenance": GovernanceEventType.MEMORY_MAINTENANCE,
        }
        event_type = kind_map.get(record.kind, GovernanceEventType.EXECUTION_OUTCOME)

        # ── Decision mapping (all 9 GovernanceDecision values) ──
        decision_raw = resp.get("decision", "")
        decision_map = {
            "approved": GovernanceDecision.APPROVE,
            "approved_for_execution": GovernanceDecision.APPROVE,
            "approved_with_watch": GovernanceDecision.APPROVE_WITH_WATCH,
            "defer": GovernanceDecision.DEFER,
            "deferred": GovernanceDecision.DEFER,
            "reject": GovernanceDecision.REJECT,
            "cancelled": GovernanceDecision.CANCEL,
            "paused": GovernanceDecision.PAUSE,
            "rollback": GovernanceDecision.ROLLBACK_REQUIRED,
            "rollback_failed": GovernanceDecision.ROLLBACK_REQUIRED,
            "completed": GovernanceDecision.COMPLETED,
            "failed": GovernanceDecision.FAILED,
            "record_only": GovernanceDecision.RECORD_ONLY,
        }
        decision = decision_map.get(
            str(decision_raw).lower(), GovernanceDecision.RECORD_ONLY
        )

        from memai.governance import GovernanceGitLineage, GovernanceBoundaryReport

        # Build structured git lineage
        gl = GovernanceGitLineage(
            source_branch=lineage.get("source_branch") or None,
            source_commit=lineage.get("source_commit") or None,
            candidate_branch=lineage.get("candidate_branch") or None,
            candidate_commit=lineage.get("candidate_commit") or None,
            active_ref=lineage.get("active_ref") or None,
            rollback_ref=lineage.get("rollback_ref") or None,
            rollback_commit=lineage.get("rollback_commit") or None,
            changed_files=list(lineage.get("changed_files") or []),
        )

        # Build evolution boundary report if present
        boundary_raw = lineage.get("evolution_boundary") or {}
        boundary = GovernanceBoundaryReport(
            ok=bool(boundary_raw.get("ok", True)),
            violations=list(boundary_raw.get("violations") or []),
        ) if boundary_raw else None

        return GovernanceEvent.create(
            event_type=event_type,
            decision=decision,
            task_id=str(req.get("task_id") or record.record_id),
            body_id=str(
                lineage.get("body_id")
                or req.get("body_id")
                or lineage.get("active_slot")
                or ""
            ),
            source_actor=str(
                lineage.get("source_actor") or req.get("source_actor") or "supervisor"
            ),
            reason=str(resp.get("reason") or record.kind),
            git_lineage=gl,
            evolution_boundary=boundary,
            probe_report_ref=str(lineage.get("probe_report_ref") or ""),
            execution_result=dict(record.execution_report or {}),
        )
