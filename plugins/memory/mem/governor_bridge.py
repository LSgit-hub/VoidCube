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
    """Minimal Mem-side governor bridge.

    This bridge wraps the deterministic governor engine but persists the
    governance history in a soul-side store so the supervisor no longer talks
    directly to the decision engine without leaving memory traces.
    """

    def __init__(
        self,
        *,
        storage_root: str | Path | None = None,
        engine: GovernorDecisionEngine | None = None,
    ) -> None:
        root = Path(storage_root) if storage_root else get_VoidCube_home() / "soul"
        self.storage_root = root.resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.history_path = self.storage_root / "governor_history.jsonl"
        self.latest_path = self.storage_root / "governor_latest.json"
        self._engine = engine or GovernorDecisionEngine()
        self._lock = threading.Lock()

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
