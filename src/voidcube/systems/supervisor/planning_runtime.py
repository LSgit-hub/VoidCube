from __future__ import annotations

import asyncio
import logging
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException
import aiohttp

from ..evolution_evaluation import EnvironmentCapabilityPolicy
from .endogenous_candidate_pipeline import CORE_VALUES
from .endogenous_proposal_port import (
    LmGenerationApplicationState,
    project_lm_generation_application_state,
)
from .endogenous_cognition_state import (
    build_cognition_state_projection,
    build_judgement_core_projection,
)
from .endogenous_strategy_projection import (
    build_attention_agenda_projection,
)
from .endogenous_uncertainty_projection import (
    build_uncertainty_ledger_projection,
)
from .endogenous_observation_projection import (
    build_observation_program_entries,
    project_observation_program,
)
from .endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)
from .endogenous_meta_governance import derive_meta_governance_mode
from .endogenous_proposal_cognition import (
    compact_proposal_memory,
    build_proposal_cognition_projection,
)
from .endogenous_drive_orchestration import (
    EndogenousDriveEvaluationContext,
    evaluate_endogenous_drive as run_endogenous_drive_evaluation,
)
from .endogenous_foundation_bridge import (
    EndogenousFoundationReadOnlyProjection,
)
from .endogenous_policy import TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
from .endogenous_state_repository import EndogenousStateRepository
from .endogenous_state_projection import (
    derive_corrective_mode,
    project_governance_event_stream,
)
from .autonomous_chain_store import AutonomousChainTask, StaleExecutionLeaseError
from .activity_projection import (
    enforce_auto_drive_input_boundary,
    idle_seconds_since,
    parse_activity_timestamp,
    project_auto_activity_snapshot,
    project_runtime_observation_input,
)
from .drive_input_evaluation import (
    DriveInputEvaluationConfig,
    evaluate_drive_input_snapshot,
)
from .autonomous_task_review import normalize_autonomous_chain_decision

logger = logging.getLogger("supervisor")


# ──────────────────────────────────────────────────────────────────────
# Supervisor scene taxonomy (baseline §3.4 / §3.6 / §13.2)
# ──────────────────────────────────────────────────────────────────────
# The supervisor (API-B) is the governance identity of Mem.  It only
# manages API-B judgement state and runs endogenous drive; it never executes
# learning or body-upgrade code.  Therefore the supervisor's `scene`
# field is restricted to the values below.  The Agent (员工代理) is the
# only component that may surface "learning" / "execution" scenes.
#
#   idle         - at rest
#   planning     - judging / handing off / denying an API-B judgement item
#   memory       - actively touching long-term memory (Mem internal)
#   drive        - endogenous drive: cognitive evaluation / governance output
#   handoff      - handing a ready execution request to 员工代理 / executor
#   maintenance  - memory-maintenance sweep (long-term memory hygiene)
#   body_switch  - judging a body switch request
#
# Forbidden scenes for the supervisor (员工代理 territory):
#   "learning"   - the Agent is doing learning work
#   "execution"  - the Agent or executor is doing work
# ──────────────────────────────────────────────────────────────────────
SUPERVISOR_LEGAL_SCENES: frozenset[str] = frozenset(
    {"idle", "planning", "memory", "drive", "handoff", "maintenance"}
)

class PlanningRuntimeMixin:
    """Supervisor planning, activity-guard evaluation, and autonomous-chain orchestration."""

    async def get_governor_history(self, limit: int = 20):
        return {
            "history": self._governor.list_history(limit=limit),
            "latest": self._governor.get_latest(),
        }

    @property
    def _endogenous_state_repository(self) -> EndogenousStateRepository:
        repository = getattr(self, "_endogenous_state_repository_instance", None)
        if repository is None:
            runtime_root = getattr(self, "_runtime_root", None) or self.config.soul_store_path
            repository = EndogenousStateRepository(runtime_root)
            self._endogenous_state_repository_instance = repository
        return repository

    @_endogenous_state_repository.setter
    def _endogenous_state_repository(self, repository: EndogenousStateRepository) -> None:
        self._endogenous_state_repository_instance = repository

    def _lm_generation_application_state(self) -> LmGenerationApplicationState:
        engine = getattr(self, "_endogenous_drive_engine", None)
        return project_lm_generation_application_state(
            runtime_config=getattr(self.config, "service_runtime", None),
            state_loader=getattr(engine, "get_latest_lm_task_generation_state", None),
        )

    def _load_evolution_foundation_projection(self) -> Dict[str, Any]:
        runtime_root = getattr(self, "_runtime_root", None) or self.config.soul_store_path
        capability_policy = EnvironmentCapabilityPolicy.for_profile(
            self.config.service_runtime.evolution_capability_policy_profile
        )
        return EndogenousFoundationReadOnlyProjection.from_root(
            Path(runtime_root) / "evolution-foundation",
            capability_policy=capability_policy,
        ).load()
    @staticmethod
    def _clamp_endogenous_ratio(value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, numeric))

    @staticmethod
    def _project_drive_input_snapshot(source_payload: Dict[str, Any]) -> Dict[str, Any]:
        return dict(source_payload or {})

    def _normalize_runtime_decision_context(
        self,
        context: Optional[Dict[str, Any]] = None,
        *,
        drive_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized = dict(context or {})
        context_drive_input = normalized.get("drive_input")
        effective_drive_input = dict(
            drive_input
            or (context_drive_input if isinstance(context_drive_input, dict) else {})
            or {}
        )
        if effective_drive_input:
            normalized["drive_input"] = effective_drive_input
        normalized.pop("activity_guards", None)
        return normalized

    def _build_drive_input_response_fields(
        self,
        drive_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        normalized = self._normalize_runtime_decision_context(
            drive_input=drive_input,
        )
        response_drive_input = dict(normalized.get("drive_input") or {})
        return {"drive_input": response_drive_input}

    def _drive_input_fields_from_evaluation(
        self,
        evaluation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        evaluation = dict(evaluation or {})
        return self._build_drive_input_response_fields(
            drive_input=dict(evaluation.get("drive_input") or {}),
        )
    @staticmethod
    def _build_drive_input_context_snapshot(source_payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = dict(source_payload or {})
        return {
            "user_mode": payload.get("user_mode"),
            "system_posture": payload.get("system_posture"),
            "active_sessions": payload.get("active_sessions"),
            "correction_signals": payload.get("correction_signals"),
            "api_b_judgement_count": (
                payload.get("api_b_judgement_count")
            ),
            "autonomous_chain_gate_active": bool(payload.get("autonomous_chain_gate_active")),
        }

    async def _resolve_runtime_drive_input_request(
        self,
        request: Dict[str, Any],
        *,
        default_task_family: Optional[str] = None,
        default_execution_kind: Optional[str] = None,
        include_gate_default: bool = False,
    ) -> Dict[str, Any]:
        request = dict(request or {})
        if "activity_guards" in request:
            raise HTTPException(
                status_code=400,
                detail="activity_guards is no longer accepted; use drive_input.",
            )
        default_governance_task_type = None
        if default_task_family is not None:
            default_governance_task_type = self._task_profile_policy.normalize_type(
                default_task_family
            )
        elif default_execution_kind is not None:
            default_governance_task_type = self._task_profile_policy.normalize_type(
                default_execution_kind
            )
        runtime = getattr(self, "_service_runtime", None)
        gate_active = bool(
            getattr(runtime, "autonomous_chain_gate_active", False)
        )
        evidence_packet = dict(
            request.get("evidence_packet")
            or getattr(runtime, "auto_evidence_packet", {})
            or {}
        )
        requested_drive_input = dict(request.get("drive_input") or {})
        if requested_drive_input:
            drive_input = dict(requested_drive_input)
            if default_task_family is not None:
                drive_input.setdefault("task_family", default_task_family)
            if default_execution_kind is not None:
                drive_input.setdefault("execution_kind", default_execution_kind)
            if default_governance_task_type is not None:
                drive_input.setdefault(
                    "governance_task_type",
                    default_governance_task_type,
                )
            if include_gate_default:
                drive_input.setdefault(
                    "autonomous_chain_gate_active",
                    gate_active,
                )
            if include_gate_default and gate_active:
                drive_input = enforce_auto_drive_input_boundary(
                    drive_input,
                    evidence_packet=evidence_packet,
                )
            if request.get("include_memory_maintenance_status") and gate_active:
                drive_input["memory_maintenance_status"] = (
                    await self._fetch_memory_maintenance_status()
                )
            return drive_input

        drive_input_request: Dict[str, Any] = {}
        if default_task_family is not None:
            drive_input_request.setdefault("task_family", default_task_family)
        if default_execution_kind is not None:
            drive_input_request.setdefault("execution_kind", default_execution_kind)
        if default_governance_task_type is not None:
            drive_input_request.setdefault(
                "governance_task_type",
                default_governance_task_type,
            )
        if include_gate_default:
            drive_input_request.setdefault(
                "autonomous_chain_gate_active",
                gate_active,
            )
            if gate_active:
                drive_input_request["perception_scope"] = "autonomous_only"
                drive_input_request["evidence_packet"] = evidence_packet
        drive_input = await self.evaluate_drive_input(drive_input_request)
        if request.get("include_memory_maintenance_status") and gate_active:
            drive_input["memory_maintenance_status"] = (
                await self._fetch_memory_maintenance_status()
            )
        return self._project_drive_input_snapshot(drive_input)

    def _build_endogenous_meta_governance(
        self,
        *,
        cognition_state_seed: Dict[str, Any],
        governance_channels: Dict[str, Any],
        strategy_memory: Dict[str, Any],
        context_key: str,
        self_regulation: Dict[str, Any],
        history: Dict[str, Any],
    ) -> Dict[str, Any]:
        reflection = dict(cognition_state_seed.get("reflection") or {})
        adaptive_policy = dict(cognition_state_seed.get("adaptive_policy") or {})
        attention_agenda = dict(cognition_state_seed.get("attention_agenda") or {})
        uncertainty_ledger = dict(cognition_state_seed.get("uncertainty_ledger") or {})
        observation_program = dict(cognition_state_seed.get("observation_program") or {})
        correction_mode = dict(cognition_state_seed.get("corrective_mode") or {})
        meta_mode = derive_meta_governance_mode(
            attention_agenda=attention_agenda,
            uncertainty_ledger=uncertainty_ledger,
            observation_program=observation_program,
            self_regulation=self_regulation,
            reflection=reflection,
            adaptive_policy=adaptive_policy,
            strategy_memory=strategy_memory,
        )
        context = {
            "preferred_focus": adaptive_policy.get("preferred_focus"),
            "corrective_mode": correction_mode.get("mode"),
            "observation_target": observation_program.get("highest_priority_target"),
            "agenda_topic": (
                attention_agenda.get("entries", [{}])[0].get("topic")
                if attention_agenda.get("entries")
                else None
            ),
            "uncertainty_domain": uncertainty_ledger.get("highest_risk_domain"),
            "context_key": context_key,
        }
        recorded_at = datetime.now(timezone.utc).isoformat()
        self._endogenous_strategy_memory_service.record_meta_governance(
            history,
            mode=meta_mode["mode"],
            priority=meta_mode["confidence"],
            confidence=meta_mode["confidence"],
            context_key=context_key,
            recorded_at=recorded_at,
            status="active",
        )
        self._endogenous_drive_history_persistence_service.persist(history)
        return {
            "summary": (
                f"The endogenous core is operating in {meta_mode['mode']} mode with "
                f"{meta_mode['stability']} stability."
            ),
            "mode": meta_mode["mode"],
            "confidence": meta_mode["confidence"],
            "stability": meta_mode["stability"],
            "drivers": list(meta_mode["drivers"]),
            "guardrails": list(meta_mode["guardrails"]),
            "context": context,
        }

    def _build_endogenous_observation_program(
        self,
        *,
        uncertainty_ledger: Dict[str, Any],
        governance_channels: Dict[str, Any],
        strategy_memory: Optional[Dict[str, Any]],
        history: Dict[str, Any],
        context_key: str,
    ) -> Dict[str, Any]:
        entries_seed = build_observation_program_entries(
            uncertainty_ledger=uncertainty_ledger,
            governance_channels=governance_channels,
        )

        recorded_at = datetime.now(timezone.utc).isoformat()
        for entry in entries_seed:
            self._endogenous_strategy_memory_service.record_observation(
                history,
                target=entry.get("target"),
                priority=entry.get("priority"),
                risk=entry.get("risk"),
                context_key=context_key,
                recorded_at=recorded_at,
                status="recommended",
            )
        active_targets = {
            str(entry.get("target") or "").strip().lower()
            for entry in entries_seed
            if str(entry.get("target") or "").strip()
        }
        changed = bool(entries_seed)
        if self._endogenous_strategy_memory_service.resolve_cleared_observation_targets(
            history,
            active_targets=active_targets,
            context_key=context_key,
            recorded_at=recorded_at,
        ):
            changed = True
        if changed:
            self._endogenous_drive_history_persistence_service.persist(history)

        refreshed_strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        refreshed_target_stats = dict(refreshed_strategy_memory.get("observation_target_stats") or {})
        return project_observation_program(
            entries_seed,
            target_stats=refreshed_target_stats,
        )

    def _derive_endogenous_context_key(
        self,
        *,
        deliberation: Optional[Dict[str, Any]] = None,
        judgement: Optional[Dict[str, Any]] = None,
        task: Optional[AutonomousChainTask] = None,
    ) -> str:
        if task is not None:
            metadata = dict(task.metadata or {})
            evidence = dict(task.evidence or {})
            endogenous_evidence = dict(evidence.get("endogenous_drive") or {})
            context_key = str(
                metadata.get("endogenous_context_key")
                or endogenous_evidence.get("context_key")
                or ""
            ).strip()
            return context_key.lower() if context_key else "unknown"

        source = judgement if isinstance(judgement, dict) else {}
        if not source and isinstance(deliberation, dict):
            source = {
                "perception": dict(deliberation.get("perception") or {}),
                "reflection": dict(deliberation.get("reflection") or {}),
            }
        perception = dict(source.get("perception") or {})
        reflection = dict(source.get("reflection") or {})
        user_mode = str(perception.get("user_mode") or "unknown").strip().lower() or "unknown"
        system_posture = str(perception.get("system_posture") or "unknown").strip().lower() or "unknown"
        dominant_constraint = (
            str(reflection.get("dominant_constraint") or "none").strip().lower() or "none"
        )
        return f"{user_mode}|{system_posture}|{dominant_constraint}"

    def _status_to_strategy_outcome_bucket(self, status: Any) -> Optional[str]:
        normalized = str(status or "").strip().lower()
        if normalized == "planned":
            return None
        if normalized == "completed":
            return "completed"
        if normalized in {"failed", "cancelled"}:
            return "failed"
        if normalized in {
            "approved",
            "deferred",
            "paused",
            "awaiting_review",
            "awaiting_user_consent",
            "retry",
            "running",
        }:
            return "dragging"
        return None

    def _annotate_endogenous_drive_candidates(
        self,
        *,
        deliberation: Dict[str, Any],
        drive_input: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        if not candidate_items:
            return []
        drive_input = dict(drive_input or {})

        history = self._endogenous_drive_history_persistence_service.load()
        evaluation_id = str(uuid.uuid4())
        recorded_at = datetime.now(timezone.utc).isoformat()
        prepared: list[Dict[str, Any]] = []
        judgement_records: list[Dict[str, Any]] = []
        recorded_active_topics: set[tuple[str, str]] = set()
        recorded_judged_focuses: set[tuple[str, str]] = set()
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        agenda_entries = build_attention_agenda_projection(
            deliberation=deliberation,
            governance_channels=self._governance_channels_from_deliberation(deliberation),
            strategy_memory=strategy_memory,
        ).get("entries") or []
        agenda_map = {
            str(item.get("topic") or "").strip().lower(): dict(item)
            for item in agenda_entries
            if isinstance(item, dict) and str(item.get("topic") or "").strip()
        }

        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            metadata = dict(row.get("metadata") or {})
            evidence = dict(row.get("evidence") or {})
            endogenous_evidence = dict(evidence.get("endogenous_drive") or {})
            judgement = dict(metadata.get("drive_judgement") or {})
            if not judgement:
                candidate_kind = str(
                    dict(metadata.get("score_breakdown") or {}).get("candidate_kind")
                    or dict(endogenous_evidence.get("score_breakdown") or {}).get("candidate_kind")
                    or ""
                ).strip()
                deliberation_intents = [
                    dict(intent)
                    for intent in list(deliberation.get("intents") or [])
                    if isinstance(intent, dict)
                ]
                selected_intents = [
                    intent
                    for intent in deliberation_intents
                    if str(intent.get("candidate_kind") or "").strip() == candidate_kind
                ] or deliberation_intents[:3]
                source_need_types = {
                    str(need_type).strip()
                    for intent in selected_intents
                    for need_type in list(intent.get("source_needs") or [])
                    if str(need_type).strip()
                }
                deliberation_needs = [
                    dict(need)
                    for need in list(deliberation.get("needs") or [])
                    if isinstance(need, dict)
                ]
                linked_needs = [
                    need
                    for need in deliberation_needs
                    if not source_need_types
                    or str(need.get("need_type") or "").strip() in source_need_types
                ][:4]
                judgement = {
                    "perception": dict(deliberation.get("perception") or {}),
                    "world_model": dict(deliberation.get("world_model") or {}),
                    "reflection": dict(deliberation.get("reflection") or {}),
                    "adaptive_policy": dict(deliberation.get("adaptive_policy") or {}),
                    "intent": selected_intents[0] if selected_intents else {},
                    "intents": selected_intents,
                    "needs": linked_needs,
                }
                metadata["drive_judgement"] = judgement
            judgement_id = str(uuid.uuid4())

            metadata["endogenous_evaluation_id"] = evaluation_id
            metadata["endogenous_judgement_id"] = judgement_id
            row["metadata"] = metadata

            if metadata.get("endogenous_drive_key") and not endogenous_evidence.get("stable_key"):
                endogenous_evidence["stable_key"] = metadata.get("endogenous_drive_key")
            if metadata.get("core_values") and not endogenous_evidence.get("core_values"):
                endogenous_evidence["core_values"] = list(metadata.get("core_values") or [])
            if metadata.get("utility") is not None and endogenous_evidence.get("utility") is None:
                endogenous_evidence["utility"] = metadata.get("utility")
            endogenous_evidence["evaluation_id"] = evaluation_id
            endogenous_evidence["judgement_id"] = judgement_id
            evidence["endogenous_drive"] = endogenous_evidence
            row["evidence"] = evidence
            prepared.append(row)

            adaptive_policy = dict(judgement.get("adaptive_policy") or {})
            preferred_focus = str(adaptive_policy.get("preferred_focus") or "").strip().lower()
            context_key = self._derive_endogenous_context_key(judgement=judgement)
            if preferred_focus:
                metadata["endogenous_preferred_focus"] = preferred_focus
                metadata["endogenous_context_key"] = context_key
                endogenous_evidence["preferred_focus"] = preferred_focus
                endogenous_evidence["context_key"] = context_key
                row["metadata"] = metadata
                evidence["endogenous_drive"] = endogenous_evidence
                row["evidence"] = evidence

            focus_key = (preferred_focus, context_key)
            if preferred_focus and focus_key not in recorded_judged_focuses:
                recorded_judged_focuses.add(focus_key)
                global_focus_bucket = self._endogenous_strategy_memory_service.focus_bucket(
                    history, preferred_focus
                )
                global_focus_bucket["judged"] += 1
                contextual_focus_bucket = self._endogenous_strategy_memory_service.focus_bucket(
                    history,
                    preferred_focus,
                    context_key=context_key,
                )
                contextual_focus_bucket["judged"] += 1

            linked_needs = [
                dict(need)
                for need in list(judgement.get("needs") or [])
                if isinstance(need, dict)
            ]
            for need in linked_needs:
                topic = str(need.get("need_type") or "").strip().lower()
                if not topic:
                    continue
                topic_key = (topic, context_key)
                if topic_key in recorded_active_topics:
                    continue
                recorded_active_topics.add(topic_key)
                agenda_entry = dict(agenda_map.get(topic) or {})
                self._endogenous_strategy_memory_service.record_agenda(
                    history,
                    topic=topic,
                    priority=agenda_entry.get("priority", need.get("severity")),
                    confidence=agenda_entry.get("confidence", need.get("confidence")),
                    context_key=context_key,
                    recorded_at=recorded_at,
                    status="active",
                )

            judgement_records.append(
                {
                    "judgement_id": judgement_id,
                    "evaluation_id": evaluation_id,
                    "recorded_at": recorded_at,
                    "candidate_key": metadata.get("endogenous_drive_key") or endogenous_evidence.get("stable_key"),
                    "title": row.get("title"),
                    "priority": row.get("priority"),
                    "governance_task_type": row.get("governance_task_type"),
                    "task_family": row.get("task_family"),
                    "execution_kind": row.get("execution_kind"),
                    "utility": metadata.get("utility"),
                    "scheduled_for": row.get("scheduled_for") or metadata.get("scheduled_for"),
                    "preferred_focus": preferred_focus or None,
                    "context_key": context_key,
                    "deliberation": {
                        "reflection": dict(deliberation.get("reflection") or {}),
                        "adaptive_policy": dict(deliberation.get("adaptive_policy") or {}),
                        "signals": list(deliberation.get("signals") or []),
                    },
                    "drive_judgement": judgement,
                    "drive_input_context": self._build_drive_input_context_snapshot(
                        {
                            **dict(deliberation.get("perception") or {}),
                            "autonomous_chain_gate_active": drive_input.get(
                                "autonomous_chain_gate_active"
                            ),
                        }
                    ),
                }
            )

        if judgement_records:
            history["judgements"] = judgement_records + list(history.get("judgements") or [])
            self._endogenous_drive_history_persistence_service.persist(history)
        return prepared

    def _restore_endogenous_evaluation_snapshots(
        self,
        *,
        drive_history: Dict[str, Any],
        governance_events: Dict[str, Any],
        cognition_state: Dict[str, Any],
    ) -> None:
        self._endogenous_drive_history_persistence_service.persist(drive_history)
        self._endogenous_governance_state_persistence_service.persist_governance_events(governance_events)
        self._endogenous_governance_state_persistence_service.persist_cognition_state(
            dict(cognition_state.get("state") or {})
        )

    def _persist_endogenous_evaluation_for_candidates(
        self,
        *,
        deliberation: Dict[str, Any],
        drive_input: Dict[str, Any],
        governance_channels: Dict[str, Any],
        self_regulation: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        history_snapshot_before = self._endogenous_drive_history_persistence_service.load()
        governance_snapshot_before = self._endogenous_governance_state_persistence_service.load_governance_events()
        cognition_snapshot_before = self._endogenous_governance_state_persistence_service.load_cognition_state()
        try:
            annotated_items = self._annotate_endogenous_drive_candidates(
                deliberation=deliberation,
                drive_input=drive_input,
                candidate_items=candidate_items,
            )
            governance_event_stream = self._record_endogenous_governance_events(
                deliberation=deliberation,
                governance_channels=governance_channels,
                candidate_items=annotated_items,
            )
            cognition_state = self._endogenous_cognition_state_assembly_service.build(
                deliberation=deliberation,
                governance_channels=governance_channels,
                governance_event_stream=governance_event_stream,
                self_regulation=self_regulation,
                candidate_items=annotated_items,
                lm_reasoning_state=lm_reasoning_state,
            )
            self._endogenous_governance_state_persistence_service.persist_cognition_state(cognition_state)
            return {
                "candidate_items": annotated_items,
                "governance_event_stream": governance_event_stream,
                "cognition_state": cognition_state,
            }
        except Exception:
            try:
                self._restore_endogenous_evaluation_snapshots(
                    drive_history=history_snapshot_before,
                    governance_events=governance_snapshot_before,
                    cognition_state=cognition_snapshot_before,
                )
            except Exception:
                logger.warning(
                    "Failed to restore endogenous evaluation snapshots",
                    exc_info=True,
                )
            raise

    def _record_endogenous_drive_outcome(
        self,
        task: AutonomousChainTask,
        *,
        event_type: str,
    ) -> None:
        metadata = dict(task.metadata or {})
        endogenous_drive_key = str(metadata.get("endogenous_drive_key") or "").strip()
        if not endogenous_drive_key and str(task.source or "").strip().lower() != "endogenous_drive":
            return

        latest_decision = task.decision_history[-1].model_dump(mode="json") if task.decision_history else {}
        decision_id = str(latest_decision.get("decision_id") or "").strip()
        status = str(task.status or "").strip()
        history = self._endogenous_drive_history_persistence_service.load()
        for existing in list(history.get("outcomes") or []):
            if not isinstance(existing, dict):
                continue
            if (
                str(existing.get("task_id") or "") == task.task_id
                and str(existing.get("decision_id") or "") == decision_id
                and str(existing.get("status") or "") == status
                and str(existing.get("event_type") or "") == event_type
            ):
                return

        evidence = dict(task.evidence or {})
        execution_result = dict(metadata.get("execution_result") or {})
        decision_context = (
            latest_decision.get("context")
            if isinstance(latest_decision.get("context"), dict)
            else {}
        )
        preferred_focus = str(metadata.get("endogenous_preferred_focus") or "").strip().lower()
        context_key = self._derive_endogenous_context_key(task=task)
        outcome_bucket = self._status_to_strategy_outcome_bucket(status)
        if outcome_bucket is not None:
            global_focus_bucket = self._endogenous_strategy_memory_service.focus_bucket(
                history, preferred_focus
            )
            global_focus_bucket[outcome_bucket] += 1
            contextual_focus_bucket = self._endogenous_strategy_memory_service.focus_bucket(
                history,
                preferred_focus,
                context_key=context_key,
            )
            contextual_focus_bucket[outcome_bucket] += 1
        linked_topics: set[str] = set()
        drive_judgement = dict(metadata.get("drive_judgement") or {})
        for need in list(drive_judgement.get("needs") or []):
            if not isinstance(need, dict):
                continue
            topic = str(need.get("need_type") or "").strip().lower()
            if topic:
                linked_topics.add(topic)
        outcome = {
            "outcome_id": str(uuid.uuid4()),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "task_id": task.task_id,
            "trace_id": task.trace_id,
            "endogenous_drive_key": endogenous_drive_key,
            "endogenous_judgement_id": metadata.get("endogenous_judgement_id"),
            "endogenous_evaluation_id": metadata.get("endogenous_evaluation_id"),
            "preferred_focus": preferred_focus or None,
            "context_key": context_key,
            "title": task.title,
            "source": task.source,
            "priority": task.priority,
            "status": status,
            "governance_task_type": self._task_profile_policy.governance_type(task),
            "task_family": self._task_profile_policy.runtime_family(task),
            "execution_kind": self._task_profile_policy.execution_kind(task),
            "decision_id": decision_id or None,
            "decision_actor": latest_decision.get("actor"),
            "decision_reason": latest_decision.get("reason") or task.decision_reason,
            "quality_score": (
                metadata.get("quality_score")
                if metadata.get("quality_score") is not None
                else decision_context.get("quality_score")
            ),
            "learning_quality_score": evidence.get("learning_quality_score"),
            "result_status": execution_result.get("status"),
        }
        final_response = str(
            decision_context.get("employee_final_response") or ""
        ).strip()
        if final_response:
            outcome["employee_final_response"] = final_response[:4000]
            outcome["outcome_summary"] = final_response[:800]
        reference_alignment = metadata.get("reference_alignment")
        if not isinstance(reference_alignment, dict):
            reference_alignment = evidence.get("reference_alignment")
        if isinstance(reference_alignment, dict) and reference_alignment:
            outcome["reference_alignment"] = dict(reference_alignment)
        cognitive_alignment = metadata.get("cognitive_alignment")
        if not isinstance(cognitive_alignment, dict):
            cognitive_alignment = evidence.get("cognitive_alignment")
        if isinstance(cognitive_alignment, dict) and cognitive_alignment:
            outcome["cognitive_alignment"] = dict(cognitive_alignment)
        posture_alignment = metadata.get("llm_posture_alignment")
        if not isinstance(posture_alignment, list):
            posture_alignment = evidence.get("llm_posture_alignment")
        normalized_posture_alignment = [
            str(item).strip()
            for item in list(posture_alignment or [])[:6]
            if str(item).strip()
        ]
        if normalized_posture_alignment:
            outcome["llm_posture_alignment"] = list(normalized_posture_alignment)
        priority_basis = metadata.get("llm_priority_basis")
        if not isinstance(priority_basis, list):
            priority_basis = evidence.get("llm_priority_basis")
        normalized_priority_basis = [
            str(item).strip()
            for item in list(priority_basis or [])[:6]
            if str(item).strip()
        ]
        if normalized_priority_basis:
            outcome["llm_priority_basis"] = list(normalized_priority_basis)
        cognitive_assessment = metadata.get("llm_cognitive_assessment")
        if not isinstance(cognitive_assessment, dict):
            cognitive_assessment = evidence.get("llm_cognitive_assessment")
        if (
            (not isinstance(cognitive_assessment, dict) or not cognitive_assessment)
            and event_type != "planned"
        ):
            cognitive_assessment = self._canonical_cognitive_assessment_from_drive_judgement(
                drive_judgement=drive_judgement,
                preferred_focus=preferred_focus,
                status=status,
            )
        if isinstance(cognitive_assessment, dict) and cognitive_assessment:
            def _assessment_texts(value: Any, *, limit: int = 6) -> list[str]:
                raw_values = [value] if isinstance(value, str) else list(value or [])
                return [
                    str(item).strip()
                    for item in raw_values[:limit]
                    if str(item).strip()
                ]

            outcome["llm_cognitive_assessment"] = {
                "current_judgement": str(
                    cognitive_assessment.get("current_judgement") or ""
                ).strip(),
                "dominant_constraint": str(
                    cognitive_assessment.get("dominant_constraint") or ""
                ).strip(),
                "primary_grounding_gaps": _assessment_texts(
                    cognitive_assessment.get("primary_grounding_gaps")
                ),
                "why_this_task_type_now": _assessment_texts(
                    cognitive_assessment.get("why_this_task_type_now")
                ),
                "why_not_improvement_now": _assessment_texts(
                    cognitive_assessment.get("why_not_improvement_now")
                ),
                "self_iteration_target": str(
                    cognitive_assessment.get("self_iteration_target") or ""
                ).strip(),
                "self_iteration_hypothesis": str(
                    cognitive_assessment.get("self_iteration_hypothesis") or ""
                ).strip(),
                "stay_or_switch": str(
                    cognitive_assessment.get("stay_or_switch") or ""
                ).strip(),
                "switch_reason": str(
                    cognitive_assessment.get("switch_reason") or ""
                ).strip(),
            }
        if outcome_bucket is not None:
            agenda_status = "dragging" if outcome_bucket == "dragging" else outcome_bucket
            recorded_at = str(outcome.get("recorded_at") or datetime.now(timezone.utc).isoformat())
            for topic in linked_topics:
                self._endogenous_strategy_memory_service.record_agenda(
                    history,
                    topic=topic,
                    priority=metadata.get("utility"),
                    confidence=metadata.get("utility"),
                    context_key=context_key,
                    recorded_at=recorded_at,
                    status=agenda_status,
                )
        history["outcomes"] = [outcome] + list(history.get("outcomes") or [])
        self._endogenous_drive_history_persistence_service.persist(history)

    def _canonical_cognitive_assessment_from_drive_judgement(
        self,
        *,
        drive_judgement: Dict[str, Any],
        preferred_focus: str,
        status: str,
    ) -> Dict[str, Any]:
        if not drive_judgement:
            return {}

        reflection = dict(drive_judgement.get("reflection") or {})
        adaptive_policy = dict(drive_judgement.get("adaptive_policy") or {})
        intents = [
            dict(item)
            for item in list(drive_judgement.get("intents") or [])
            if isinstance(item, dict)
        ]
        needs = [
            dict(item)
            for item in list(drive_judgement.get("needs") or [])
            if isinstance(item, dict)
        ]
        primary_intent = intents[0] if intents else {}
        primary_need = needs[0] if needs else {}

        focus = preferred_focus or str(adaptive_policy.get("preferred_focus") or "").strip().lower()
        dominant_constraint = str(reflection.get("dominant_constraint") or "").strip()
        intent_type = str(primary_intent.get("intent_type") or "").strip()
        need_type = str(primary_need.get("need_type") or "").strip()
        candidate_kind = str(primary_intent.get("candidate_kind") or "").strip()

        target_by_focus = {
            "truthfulness": "truthfulness",
            "learning_expansion": "learning_frontier",
            "memory_continuity": "memory_continuity",
            "governance_hygiene": "api_b_judgement",
            "body_growth": "body_growth",
            "observation": "grounding",
        }
        target = target_by_focus.get(focus) or target_by_focus.get(candidate_kind) or focus
        if not target and need_type:
            target = need_type

        current_judgement = (
            f"当前选择 {focus or 'endogenous'} 焦点"
            + (f"，主约束为 {dominant_constraint}" if dominant_constraint else "")
        ).strip()
        if not focus and not dominant_constraint and not intent_type and not need_type:
            return {}

        why_not_improvement: list[str] = []
        if dominant_constraint in {"user_service_priority", "historical_underdelivery"}:
            why_not_improvement.append(
                f"当 {dominant_constraint} 仍是主约束时，应暂缓直接进行身体改进。"
            )
        if focus in {"truthfulness", "observation", "governance_hygiene", "memory_continuity"}:
            why_not_improvement.append(
                f"在直接进行身体改进前，应优先处理 {focus} 治理。"
            )
        if status in {
            "failed",
            "deferred",
            "awaiting_review",
            "awaiting_user_consent",
        }:
            why_not_improvement.append(
                f"最近结果状态为 {status}，在推进更大范围的自我改进前需要先复核。"
            )

        return {
            "current_judgement": current_judgement,
            "dominant_constraint": dominant_constraint,
            "primary_grounding_gaps": [need_type] if need_type else [],
            "why_this_task_type_now": [
                item
                for item in [
                    str(primary_intent.get("rationale") or "").strip(),
                    str(primary_need.get("rationale") or "").strip(),
                ]
                if item
            ][:6],
            "why_not_improvement_now": why_not_improvement[:6],
            "self_iteration_target": target,
            "self_iteration_hypothesis": (
                str(primary_intent.get("rationale") or "").strip()
                or f"在证据发生变化前，继续围绕 {target or focus or 'endogenous'} 推进。"
            ),
            "stay_or_switch": "stay" if focus else "",
            "switch_reason": "",
        }

    def _active_autonomous_chain_tasks(self) -> list[AutonomousChainTask]:
        """Return active autonomous-chain rows across API-B and employee lanes."""
        rows: list[AutonomousChainTask] = []
        seen: set[str] = set()
        for task in [
            *self._autonomous_chain_store.list_api_b_judgement_tasks(),
            *self._autonomous_chain_store.list_employee_execution_lane_tasks(),
        ]:
            if task.task_id in seen:
                continue
            seen.add(task.task_id)
            rows.append(task)
        return rows

    def _task_activity_metadata(self, task: AutonomousChainTask) -> Dict[str, Any]:
        profile = self._task_profile_policy.runtime_profile(task)
        metadata: Dict[str, Any] = {
            "task_id": task.task_id,
            "trace_id": task.trace_id,
            "task_type": task.task_type,
            "governance_task_type": profile["governance_task_type"],
            "task_family": profile["task_family"],
        }
        execution_kind = profile.get("execution_kind")
        if execution_kind is not None:
            metadata["execution_kind"] = execution_kind
        scheduled_for = self._schedule_allocator.task_schedule_token(task)
        if scheduled_for is not None:
            metadata["scheduled_for"] = scheduled_for
        return metadata

    def _build_autonomous_chain_activity_metadata(
        self,
        tasks: list[AutonomousChainTask],
        *,
        action: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "action": action,
            "count": len(tasks),
            **dict(extra or {}),
        }
        metadata["governance_task_types"] = sorted(
            {self._task_profile_policy.governance_type(task) for task in tasks}
        )
        metadata["task_families"] = sorted(
            {self._task_profile_policy.runtime_family(task) for task in tasks}
        )
        execution_kinds = sorted(
            {
                execution_kind
                for execution_kind in (self._task_profile_policy.execution_kind(task) for task in tasks)
                if execution_kind is not None
            }
        )
        if execution_kinds:
            metadata["execution_kinds"] = execution_kinds
        if len(tasks) == 1:
            metadata.update(self._task_activity_metadata(tasks[0]))
        return metadata

    def _current_shell_slot_context(self) -> Optional[Dict[str, Any]]:
        registry = getattr(self, "_body_registry", None)
        if registry is None:
            return None
        try:
            shell_meta = registry.get_shell_slot()
        except Exception:
            return None
        if shell_meta is None:
            return None

        try:
            payload = shell_meta.model_dump(mode="json")
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        slot_id = str(payload.get("slot_id") or "").strip()
        if not slot_id:
            return payload

        from pathlib import Path

        repaired = False
        try:
            expected_root = registry.slot_root(slot_id)
        except Exception:
            return payload
        for field_name, leaf in (
            ("worktree_path", "worktree"),
            ("runtime_path", "runtime"),
            ("logs_path", "logs"),
        ):
            current = str(payload.get(field_name) or "").strip()
            expected = (expected_root / leaf).resolve()
            if current and Path(current).exists():
                continue
            if expected.exists():
                payload[field_name] = str(expected)
                setattr(shell_meta, field_name, str(expected))
                repaired = True

        if repaired:
            try:
                registry.save_slot_meta(shell_meta)
            except Exception:
                pass
        from .body_execution_readiness import inspect_body_execution_readiness

        payload["body_readiness"] = inspect_body_execution_readiness(
            slot_id=slot_id,
            worktree_path=str(payload.get("worktree_path") or ""),
            expected_body_state=payload.get("body_state"),
        )
        return payload

    def _completed_learning_task_summaries(self, limit: int = 8) -> list[Dict[str, Any]]:
        rows: list[tuple[str, Dict[str, Any]]] = []
        for task in self._autonomous_chain_store.list_writeback_history(status="completed"):
            if self._task_profile_policy.runtime_family(task) != "self_learning":
                continue
            metadata = dict(task.metadata or {})
            evidence = dict(task.evidence or {})
            latest_decision = task.decision_history[-1] if task.decision_history else None
            latest_context = dict(latest_decision.context or {}) if latest_decision else {}
            completed_at = (
                metadata.get("completed_at")
                or getattr(task, "updated_at", None)
                or getattr(task, "created_at", None)
            )
            if isinstance(completed_at, datetime):
                completed_at = completed_at.isoformat()
            conclusion = str(
                latest_context.get("employee_final_response")
                or metadata.get("outcome_summary")
                or dict(metadata.get("execution_result") or {}).get("summary")
                or ""
            ).strip()
            raw_evidence_summary = evidence.get("evidence_summary") or []
            if isinstance(raw_evidence_summary, str):
                raw_evidence_summary = [raw_evidence_summary]
            evidence_summary = [
                str(item).strip()
                for item in list(raw_evidence_summary)[:6]
                if str(item).strip()
            ]
            rows.append(
                (
                    str(completed_at or ""),
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "summary": task.summary,
                        "conclusion": conclusion[:1600],
                        "evidence_summary": evidence_summary,
                        "completed_at": completed_at,
                        "quality_score": (
                            metadata.get("quality_score")
                            if metadata.get("quality_score") is not None
                            else latest_context.get("quality_score")
                            if latest_context.get("quality_score") is not None
                            else evidence.get("learning_quality_score")
                        ),
                        "endogenous_drive_key": metadata.get("endogenous_drive_key"),
                    },
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _autonomous_chain_task_summary_payload(
        self,
        task: AutonomousChainTask,
    ) -> Dict[str, Any]:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "status": str(task.status),
            "governance_task_type": self._task_profile_policy.governance_type(task),
            "task_family": self._task_profile_policy.runtime_family(task),
            "execution_kind": self._task_profile_policy.execution_kind(task),
            "created_at": (
                task.created_at.isoformat()
                if isinstance(getattr(task, "created_at", None), datetime)
                else str(getattr(task, "created_at", "") or "")
            ),
            "updated_at": (
                task.updated_at.isoformat()
                if isinstance(getattr(task, "updated_at", None), datetime)
                else str(getattr(task, "updated_at", "") or "")
            ),
            "constraints": dict(task.constraints or {}),
            "evidence": {
                "learning_quality_score": dict(task.evidence or {}).get("learning_quality_score"),
                "recent_learning_topics": dict(task.evidence or {}).get("recent_learning_topics"),
                "source": dict(task.evidence or {}).get("source"),
            },
            "metadata": {
                "endogenous_drive_key": dict(task.metadata or {}).get("endogenous_drive_key"),
                "learning_branch": dict(task.metadata or {}).get("learning_branch"),
                "self_learning_mode": dict(task.metadata or {}).get("self_learning_mode"),
                "quality_score": dict(task.metadata or {}).get("quality_score"),
                "candidate_kind": dict(
                    dict(task.metadata or {}).get("score_breakdown") or {}
                ).get("candidate_kind"),
            },
        }

    def _api_b_judgement_task_summaries(self, limit: int = 20) -> list[Dict[str, Any]]:
        rows: list[tuple[str, Dict[str, Any]]] = []
        for task in self._autonomous_chain_store.list_api_b_judgement_tasks():
            rows.append(
                (
                    str(getattr(task, "updated_at", None) or getattr(task, "created_at", None) or ""),
                    self._autonomous_chain_task_summary_payload(task),
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _employee_execution_lane_task_summaries(self, limit: int = 20) -> list[Dict[str, Any]]:
        rows: list[tuple[str, Dict[str, Any]]] = []
        for task in self._autonomous_chain_store.list_employee_execution_lane_tasks():
            rows.append(
                (
                    str(getattr(task, "updated_at", None) or getattr(task, "created_at", None) or ""),
                    self._autonomous_chain_task_summary_payload(task),
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _planning_activity_kind_for_task(self, task_type: str) -> str:
        normalized = self._task_profile_policy.normalize_type(task_type)
        if normalized == "self_learning":
            return "self_learning"
        if normalized in {"self_evolution", "memory_maintenance"}:
            return "autonomous_chain_plan"
        return "autonomous_chain_plan"

    async def _fetch_gateway_activity_snapshot(self) -> Dict[str, Any]:
        import asyncio
        import aiohttp

        execution_config = self.config.execution
        url = f"{execution_config.gateway_address}/admin/activity"
        last_error: Exception | None = None

        for attempt in range(1, 4):
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        url,
                        headers=self._gateway_registration_headers(),
                    ) as response:
                        if response.status != 200:
                            raise HTTPException(
                                status_code=503,
                                detail=f"网关活动接口返回状态 {response.status}",
                            )
                        return await response.json()
            except HTTPException:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
                    continue

        logger.warning(f"Failed to fetch gateway activity snapshot: {last_error}")
        raise HTTPException(status_code=503, detail="网关活动快照暂不可用")

    async def _fetch_memory_maintenance_status(self) -> Dict[str, Any]:
        """Read Memory's cadence state for the Auto candidate gate."""
        now = time.monotonic()
        cached_at = getattr(self, "_memory_maintenance_status_cached_at", 0.0)
        cached = getattr(self, "_memory_maintenance_status_cache", None)
        if isinstance(cached, dict) and now - cached_at < 30.0:
            return dict(cached)
        try:
            payload = await self._memory_client(
                memory_actor="stellar_auto",
                memory_domain="evolution",
                timeout_seconds=2,
            ).request_json("GET", "/compressed/rules-status")
            status = dict(payload) if isinstance(payload, dict) else {}
        except Exception as exc:
            status = {"status": "unavailable", "error": type(exc).__name__}
        self._memory_maintenance_status_cache = dict(status)
        self._memory_maintenance_status_cached_at = now
        return status

    async def get_runtime_activity(self):
        snapshot = await self._fetch_gateway_activity_snapshot()
        return {
            "status": "ok",
            "gateway_address": self.config.execution.gateway_address,
            "activity": snapshot,
        }

    async def get_runtime_observation_input(self):
        payload = await self.evaluate_drive_input(
            {
                "autonomous_chain_gate_active": False,
                "perception_scope": "full",
            }
        )
        observation_input = project_runtime_observation_input(
            payload,
            snapshot_source="live",
        )
        return {
            "status": "ok",
            "gateway_address": self.config.execution.gateway_address,
            "observation_input": observation_input,
        }

    async def evaluate_drive_input(self, request: dict | None = None):
        request = dict(request or {})
        runtime = getattr(self, "_service_runtime", None)
        requested_gate_state = request.get("autonomous_chain_gate_active")
        gate_active = (
            bool(getattr(runtime, "autonomous_chain_gate_active", False))
            if requested_gate_state is None
            else bool(requested_gate_state)
        )
        perception_scope = "autonomous_only" if gate_active else (
            str(request.get("perception_scope") or "").strip().lower() or "full"
        )
        effective_evidence_packet = dict(
            request.get("evidence_packet")
            or getattr(runtime, "auto_evidence_packet", {})
            or {}
        )
        snapshot = await self._fetch_gateway_activity_snapshot()
        if perception_scope == "autonomous_only":
            snapshot = project_auto_activity_snapshot(snapshot)

        now_override = request.get("now")
        if isinstance(now_override, str):
            try:
                now = datetime.fromisoformat(now_override)
                if now.tzinfo is not None:
                    now = now.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid now override: {exc}")
        else:
            now = datetime.utcnow()

        service_cfg = self.config.service_runtime
        task_profile = self._task_profile_policy.drive_input_profile(request)
        evaluation_config = DriveInputEvaluationConfig(
            gateway_address=self.config.execution.gateway_address,
            now=now,
            user_idle_seconds=int(
                request.get(
                    "user_idle_seconds",
                    getattr(service_cfg, "activity_guard_user_seconds", 600),
                )
            ),
            memory_idle_seconds=int(
                request.get(
                    "memory_idle_seconds",
                    getattr(service_cfg, "activity_guard_memory_seconds", 600),
                )
            ),
            workflow_idle_seconds=int(
                request.get(
                    "workflow_idle_seconds",
                    getattr(service_cfg, "activity_guard_workflow_seconds", 600),
                )
            ),
            perception_scope=perception_scope,
            autonomous_chain_gate_active=gate_active,
            evidence_packet=effective_evidence_packet,
        )
        return evaluate_drive_input_snapshot(
            request=request,
            snapshot=snapshot,
            config=evaluation_config,
            task_profile=task_profile,
            shell_slot=self._current_shell_slot_context(),
            completed_learning_tasks=self._completed_learning_task_summaries(),
        )

    async def _touch_gateway_activity(
        self,
        activity_kind: str,
        *,
        source_service: str = "supervisor",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            import aiohttp

            execution_config = self.config.execution
            async with aiohttp.ClientSession() as session:
                url = f"{execution_config.gateway_address}/admin/activity/touch"
                payload = {
                    "activity_kind": activity_kind,
                    "source_service": source_service,
                    "metadata": dict(metadata or {}),
                }
                async with session.post(
                    url,
                    json=payload,
                    headers=self._gateway_registration_headers(),
                    timeout=10,
                ) as response:
                    if response.status != 200:
                        logger.debug(
                            "Gateway activity touch ignored with status %s for kind %s",
                            response.status,
                            activity_kind,
                        )
        except Exception as exc:
            logger.debug(f"Unable to touch gateway activity kind={activity_kind}: {exc}")

    def _existing_endogenous_drive_keys(self) -> set[str]:
        """Return drive keys for tasks that are still alive (not terminal).

        Terminal = completed, failed, cancelled.  Everything else blocks
        re-creation of the same drive key to prevent task pile-up.
        """
        terminal = {"completed", "failed", "cancelled"}
        keys: set[str] = set()
        for task in self._active_autonomous_chain_tasks():
            if task.status in terminal:
                continue
            key = task.metadata.get("endogenous_drive_key")
            if isinstance(key, str) and key:
                keys.add(key)
        return keys

    async def evaluate_endogenous_drive(self, request: dict | None = None):
        """Evaluate endogenous cognition state and API-B judgement projections."""

        request = dict(request or {})
        request.setdefault("include_memory_maintenance_status", True)

        def schedule_candidate_items(candidates: list[Any]) -> list[Dict[str, Any]]:
            return self._schedule_allocator.apply_to_candidates(
                [candidate.to_api_b_judgement_item() for candidate in candidates],
                occupied_tokens=self._schedule_allocator.occupied_tokens(
                    self._active_autonomous_chain_tasks()
                ),
                now=datetime.now(),
            )

        def derive_cognitive_self_regulation(
            *,
            drive_history: Dict[str, Any],
            lm_reasoning_state: Dict[str, Any],
            deliberation: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            posture_service = self._endogenous_cognitive_posture_service
            policy = posture_service.current_policy()
            return self._endogenous_self_regulation_service.derive(
                policy=policy,
                posture_profile=posture_service.resolve_profile(
                    policy,
                    lm_reasoning_state=lm_reasoning_state,
                    drive_history=drive_history,
                    deliberation=deliberation,
                ),
                recent_cognitive_alignment=(
                    posture_service.recent_alignment(
                        history_snapshot=drive_history,
                    )
                ),
                lm_reasoning_state=lm_reasoning_state,
            )

        def release_cleared_observation_carryover(
            *,
            persisted_self_regulation: Dict[str, Any],
            cognitive_self_regulation: Dict[str, Any],
            deliberation: Dict[str, Any],
            lm_reasoning_state: Dict[str, Any],
            drive_history: Dict[str, Any],
        ) -> Dict[str, Any]:
            posture_service = self._endogenous_cognitive_posture_service
            return self._endogenous_self_regulation_service.release_cleared_historical_observation_carryover(
                persisted_self_regulation=persisted_self_regulation,
                cognitive_self_regulation=cognitive_self_regulation,
                deliberation=deliberation,
                lm_reasoning_state=lm_reasoning_state,
                posture_profile=posture_service.active_profile(
                    lm_reasoning_state=lm_reasoning_state,
                    history_snapshot=drive_history,
                    deliberation=deliberation,
                ),
            )

        context = EndogenousDriveEvaluationContext(
            runtime_config=self.config.service_runtime,
            resolve_drive_input_request=lambda payload: self._resolve_runtime_drive_input_request(
                payload,
                include_gate_default=True,
            ),
            load_self_regulation=self._endogenous_governance_state_persistence_service.load_self_regulation,
            load_drive_history=self._endogenous_drive_history_persistence_service.load,
            normalize_strategy_memory=normalize_endogenous_strategy_memory,
            api_b_judgement_task_summaries=self._api_b_judgement_task_summaries,
            employee_execution_lane_task_summaries=self._employee_execution_lane_task_summaries,
            build_deliberation_report=self._endogenous_drive_engine.build_deliberation_report,
            generate_candidates=self._endogenous_drive_engine.generate_candidates,
            existing_drive_keys=self._existing_endogenous_drive_keys,
            schedule_candidate_items=schedule_candidate_items,
            lm_generation_application_state=self._lm_generation_application_state,
            derive_cognitive_self_regulation=derive_cognitive_self_regulation,
            release_cleared_observation_carryover=release_cleared_observation_carryover,
            governance_channels_from_deliberation=self._governance_channels_from_deliberation,
            persist_evaluation=self._persist_endogenous_evaluation_for_candidates,
            load_governance_events=self._endogenous_governance_state_persistence_service.load_governance_events,
            build_cognition_state=self._endogenous_cognition_state_assembly_service.build,
            record_ui_activity=self._ui_runtime.record_activity,
            build_response_fields=self._build_drive_input_response_fields,
            drive_posture_from_deliberation=self._drive_posture_signal_from_deliberation,
            core_values=CORE_VALUES,
            load_evolution_foundation=self._load_evolution_foundation_projection,
        )
        return await run_endogenous_drive_evaluation(
            request=request,
            context=context,
        )

    async def get_endogenous_governance_events(self) -> Dict[str, Any]:
        snapshot = self._endogenous_governance_state_persistence_service.load_governance_events()
        return {
            "status": "ok",
            "updated_at": snapshot.get("updated_at"),
            "governance_event_stream": project_governance_event_stream(snapshot),
        }

    async def get_endogenous_self_regulation(self) -> Dict[str, Any]:
        regulation = self._endogenous_governance_state_persistence_service.load_self_regulation()
        return {
            "status": "ok",
            "updated_at": regulation.get("updated_at"),
            "self_regulation": regulation,
            "corrective_mode": derive_corrective_mode(regulation),
        }

    async def get_endogenous_cognition_state(self) -> Dict[str, Any]:
        snapshot = self._endogenous_governance_state_persistence_service.load_cognition_state()
        return {
            "status": "ok",
            "updated_at": snapshot.get("updated_at"),
            "cognition_state": dict(snapshot.get("state") or {}),
        }

    async def get_endogenous_governance_state(self) -> Dict[str, Any]:
        cognition_snapshot = self._endogenous_governance_state_persistence_service.load_cognition_state()
        event_snapshot = self._endogenous_governance_state_persistence_service.load_governance_events()
        regulation = self._endogenous_governance_state_persistence_service.load_self_regulation()
        drive_history = self._endogenous_drive_history_persistence_service.load()
        return {
            "status": "ok",
            "updated_at": cognition_snapshot.get("updated_at"),
            "cognition_state": dict(cognition_snapshot.get("state") or {}),
            "governance_event_stream": project_governance_event_stream(event_snapshot),
            "self_regulation": regulation,
            "corrective_mode": derive_corrective_mode(regulation),
            "strategy_memory": normalize_endogenous_strategy_memory(
                drive_history.get("strategy_memory")
            ),
        }

    def _drive_posture_signal_from_deliberation(
        self,
        deliberation: Dict[str, Any],
    ) -> Dict[str, Any]:
        for signal in list(deliberation.get("signals") or []):
            if not isinstance(signal, dict):
                continue
            if str(signal.get("signal_type") or "").strip() == "drive_posture_signal":
                return dict(signal)
        return {}

    def _governance_channels_from_deliberation(
        self,
        deliberation: Dict[str, Any],
    ) -> Dict[str, Any]:
        channels: Dict[str, Any] = {
            "task_candidates": [],
            "observation_requests": [],
            "governance_review_requests": [],
            "truthfulness_alerts": [],
            "autonomy_alignment_requests": [],
            "posture": {},
        }

        for signal in list(deliberation.get("signals") or []):
            if not isinstance(signal, dict):
                continue
            signal_type = str(signal.get("signal_type") or "").strip()
            if signal_type == "drive_posture_signal":
                channels["posture"] = dict(signal)
                continue
            if signal_type == "observation_signal":
                signal_copy = dict(signal)
                channels["observation_requests"].append(signal_copy)
                payload = dict(signal_copy.get("payload") or {})
                observation_target = str(payload.get("observation_target") or "").strip().lower()
                if observation_target == "truthfulness":
                    channels["truthfulness_alerts"].append(
                        {
                            "signal_type": "truthfulness_alert",
                            "priority": signal_copy.get("priority"),
                            "message": signal_copy.get("message"),
                            "rationale": signal_copy.get("rationale"),
                            "payload": payload,
                        }
                    )
                continue
            if signal_type == "governance_review_suggestion":
                channels["governance_review_requests"].append(dict(signal))
                continue
            if signal_type == "autonomy_alignment_signal":
                channels["autonomy_alignment_requests"].append(dict(signal))

        return channels

    def _record_endogenous_governance_events(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        snapshot = self._endogenous_governance_state_persistence_service.load_governance_events()
        recorded_at = datetime.now(timezone.utc).isoformat()
        perception = dict(deliberation.get("perception") or {})
        reflection = dict(deliberation.get("reflection") or {})
        adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
        context_key = (
            f"{str(perception.get('user_mode') or 'unknown').strip().lower() or 'unknown'}|"
            f"{str(perception.get('system_posture') or 'unknown').strip().lower() or 'unknown'}|"
            f"{str(reflection.get('dominant_constraint') or 'none').strip().lower() or 'none'}"
        )

        task_candidates = []
        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            metadata = dict(item.get("metadata") or {})
            score_breakdown = dict(metadata.get("score_breakdown") or {})
            task_candidates.append(
                {
                    "title": item.get("title"),
                    "stable_key": item.get("stable_key"),
                    "candidate_kind": score_breakdown.get("candidate_kind"),
                    "priority": item.get("priority"),
                }
            )

        channels = dict(governance_channels or {})
        channels["task_candidates"] = task_candidates

        generated_events: list[Dict[str, Any]] = []
        channel_event_map = {
            "observation_requests": "observation_request",
            "governance_review_requests": "governance_review_request",
            "truthfulness_alerts": "truthfulness_alert",
            "autonomy_alignment_requests": "autonomy_alignment_request",
        }
        for channel_name, event_type in channel_event_map.items():
            for item in list(channels.get(channel_name) or []):
                if not isinstance(item, dict):
                    continue
                generated_events.append(
                    {
                        "event_id": str(uuid.uuid4()),
                        "event_type": event_type,
                        "channel": channel_name,
                        "recorded_at": recorded_at,
                        "context_key": context_key,
                        "preferred_focus": adaptive_policy.get("preferred_focus"),
                        "priority": item.get("priority"),
                        "message": item.get("message"),
                        "rationale": item.get("rationale"),
                        "payload": dict(item.get("payload") or {}),
                    }
                )

        posture = dict(channels.get("posture") or {})
        if posture:
            generated_events.append(
                {
                    "event_id": str(uuid.uuid4()),
                    "event_type": "drive_posture",
                    "channel": "posture",
                    "recorded_at": recorded_at,
                    "context_key": context_key,
                    "preferred_focus": adaptive_policy.get("preferred_focus"),
                    "priority": posture.get("priority"),
                    "message": posture.get("message"),
                    "rationale": posture.get("rationale"),
                    "payload": dict(posture.get("payload") or {}),
                }
            )

        if generated_events:
            existing_event_keys = {
                semantic_key
                for item in list(snapshot.get("events") or [])
                if isinstance(item, dict)
                for semantic_key in [self._endogenous_governance_state_persistence_service.semantic_event_key(item)]
                if semantic_key
            }
            new_events: list[Dict[str, Any]] = []
            for event in generated_events:
                semantic_key = self._endogenous_governance_state_persistence_service.semantic_event_key(event)
                if semantic_key and semantic_key in existing_event_keys:
                    continue
                if semantic_key:
                    existing_event_keys.add(semantic_key)
                new_events.append(event)
            if not new_events:
                return project_governance_event_stream(snapshot)
            snapshot["events"] = new_events + list(snapshot.get("events") or [])
            self._endogenous_governance_state_persistence_service.persist_governance_events(snapshot)
        return project_governance_event_stream(snapshot)

    async def list_autonomous_chain_tasks(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ):
        return await self._autonomous_chain_planning_service.list_tasks(
            status=status,
            task_type=task_type,
            execution_kind=execution_kind,
        )

    async def get_autonomous_chain_task(self, task_id: str):
        return await self._autonomous_chain_planning_service.get_task(task_id)

    async def validate_autonomous_chain_task_lease(
        self,
        task_id: str,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(request or {})
        try:
            task = self._autonomous_chain_store.validate_execution_lease(
                task_id,
                generation=int(payload.get("generation") or 0),
                attempt_id=str(payload.get("attempt_id") or ""),
                owner_session_id=str(payload.get("owner_session_id") or ""),
            )
        except (KeyError, TypeError, ValueError, StaleExecutionLeaseError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "stale_execution_lease", "message": str(exc)},
            ) from exc
        return {
            "status": "valid",
            "task_id": task.task_id,
            "generation": task.execution_lease.generation,
            "attempt_id": task.execution_lease.attempt_id,
        }

    async def renew_autonomous_chain_task_lease(
        self,
        task_id: str,
        request: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(request or {})
        try:
            self._autonomous_chain_store.validate_execution_lease(
                task_id,
                generation=int(payload.get("generation") or 0),
                attempt_id=str(payload.get("attempt_id") or ""),
                owner_session_id=str(payload.get("owner_session_id") or ""),
            )
            task = self._autonomous_task_state.renew_execution(
                task_id,
                generation=int(payload.get("generation") or 0),
                attempt_id=str(payload.get("attempt_id") or ""),
                lease_seconds=float(payload.get("lease_seconds") or 300),
            )
        except (KeyError, TypeError, ValueError, StaleExecutionLeaseError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "stale_execution_lease", "message": str(exc)},
            ) from exc
        return {
            "status": "renewed",
            "task_id": task.task_id,
            "generation": task.execution_lease.generation,
            "attempt_id": task.execution_lease.attempt_id,
            "expires_at": (
                task.execution_lease.expires_at.isoformat()
                if task.execution_lease.expires_at
                else None
            ),
        }

    async def clear_autonomous_chain_runtime(self, request: dict | None = None):
        return await self._autonomous_chain_runtime_reset_service.clear(request)

    async def recover_autonomous_chain_from_mem(self, request: dict | None = None):
        return await self._autonomous_chain_recovery_service.recover_from_mem(request)

    async def decide_autonomous_chain_task(self, task_id: str, request: dict | None = None):
        return await self._autonomous_task_review_service.decide(task_id, request)

    async def review_autonomous_chain_tasks(self, request: dict | None = None):
        return await self._autonomous_task_review_service.review(request)
