from __future__ import annotations

import asyncio
import logging
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException
import aiohttp

from systems.self_learning.models import SupervisorConclusionSubmission
from systems.supervisor.endogenous_candidate_pipeline import CORE_VALUES
from systems.supervisor.endogenous_proposal_port import (
    LmGenerationApplicationState,
    project_lm_generation_application_state,
)
from systems.supervisor.endogenous_cognition_state import (
    build_cognition_state_projection,
    build_judgement_core_projection,
)
from systems.supervisor.endogenous_strategy_projection import (
    build_attention_agenda_projection,
)
from systems.supervisor.endogenous_uncertainty_projection import (
    build_uncertainty_ledger_projection,
)
from systems.supervisor.endogenous_observation_projection import (
    build_observation_program_entries,
    project_observation_program,
)
from systems.supervisor.endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)
from systems.supervisor.endogenous_meta_governance import derive_meta_governance_mode
from systems.supervisor.endogenous_proposal_cognition import (
    compact_proposal_memory,
    build_proposal_cognition_projection,
)
from systems.supervisor.endogenous_drive_orchestration import (
    EndogenousDriveEvaluationContext,
    evaluate_endogenous_drive as run_endogenous_drive_evaluation,
)
from systems.supervisor.endogenous_drive_cycle import (
    EndogenousDriveCycleContext,
    run_endogenous_drive_cycle,
)
from systems.supervisor.endogenous_policy import TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
from systems.supervisor.endogenous_state_repository import EndogenousStateRepository
from systems.supervisor.endogenous_state_projection import (
    derive_corrective_mode,
    project_governance_event_stream,
)
from systems.supervisor.autonomous_chain_store import (
    AutonomousChainExecutionRequest,
    AutonomousChainGitLineage,
    AutonomousChainTask,
)
from systems.supervisor.activity_projection import (
    enforce_auto_drive_input_boundary,
    idle_seconds_since,
    parse_activity_timestamp,
    project_auto_activity_snapshot,
    project_runtime_observation_input,
)
from systems.supervisor.drive_input_evaluation import (
    DriveInputEvaluationConfig,
    evaluate_drive_input_snapshot,
)
from systems.supervisor.autonomous_task_review import (
    build_autonomous_chain_auto_decision,
    is_agent_pull_task,
    normalize_autonomous_chain_decision,
)

logger = logging.getLogger("supervisor")


# ──────────────────────────────────────────────────────────────────────
# Supervisor scene taxonomy (baseline §3.4 / §3.6 / §13.2)
# ──────────────────────────────────────────────────────────────────────
# The supervisor (API-B) is the governance identity of Mem.  It only
# manages API-B judgement state and runs endogenous drive; it never executes
# learning or body-upgrade code.  Therefore the supervisor's `scene`
# field is restricted to the values below.  The Agent (API-A) is the
# only component that may surface "learning" / "execution" scenes.
#
#   idle         - at rest
#   planning     - judging / handing off / denying an API-B judgement item
#   memory       - actively touching long-term memory (Mem internal)
#   drive        - endogenous drive: cognitive evaluation / governance output
#   handoff      - handing a ready execution request to API-A / executor
#   maintenance  - memory-maintenance sweep (long-term memory hygiene)
#   body_switch  - judging a body switch request
#
# Forbidden scenes for the supervisor (API-A territory):
#   "learning"   - the Agent is doing learning work
#   "execution"  - the Agent or executor is doing work
# ──────────────────────────────────────────────────────────────────────
SUPERVISOR_LEGAL_SCENES: frozenset[str] = frozenset(
    {"idle", "planning", "memory", "drive", "handoff", "maintenance"}
)

class PlanningRuntimeMixin:
    """Supervisor planning, activity-guard evaluation, and autonomous-chain orchestration."""
    _ENDOGENOUS_DRIVE_HISTORY_LIMIT = 240
    _ENDOGENOUS_GOVERNANCE_EVENT_LIMIT = 240

    _LM_GOVERNANCE_ACTION_TO_STATUS: Dict[str, str] = {
        "approve": "approved",
        "approved": "approved",
        "defer": "deferred",
        "deferred": "deferred",
        "cancel": "cancelled",
        "cancelled": "cancelled",
        "pause": "paused",
        "paused": "paused",
    }
    _LM_GOVERNANCE_SHADOW_ACTIONS: frozenset[str] = frozenset(
        {"retire", "merge"}
    )

    async def get_governor_history(self, limit: int = 20):
        return {
            "history": self._governor.list_history(limit=limit),
            "latest": self._governor.get_latest(),
        }

    def _endogenous_drive_history_default(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "judgements": [],
            "outcomes": [],
            "strategy_memory": {
                "focus_stats": {},
                "agenda_topic_stats": {},
            },
        }

    def _endogenous_governance_events_default(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "events": [],
        }

    def _endogenous_cognition_state_default(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "state": {
                "status": "uninitialized",
                "enabled": bool(self.config.service_runtime.endogenous_drive_enabled),
                "identity": {
                    "role": "endogenous_supervisory_core",
                    "responsibility": (
                        "Perceive user, system, and self state; then govern "
                        "autonomous direction before execution."
                    ),
                    "execution_scope": "governance_only",
                    "execution_chain_coupled": False,
                },
                "perception": {},
                "world_model": {},
                "self_model": {},
                "governance": {},
                "proposal_cognition": {
                    "summary": "posture=unknown; drift=unknown.",
                    "lm_trace": {
                        "available": False,
                        "status": None,
                        "model_role": None,
                        "charter_core_mission": None,
                        "proposal_count": 0,
                    },
                    "current_candidates": {
                        "count": 0,
                        "lm_generated_count": 0,
                        "average_cognitive_alignment_score": 0.0,
                        "average_reference_alignment_score": 0.0,
                    },
                    "cognitive_control_policy": {},
                    "active_cognitive_posture_profile": {},
                    "meta_cognition_profile": {
                        "available": False,
                        "current_judgement": "",
                        "dominant_constraint": "",
                        "grounding_pressure": "",
                        "dominant_failure_mode": "",
                        "governance_posture": "",
                        "priority_signals": [],
                        "self_iteration_focus": {
                            "domain": None,
                            "hypothesis": None,
                        },
                    },
                    "assessment_trace": {
                        "available": False,
                        "dominant_constraint": None,
                        "current_judgement": None,
                        "why_not_improvement_now_count": 0,
                    },
                    "auxiliary_memory": {
                        "recent_reference_alignment": {
                            "available": False,
                            "average_alignment_score": 0.0,
                            "weak_or_partial_count": 0,
                            "entry_count": 0,
                            "primary_missing_evidence_node": None,
                            "primary_missing_agenda_node": None,
                            "missing_evidence_node_count": 0,
                            "missing_agenda_node_count": 0,
                        },
                        "proposal_drift_memory": {
                            "available": False,
                            "average_score": 0.0,
                            "drift_state": "unknown",
                            "quality_counts": {},
                            "posture_alignment_signal_count": 0,
                            "priority_basis_signal_count": 0,
                            "missing_posture_alignment_count": 0,
                            "missing_priority_basis_count": 0,
                            "posture_alignment_health": "",
                            "priority_basis_health": "",
                            "dominant_posture_conflict_reason": None,
                        },
                        "recent_cognitive_alignment": {
                            "available": False,
                            "average_score": 0.0,
                            "quality_counts": {},
                            "dominant_task_shape": None,
                            "reason_count": 0,
                            "posture_alignment_signal_count": 0,
                            "priority_basis_signal_count": 0,
                            "missing_posture_alignment_count": 0,
                            "missing_priority_basis_count": 0,
                            "entry_count": 0,
                        },
                    },
                },
                "attention_agenda": {
                    "summary": "No endogenous agenda has been formed yet.",
                    "active_count": 0,
                    "entries": [],
                },
                "uncertainty_ledger": {
                    "summary": "No endogenous uncertainty ledger has been formed yet.",
                    "active_count": 0,
                    "highest_risk_domain": None,
                    "entries": [],
                },
                "observation_program": {
                    "summary": "No endogenous observation program has been formed yet.",
                    "active_count": 0,
                    "highest_priority_target": None,
                    "entries": [],
                },
                "meta_governance": {
                    "summary": "No endogenous meta-governance posture has been formed yet.",
                    "mode": "uninitialized",
                    "confidence": 0.0,
                    "drivers": [],
                    "guardrails": [],
                    "stability": "unknown",
                    "context": {
                        "preferred_focus": None,
                        "corrective_mode": None,
                        "observation_target": None,
                        "agenda_topic": None,
                        "uncertainty_domain": None,
                    },
                },
                "strategy_memory": {
                    "focus_stats": {},
                    "agenda_topic_stats": {},
                    "observation_target_stats": {},
                    "meta_governance_stats": {},
                    "context_key": "unknown",
                    "current_context_focus_stats": {},
                    "current_agenda_topic_stats": {},
                    "current_observation_target_stats": {},
                    "current_meta_governance_stats": {},
                },
                "recent_events": [],
            },
        }

    def _endogenous_self_regulation_default(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "dynamic_candidate_throttle_boost": 0.0,
            "dynamic_observation_bias_boost": 0.0,
            "dynamic_truthfulness_bias_boost": 0.0,
            "dynamic_learning_expansion_suppression": 0.0,
            "last_reason": None,
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

    def _load_endogenous_drive_history(self) -> Dict[str, Any]:
        raw = self._endogenous_state_repository.read_object(
            self._endogenous_state_repository.paths.drive_history
        )
        if raw is None:
            return self._endogenous_drive_history_default()
        snapshot = self._endogenous_drive_history_default()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["judgements"] = [
            dict(item)
            for item in list(raw.get("judgements") or [])
            if isinstance(item, dict)
        ]
        snapshot["outcomes"] = [
            dict(item)
            for item in list(raw.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        snapshot["strategy_memory"] = normalize_endogenous_strategy_memory(
            raw.get("strategy_memory")
        )
        return self._trim_endogenous_drive_history(snapshot)

    def _load_endogenous_governance_events(self) -> Dict[str, Any]:
        raw = self._endogenous_state_repository.read_object(
            self._endogenous_state_repository.paths.governance_events
        )
        if raw is None:
            return self._endogenous_governance_events_default()
        snapshot = self._endogenous_governance_events_default()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["events"] = [
            dict(item)
            for item in list(raw.get("events") or [])
            if isinstance(item, dict)
        ]
        return self._trim_endogenous_governance_events(snapshot)

    def _load_endogenous_cognition_state(self) -> Dict[str, Any]:
        raw = self._endogenous_state_repository.read_object(
            self._endogenous_state_repository.paths.cognition_state
        )
        if raw is None:
            return self._endogenous_cognition_state_default()
        snapshot = self._endogenous_cognition_state_default()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["state"] = dict(raw.get("state") or {})
        return snapshot

    def _load_endogenous_self_regulation(self) -> Dict[str, Any]:
        raw = self._endogenous_state_repository.read_object(
            self._endogenous_state_repository.paths.self_regulation
        )
        if raw is None:
            return self._endogenous_self_regulation_default()
        snapshot = self._endogenous_self_regulation_default()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["dynamic_candidate_throttle_boost"] = max(
            0.0, float(raw.get("dynamic_candidate_throttle_boost") or 0.0)
        )
        snapshot["dynamic_observation_bias_boost"] = max(
            0.0, float(raw.get("dynamic_observation_bias_boost") or 0.0)
        )
        snapshot["dynamic_truthfulness_bias_boost"] = max(
            0.0, float(raw.get("dynamic_truthfulness_bias_boost") or 0.0)
        )
        snapshot["dynamic_learning_expansion_suppression"] = max(
            0.0, float(raw.get("dynamic_learning_expansion_suppression") or 0.0)
        )
        snapshot["last_reason"] = raw.get("last_reason")
        return self._decay_endogenous_self_regulation(snapshot)

    def _decay_endogenous_self_regulation(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        decayed = dict(snapshot or {})
        updated_at_raw = str(decayed.get("updated_at") or "").strip()
        if not updated_at_raw:
            return decayed
        try:
            updated_at = datetime.fromisoformat(updated_at_raw)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except Exception:
            return decayed

        now = datetime.now(timezone.utc)
        elapsed_hours = max(0.0, (now - updated_at).total_seconds() / 3600.0)
        if elapsed_hours <= 0.0:
            return decayed

        throttle = float(decayed.get("dynamic_candidate_throttle_boost") or 0.0)
        observation = float(decayed.get("dynamic_observation_bias_boost") or 0.0)
        truthfulness = float(decayed.get("dynamic_truthfulness_bias_boost") or 0.0)
        learning_suppression = float(decayed.get("dynamic_learning_expansion_suppression") or 0.0)
        decay_factor = max(0.0, 1.0 - elapsed_hours / 6.0)

        new_throttle = round(max(0.0, throttle * decay_factor), 4)
        new_observation = round(max(0.0, observation * decay_factor), 4)
        new_truthfulness = round(max(0.0, truthfulness * decay_factor), 4)
        new_learning_suppression = round(max(0.0, learning_suppression * decay_factor), 4)

        if (
            new_throttle == throttle
            and new_observation == observation
            and new_truthfulness == truthfulness
            and new_learning_suppression == learning_suppression
        ):
            return decayed

        decayed["dynamic_candidate_throttle_boost"] = new_throttle
        decayed["dynamic_observation_bias_boost"] = new_observation
        decayed["dynamic_truthfulness_bias_boost"] = new_truthfulness
        decayed["dynamic_learning_expansion_suppression"] = new_learning_suppression
        if (
            new_throttle <= 0.001
            and new_observation <= 0.001
            and new_truthfulness <= 0.001
            and new_learning_suppression <= 0.001
        ):
            decayed["dynamic_candidate_throttle_boost"] = 0.0
            decayed["dynamic_observation_bias_boost"] = 0.0
            decayed["dynamic_truthfulness_bias_boost"] = 0.0
            decayed["dynamic_learning_expansion_suppression"] = 0.0
            decayed["last_reason"] = None
        self._persist_endogenous_self_regulation(decayed)
        return decayed

    def _trim_endogenous_drive_history(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        trimmed = dict(snapshot or {})
        trimmed["version"] = 1
        trimmed["judgements"] = list(trimmed.get("judgements") or [])[: self._ENDOGENOUS_DRIVE_HISTORY_LIMIT]
        trimmed["outcomes"] = list(trimmed.get("outcomes") or [])[: self._ENDOGENOUS_DRIVE_HISTORY_LIMIT]
        trimmed["strategy_memory"] = normalize_endogenous_strategy_memory(
            trimmed.get("strategy_memory")
        )
        return trimmed

    def _trim_endogenous_governance_events(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        trimmed = dict(snapshot or {})
        trimmed["version"] = 1
        events: list[Dict[str, Any]] = []
        seen_event_ids: set[str] = set()
        seen_unconsumed_event_keys: set[str] = set()
        for item in list(trimmed.get("events") or []):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            event_id = str(row.get("event_id") or "").strip()
            if event_id:
                if event_id in seen_event_ids:
                    continue
                seen_event_ids.add(event_id)
            semantic_key = self._endogenous_governance_event_semantic_key(row)
            if semantic_key:
                if semantic_key in seen_unconsumed_event_keys:
                    continue
                seen_unconsumed_event_keys.add(semantic_key)
            events.append(row)
            if len(events) >= self._ENDOGENOUS_GOVERNANCE_EVENT_LIMIT:
                break
        trimmed["events"] = events
        return trimmed

    def _endogenous_governance_event_semantic_key(
        self,
        event: Dict[str, Any],
    ) -> Optional[str]:
        if not str(event.get("event_id") or "").strip():
            return None
        if event.get("consumed_at"):
            return None
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        try:
            payload_key = json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except TypeError:
            payload_key = str(payload)
        parts = (
            str(event.get("event_type") or "").strip().lower(),
            str(event.get("channel") or "").strip().lower(),
            str(event.get("context_key") or "").strip().lower(),
            str(event.get("preferred_focus") or "").strip().lower(),
            str(event.get("message") or "").strip(),
            str(event.get("rationale") or "").strip(),
            payload_key,
        )
        if not any(parts):
            return None
        return "\x1f".join(parts)

    def _persist_endogenous_drive_history(self, snapshot: Dict[str, Any]) -> None:
        payload = self._trim_endogenous_drive_history(snapshot)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._endogenous_state_repository.write_object(
            self._endogenous_state_repository.paths.drive_history, payload
        )

    def _persist_endogenous_governance_events(self, snapshot: Dict[str, Any]) -> None:
        payload = self._trim_endogenous_governance_events(snapshot)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._endogenous_state_repository.write_object(
            self._endogenous_state_repository.paths.governance_events, payload
        )

    def _persist_endogenous_cognition_state(self, state: Dict[str, Any]) -> None:
        payload = self._endogenous_cognition_state_default()
        payload["state"] = dict(state or {})
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._endogenous_state_repository.write_object(
            self._endogenous_state_repository.paths.cognition_state, payload
        )

    def _persist_endogenous_self_regulation(self, snapshot: Dict[str, Any]) -> None:
        payload = dict(snapshot or {})
        payload["version"] = 1
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._endogenous_state_repository.write_object(
            self._endogenous_state_repository.paths.self_regulation, payload
        )

    def _lm_generation_application_state(self) -> LmGenerationApplicationState:
        engine = getattr(self, "_endogenous_drive_engine", None)
        return project_lm_generation_application_state(
            runtime_config=getattr(self.config, "service_runtime", None),
            state_loader=getattr(engine, "get_latest_lm_task_generation_state", None),
        )

    def _derive_cognitive_self_regulation(
        self,
        *,
        drive_history: Dict[str, Any],
        lm_reasoning_state: Dict[str, Any],
        deliberation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        runtime_config = getattr(self.config, "service_runtime", None)
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        cognitive_control_policy_model = getattr(
            charter_model,
            "cognitive_control_policy",
            None,
        )
        if hasattr(cognitive_control_policy_model, "model_dump"):
            policy = cognitive_control_policy_model.model_dump(mode="json")
        else:
            policy = dict(cognitive_control_policy_model or {})
        posture_profile = self._resolve_cognitive_posture_profile(
            policy,
            lm_reasoning_state=lm_reasoning_state,
            drive_history=drive_history,
            deliberation=deliberation,
        )
        regulation = {
            "dynamic_candidate_throttle_boost": 0.0,
            "dynamic_observation_bias_boost": 0.0,
            "dynamic_truthfulness_bias_boost": 0.0,
            "dynamic_learning_expansion_suppression": 0.0,
            "last_reason": None,
        }
        reasons: list[str] = []

        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        evidence_basis = dict(lm_reasoning_state.get("evidence_basis") or {})
        recent_cognitive_alignment = self._build_recent_cognitive_alignment_summary(
            history_snapshot=drive_history,
        )

        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        drift_average_score = self._clamp_endogenous_ratio(
            proposal_drift_memory.get("average_score") or 0.0
        )
        posture_alignment_health = str(
            proposal_drift_memory.get("posture_alignment_health") or ""
        ).strip().lower()
        priority_basis_health = str(
            proposal_drift_memory.get("priority_basis_health") or ""
        ).strip().lower()
        missing_posture_alignment_count = max(
            0,
            int(proposal_drift_memory.get("missing_posture_alignment_count") or 0),
        )
        missing_priority_basis_count = max(
            0,
            int(proposal_drift_memory.get("missing_priority_basis_count") or 0),
        )
        dominant_posture_conflict_reason = str(
            proposal_drift_memory.get("dominant_posture_conflict_reason") or ""
        ).strip().lower()
        reference_alignment_score = self._clamp_endogenous_ratio(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        reference_alignment_available = (
            bool(recent_reference_alignment.get("available"))
            or "average_alignment_score" in recent_reference_alignment
            or weak_reference_count > 0
        )
        readiness_score = self._clamp_endogenous_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
        readiness_available = "self_iteration_readiness_score" in evidence_basis
        weak_or_missing_channels = [
            str(item).strip()
            for item in list(evidence_basis.get("weak_or_missing_channels") or [])[:6]
            if str(item).strip()
        ]
        self_understanding_gaps = [
            str(item).strip()
            for item in list(evidence_basis.get("self_understanding_gaps") or [])[:6]
            if str(item).strip()
        ]
        alignment_average_score = self._clamp_endogenous_ratio(
            recent_cognitive_alignment.get("average_score") or 0.0
        )
        alignment_quality_counts = dict(
            recent_cognitive_alignment.get("quality_counts") or {}
        )
        weak_alignment_count = max(0, int(alignment_quality_counts.get("weak") or 0))
        partial_alignment_count = max(0, int(alignment_quality_counts.get("partial") or 0))
        observation_multiplier = max(
            0.0,
            float(posture_profile.get("observation_multiplier") or 1.0),
        )
        throttle_multiplier = max(
            0.0,
            float(posture_profile.get("throttle_multiplier") or 1.0),
        )
        truthfulness_multiplier = max(
            0.0,
            float(posture_profile.get("truthfulness_multiplier") or 1.0),
        )
        learning_suppression_multiplier = max(
            0.0,
            float(posture_profile.get("learning_suppression_multiplier") or 1.0),
        )
        explanation_missing_threshold = max(
            1,
            int(policy.get("auto_explanation_repair_missing_threshold") or 2),
        )
        explanation_inconsistent_threshold = max(
            1,
            int(policy.get("auto_explanation_repair_inconsistent_threshold") or 1),
        )
        explanation_missing_pressure = max(
            missing_posture_alignment_count,
            missing_priority_basis_count,
        )
        explanation_inconsistent_pressure = 0
        if posture_alignment_health == "inconsistent":
            explanation_inconsistent_pressure += 1
        if priority_basis_health == "inconsistent":
            explanation_inconsistent_pressure += 1

        if drift_state == "drifting":
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("drift_throttle_boost") or 0.0
            ) * throttle_multiplier
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("drift_observation_boost") or 0.0
            ) * observation_multiplier
            regulation["dynamic_learning_expansion_suppression"] += float(
                policy.get("drift_learning_suppression_boost") or 0.0
            ) * learning_suppression_multiplier
            reasons.append("proposal_drift_is_active")
        elif drift_state == "correcting":
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("correcting_throttle_boost") or 0.0
            ) * throttle_multiplier
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("correcting_observation_boost") or 0.0
            ) * observation_multiplier
            regulation["dynamic_learning_expansion_suppression"] += float(
                policy.get("correcting_learning_suppression_boost") or 0.0
            ) * learning_suppression_multiplier
            reasons.append("proposal_drift_is_being_corrected")

        drift_observe_trigger_score = self._clamp_endogenous_ratio(
            float(policy.get("drift_observe_trigger_score") or 0.5)
            + float(posture_profile.get("drift_trigger_delta") or 0.0)
        )
        drift_strong_trigger_score = self._clamp_endogenous_ratio(
            float(policy.get("drift_strong_trigger_score") or 0.45)
            + float(posture_profile.get("drift_trigger_delta") or 0.0)
        )
        if drift_average_score > 0.0 and drift_average_score < drift_observe_trigger_score:
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("low_alignment_throttle_boost") or 0.0
            ) * throttle_multiplier
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("low_alignment_observation_boost") or 0.0
            ) * observation_multiplier
            reasons.append("proposal_alignment_average_is_low")

        if explanation_missing_pressure >= explanation_missing_threshold:
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("explanation_missing_throttle_boost") or 0.0
            ) * throttle_multiplier
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("explanation_missing_observation_boost") or 0.0
            ) * observation_multiplier
            reasons.append("proposal_explanation_memory_is_missing")

        if explanation_inconsistent_pressure >= explanation_inconsistent_threshold:
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("explanation_inconsistent_observation_boost") or 0.0
            ) * observation_multiplier
            regulation["dynamic_truthfulness_bias_boost"] += float(
                policy.get("explanation_inconsistent_truthfulness_boost") or 0.0
            ) * truthfulness_multiplier
            regulation["dynamic_learning_expansion_suppression"] += float(
                policy.get("explanation_inconsistent_learning_suppression_boost") or 0.0
            ) * learning_suppression_multiplier
            if dominant_posture_conflict_reason:
                reasons.append(f"proposal_explanation_conflict:{dominant_posture_conflict_reason}")
            else:
                reasons.append("proposal_explanation_is_inconsistent")

        if recent_cognitive_alignment.get("available"):
            weak_alignment_count_trigger = max(
                1,
                int(policy.get("weak_alignment_count_trigger") or 2),
            )
            if (
                weak_alignment_count >= weak_alignment_count_trigger
                or alignment_average_score < drift_strong_trigger_score
            ):
                regulation["dynamic_candidate_throttle_boost"] += float(
                    policy.get("weak_alignment_throttle_boost") or 0.0
                ) * throttle_multiplier
                regulation["dynamic_observation_bias_boost"] += float(
                    policy.get("weak_alignment_observation_boost") or 0.0
                ) * observation_multiplier
                regulation["dynamic_learning_expansion_suppression"] += float(
                    policy.get("weak_alignment_learning_suppression_boost") or 0.0
                ) * learning_suppression_multiplier
                reasons.append("recent_cognitive_alignment_is_weak")
            elif partial_alignment_count >= 2:
                regulation["dynamic_observation_bias_boost"] += float(
                    policy.get("partial_alignment_observation_boost") or 0.0
                ) * observation_multiplier
                reasons.append("recent_cognitive_alignment_remains_partial")

        reference_alignment_min_score = self._clamp_endogenous_ratio(
            float(policy.get("reference_alignment_min_score") or 0.65)
            + float(posture_profile.get("reference_alignment_delta") or 0.0)
        )
        if reference_alignment_available and reference_alignment_score < reference_alignment_min_score:
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("weak_reference_observation_boost") or 0.0
            ) * observation_multiplier
            regulation["dynamic_truthfulness_bias_boost"] += float(
                policy.get("weak_reference_truthfulness_boost") or 0.0
            ) * truthfulness_multiplier
            reasons.append("reference_alignment_is_not_stable")
        weak_reference_count_trigger = max(
            1,
            int(policy.get("weak_reference_count_trigger") or 2),
        )
        if weak_reference_count >= weak_reference_count_trigger:
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("repeated_weak_reference_throttle_boost") or 0.0
            ) * throttle_multiplier
            regulation["dynamic_truthfulness_bias_boost"] += float(
                policy.get("repeated_weak_reference_truthfulness_boost") or 0.0
            ) * truthfulness_multiplier
            reasons.append("reference_alignment_has_multiple_weak_entries")

        readiness_min_score = self._clamp_endogenous_ratio(
            float(policy.get("readiness_min_score") or 0.52)
            + float(posture_profile.get("readiness_delta") or 0.0)
        )
        if readiness_available and readiness_score < readiness_min_score:
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("low_readiness_throttle_boost") or 0.0
            ) * throttle_multiplier
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("low_readiness_observation_boost") or 0.0
            ) * observation_multiplier
            regulation["dynamic_learning_expansion_suppression"] += float(
                policy.get("low_readiness_learning_suppression_boost") or 0.0
            ) * learning_suppression_multiplier
            reasons.append("self_iteration_readiness_is_low")

        if weak_or_missing_channels:
            channel_penalty = min(
                len(weak_or_missing_channels),
                max(1, int(policy.get("weak_channel_count_observe_cap") or 3)),
            )
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("weak_channel_observation_step") or 0.0
            ) * channel_penalty * observation_multiplier
            regulation["dynamic_truthfulness_bias_boost"] += float(
                policy.get("weak_channel_truthfulness_step") or 0.0
            ) * channel_penalty * truthfulness_multiplier
            reasons.append("weak_evidence_channels_require_more_observation")

        if self_understanding_gaps:
            gap_penalty = min(
                len(self_understanding_gaps),
                max(1, int(policy.get("self_gap_observe_cap") or 3)),
            )
            regulation["dynamic_candidate_throttle_boost"] += float(
                policy.get("self_gap_throttle_step") or 0.0
            ) * gap_penalty * throttle_multiplier
            regulation["dynamic_observation_bias_boost"] += float(
                policy.get("self_gap_observation_step") or 0.0
            ) * gap_penalty * observation_multiplier
            reasons.append("self_understanding_gaps_are_active")

        for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        ):
            regulation[key] = round(
                self._clamp_endogenous_ratio(regulation[key]),
                4,
            )

        if reasons:
            regulation["last_reason"] = "; ".join(reasons[:6])
        return regulation

    def _release_cleared_historical_observation_carryover(
        self,
        *,
        persisted_self_regulation: Dict[str, Any],
        cognitive_self_regulation: Dict[str, Any],
        deliberation: Dict[str, Any],
        lm_reasoning_state: Dict[str, Any],
        drive_history: Dict[str, Any],
    ) -> Dict[str, Any]:
        adjusted = dict(cognitive_self_regulation or {})
        reflection = dict(deliberation.get("reflection") or {})
        perception = dict(deliberation.get("perception") or {})

        if str(reflection.get("dominant_constraint") or "").strip().lower() != "none":
            return adjusted
        if float(
            reflection.get("api_b_judgement_blockage_pressure") or 0.0
        ) >= 0.18:
            return adjusted
        if str(reflection.get("learning_yield_state") or "").strip().lower() not in {"mixed", "strong"}:
            return adjusted
        if max(
            0,
            int(
                perception.get("api_b_judgement_count")
                or 0
            ),
        ) > 0:
            return adjusted
        if max(0, int(perception.get("stale_backlog_count") or 0)) > 0:
            return adjusted
        if max(0, int(perception.get("pending_review_count") or 0)) > 0:
            return adjusted
        if (
            max(0, int(perception.get("correction_signals") or 0))
            >= TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
        ):
            return adjusted

        posture_profile = self._current_active_cognitive_posture_profile(
            lm_reasoning_state=lm_reasoning_state,
            history_snapshot=drive_history,
            deliberation=deliberation,
        )
        if str(posture_profile.get("name") or "").strip().lower() != "observe_first":
            return adjusted

        persisted_observation = float(
            persisted_self_regulation.get("dynamic_observation_bias_boost") or 0.0
        )
        persisted_throttle = float(
            persisted_self_regulation.get("dynamic_candidate_throttle_boost") or 0.0
        )
        persisted_learning_suppression = float(
            persisted_self_regulation.get("dynamic_learning_expansion_suppression") or 0.0
        )
        if max(
            persisted_observation,
            persisted_throttle,
            persisted_learning_suppression,
        ) < 0.08:
            return adjusted

        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        evidence_basis = dict(lm_reasoning_state.get("evidence_basis") or {})

        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        drift_average_score = self._clamp_endogenous_ratio(
            proposal_drift_memory.get("average_score") or 0.0
        )
        reference_alignment_score = self._clamp_endogenous_ratio(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        readiness_score = self._clamp_endogenous_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
        weak_channel_count = len(
            [
                str(item).strip()
                for item in list(evidence_basis.get("weak_or_missing_channels") or [])[:6]
                if str(item).strip()
            ]
        )
        if drift_state != "correcting":
            return adjusted
        if drift_average_score < 0.42:
            return adjusted
        if reference_alignment_score < 0.58:
            return adjusted
        if weak_reference_count > 1:
            return adjusted
        if readiness_score < 0.48:
            return adjusted
        if weak_channel_count > 1:
            return adjusted

        observation_boost = float(
            cognitive_self_regulation.get("dynamic_observation_bias_boost") or 0.0
        )
        throttle_boost = float(
            cognitive_self_regulation.get("dynamic_candidate_throttle_boost") or 0.0
        )
        learning_suppression = float(
            cognitive_self_regulation.get("dynamic_learning_expansion_suppression") or 0.0
        )
        if max(observation_boost, throttle_boost, learning_suppression) < 0.12:
            return adjusted

        # Historical underdelivery is already cleared here, so do not let a
        # fresh corrective pass restack observation/throttle pressure on top
        # of decaying persisted guard carryover.
        adjusted["dynamic_observation_bias_boost"] = 0.0
        adjusted["dynamic_candidate_throttle_boost"] = 0.0
        adjusted["dynamic_learning_expansion_suppression"] = 0.0
        reason = str(adjusted.get("last_reason") or "").strip()
        release_reason = "cleared_historical_window_releases_composite_observation_carryover"
        adjusted["last_reason"] = (
            f"{reason}; {release_reason}" if reason else release_reason
        )
        return adjusted

    def _resolve_cognitive_posture_profile(
        self,
        policy: Dict[str, Any],
        *,
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
        drive_history: Optional[Dict[str, Any]] = None,
        deliberation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selection_mode = str(policy.get("posture_selection_mode") or "auto").strip().lower()
        profile_name = str(policy.get("active_posture_profile") or "balanced").strip().lower()
        profiles = dict(policy.get("posture_profiles") or {})
        auto_selection_reason = "manual_selection"
        if selection_mode != "manual":
            profile_name, auto_selection_reason = self._select_cognitive_posture_profile_name(
                policy=policy,
                lm_reasoning_state=lm_reasoning_state or {},
                drive_history=drive_history or {},
                deliberation=deliberation or {},
            )
        selected = profiles.get(profile_name)
        if not isinstance(selected, dict):
            profile_name = "balanced"
            selected = profiles.get(profile_name) or {}
        resolved = dict(selected or {})
        resolved["name"] = profile_name
        resolved["selection_mode"] = selection_mode
        resolved["selection_reason"] = auto_selection_reason
        return resolved

    def _select_cognitive_posture_profile_name(
        self,
        *,
        policy: Dict[str, Any],
        lm_reasoning_state: Dict[str, Any],
        drive_history: Dict[str, Any],
        deliberation: Dict[str, Any],
    ) -> tuple[str, str]:
        perception = dict(deliberation.get("perception") or {})
        reflection = dict(deliberation.get("reflection") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        evidence_basis = dict(lm_reasoning_state.get("evidence_basis") or {})
        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_cognitive_alignment = self._build_recent_cognitive_alignment_summary(
            history_snapshot=drive_history,
        )

        correction_signals = max(0, int(perception.get("correction_signals") or 0))
        active_sessions = max(0, int(perception.get("active_sessions") or 0))
        weak_reference_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        weak_channels = [
            str(item).strip()
            for item in list(evidence_basis.get("weak_or_missing_channels") or [])[:6]
            if str(item).strip()
        ]
        self_gaps = [
            str(item).strip()
            for item in list(evidence_basis.get("self_understanding_gaps") or [])[:6]
            if str(item).strip()
        ]
        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        posture_alignment_health = str(
            proposal_drift_memory.get("posture_alignment_health") or ""
        ).strip().lower()
        priority_basis_health = str(
            proposal_drift_memory.get("priority_basis_health") or ""
        ).strip().lower()
        missing_posture_alignment_count = max(
            0,
            int(proposal_drift_memory.get("missing_posture_alignment_count") or 0),
        )
        missing_priority_basis_count = max(
            0,
            int(proposal_drift_memory.get("missing_priority_basis_count") or 0),
        )
        dominant_posture_conflict_reason = str(
            proposal_drift_memory.get("dominant_posture_conflict_reason") or ""
        ).strip().lower()
        readiness_score = self._clamp_endogenous_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
        alignment_average_score = self._clamp_endogenous_ratio(
            recent_cognitive_alignment.get("average_score") or 0.0
        )
        dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()

        service_active_sessions_threshold = max(
            0,
            int(policy.get("auto_service_active_sessions_threshold") or 1),
        )
        truthfulness_signal_threshold = max(
            1,
            int(
                policy.get("auto_truthfulness_correction_signal_threshold")
                or TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD
            ),
        )
        evidence_repair_signal_threshold = max(
            1,
            int(policy.get("auto_evidence_repair_signal_threshold") or 3),
        )
        explanation_missing_threshold = max(
            1,
            int(policy.get("auto_explanation_repair_missing_threshold") or 2),
        )
        explanation_inconsistent_threshold = max(
            1,
            int(policy.get("auto_explanation_repair_inconsistent_threshold") or 1),
        )
        explanation_missing_pressure = max(
            missing_posture_alignment_count,
            missing_priority_basis_count,
        )
        explanation_inconsistent_pressure = 0
        if posture_alignment_health == "inconsistent":
            explanation_inconsistent_pressure += 1
        if priority_basis_health == "inconsistent":
            explanation_inconsistent_pressure += 1

        if active_sessions >= service_active_sessions_threshold:
            return "conservative", "service_pressure_requires_conservative_posture"
        if correction_signals >= truthfulness_signal_threshold:
            return "truthfulness_first", "truthfulness_signals_are_elevated"
        if (
            explanation_missing_pressure >= explanation_missing_threshold
            and explanation_inconsistent_pressure >= explanation_inconsistent_threshold
        ):
            return "evidence_repair_first", "explanation_quality_requires_evidence_repair"
        if explanation_missing_pressure >= explanation_missing_threshold:
            return "observe_first", "missing_explanation_memory_requires_observation"
        if explanation_inconsistent_pressure >= explanation_inconsistent_threshold:
            if "truthfulness" in dominant_posture_conflict_reason or "reference_alignment" in dominant_posture_conflict_reason:
                return "truthfulness_first", "explanation_conflict_requires_truthfulness_repair"
            return "observe_first", "explanation_conflict_requires_observation"
        if (
            weak_reference_count >= evidence_repair_signal_threshold
            or len(weak_channels) >= evidence_repair_signal_threshold
            or "reference_alignment_is_unstable" in self_gaps
        ):
            return "evidence_repair_first", "evidence_repair_pressure_is_elevated"
        if (
            drift_state in {"drifting", "correcting"}
            or alignment_average_score < self._clamp_endogenous_ratio(
                policy.get("drift_observe_trigger_score") or 0.5
            )
            or readiness_score < self._clamp_endogenous_ratio(
                policy.get("readiness_min_score") or 0.52
            )
            or dominant_constraint in {"api_b_judgement_blockage", "historical_underdelivery"}
        ):
            return "observe_first", "drift_or_readiness_requires_observation"
        return "balanced", "balanced_posture_is_sufficient"

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

    def _drive_input_fields_from_decision_context(
        self,
        decision_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        decision_context = dict(decision_context or {})
        return self._build_drive_input_response_fields(
            drive_input=dict(decision_context.get("drive_input") or {}),
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
        return self._project_drive_input_snapshot(drive_input)

    def _build_endogenous_cognition_state(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        governance_event_stream: Dict[str, Any],
        self_regulation: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        history_snapshot = self._load_endogenous_drive_history()
        perception = dict(deliberation.get("perception") or {})
        world_model = dict(deliberation.get("world_model") or {})
        reflection = dict(deliberation.get("reflection") or {})
        adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
        drive_posture = self._drive_posture_signal_from_deliberation(deliberation)
        context_key = self._derive_endogenous_context_key(deliberation=deliberation)
        strategy_memory = normalize_endogenous_strategy_memory(
            history_snapshot.get("strategy_memory")
        )
        corrective_mode = derive_corrective_mode(self_regulation)
        attention_agenda = build_attention_agenda_projection(
            deliberation=deliberation,
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
        )
        uncertainty_ledger = build_uncertainty_ledger_projection(
            deliberation=deliberation,
            governance_channels=governance_channels,
            self_regulation=self_regulation,
        )
        observation_program = self._build_endogenous_observation_program(
            uncertainty_ledger=uncertainty_ledger,
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
            history=history_snapshot,
            context_key=context_key,
        )
        strategy_memory = normalize_endogenous_strategy_memory(
            history_snapshot.get("strategy_memory")
        )
        meta_governance = self._build_endogenous_meta_governance(
            cognition_state_seed={
                "perception": perception,
                "world_model": world_model,
                "reflection": reflection,
                "adaptive_policy": adaptive_policy,
                "corrective_mode": corrective_mode,
                "attention_agenda": attention_agenda,
                "uncertainty_ledger": uncertainty_ledger,
                "observation_program": observation_program,
            },
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
            context_key=context_key,
            self_regulation=self_regulation,
            history=history_snapshot,
        )
        judgement_core = build_judgement_core_projection(
            deliberation=deliberation,
            governance_channels=governance_channels,
            attention_agenda=attention_agenda,
            uncertainty_ledger=uncertainty_ledger,
            observation_program=observation_program,
            meta_governance=meta_governance,
        )
        proposal_cognition = self._build_endogenous_proposal_cognition(
            history_snapshot=history_snapshot,
            candidate_items=candidate_items,
            deliberation=deliberation,
            lm_reasoning_state=lm_reasoning_state,
        )
        return build_cognition_state_projection(
            enabled=bool(self.config.service_runtime.endogenous_drive_enabled),
            deliberation=deliberation,
            governance_channels=governance_channels,
            governance_event_stream=governance_event_stream,
            self_regulation=self_regulation,
            drive_posture=drive_posture,
            context_key=context_key,
            strategy_memory=strategy_memory,
            corrective_mode=corrective_mode,
            attention_agenda=attention_agenda,
            uncertainty_ledger=uncertainty_ledger,
            observation_program=observation_program,
            meta_governance=meta_governance,
            judgement_core=judgement_core,
            proposal_cognition=proposal_cognition,
        )

    def _build_endogenous_proposal_cognition(
        self,
        *,
        history_snapshot: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
        deliberation: Optional[Dict[str, Any]] = None,
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # This block is an auxiliary observation/tracking layer.  The main drive
        # judgement stays in judgement_core, attention_agenda, and meta_governance.
        if lm_reasoning_state is None:
            lm_reasoning_state = self._lm_generation_application_state().reasoning_state
        else:
            lm_reasoning_state = dict(lm_reasoning_state)

        task_type_priors = dict(lm_reasoning_state.get("task_type_priors") or {})
        if not task_type_priors:
            task_shape_hint = dict(lm_reasoning_state.get("task_shape_hint") or {})
            shape = str(task_shape_hint.get("shape") or "").strip()
            alternatives = [
                {
                    "task_type": str(item.get("task_type") or item.get("shape") or "").strip(),
                    "score": item.get("score"),
                    "reasons": list(item.get("reasons") or [])[:3],
                }
                for item in list(task_shape_hint.get("alternatives") or [])[:5]
                if isinstance(item, dict)
                and str(item.get("task_type") or item.get("shape") or "").strip()
            ]
            if shape or alternatives:
                task_type_priors = {
                    "top_priority_task_type": shape,
                    "top_priority_score": task_shape_hint.get("score"),
                    "priors": alternatives,
                }
        meta_cognition_profile = dict(
            lm_reasoning_state.get("meta_cognition_profile") or {}
        )
        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        cognitive_assessment_memory = dict(
            lm_reasoning_state.get("cognitive_assessment_memory") or {}
        )
        if not cognitive_assessment_memory:
            cognitive_assessment_memory = self._build_recent_lm_cognitive_assessment_summary(
                history_snapshot=history_snapshot,
            )
        if not recent_reference_alignment:
            recent_reference_alignment = self._build_recent_reference_alignment_summary(
                history_snapshot=history_snapshot,
            )
        self_iteration_trend_memory = self._build_recent_self_iteration_trend_summary(
            history_snapshot=history_snapshot,
        )
        switch_self_regulation_memory = self._build_recent_switch_self_regulation_summary(
            history_snapshot=history_snapshot,
        )
        post_task_effect_memory = self._build_recent_post_task_effect_summary(
            history_snapshot=history_snapshot,
        )
        if not meta_cognition_profile:
            meta_cognition_profile = self._build_recent_meta_cognition_profile_summary(
                cognitive_assessment_memory=cognitive_assessment_memory,
                self_iteration_trend_memory=self_iteration_trend_memory,
                switch_self_regulation_memory=switch_self_regulation_memory,
                post_task_effect_memory=post_task_effect_memory,
                proposal_drift_memory=proposal_drift_memory,
                task_type_priors=task_type_priors,
                recent_reference_alignment=recent_reference_alignment,
            )
        self_iteration_hypotheses = dict(
            lm_reasoning_state.get("self_iteration_hypotheses") or {}
        )
        if not self_iteration_hypotheses:
            dominant_hypothesis = str(
                cognitive_assessment_memory.get("self_iteration_hypothesis")
                or self_iteration_trend_memory.get("dominant_hypothesis")
                or ""
            ).strip()
            hypothesis_count = max(
                1 if dominant_hypothesis else 0,
                max(0, int(cognitive_assessment_memory.get("self_iteration_hypothesis_count") or 0)),
                max(0, int(self_iteration_trend_memory.get("hypothesis_count") or 0)),
            )
            self_iteration_hypotheses = {
                "available": bool(dominant_hypothesis),
                "dominant_hypothesis": dominant_hypothesis,
                "top_target_domain": str(
                    meta_cognition_profile.get("top_self_iteration_domain")
                    or self_iteration_trend_memory.get("dominant_target")
                    or cognitive_assessment_memory.get("self_iteration_target")
                    or ""
                ).strip(),
                "hypothesis_count": hypothesis_count,
            }
        recent_cognitive_alignment = self._build_recent_cognitive_alignment_summary(
            history_snapshot=history_snapshot,
        )
        current_candidates = self._build_current_candidate_cognition_summary(
            candidate_items=candidate_items,
        )
        active_cognitive_posture_profile = self._current_active_cognitive_posture_profile(
            lm_reasoning_state=lm_reasoning_state,
            history_snapshot=history_snapshot,
            deliberation=deliberation,
        )
        compact_memory = compact_proposal_memory(
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
            recent_cognitive_alignment=recent_cognitive_alignment,
            cognitive_assessment_memory=cognitive_assessment_memory,
            self_iteration_hypotheses=self_iteration_hypotheses,
            self_iteration_trend_memory=self_iteration_trend_memory,
            switch_self_regulation_memory=switch_self_regulation_memory,
            post_task_effect_memory=post_task_effect_memory,
        )

        return build_proposal_cognition_projection(
            lm_reasoning_state=lm_reasoning_state,
            cognitive_control_policy=self._current_cognitive_control_policy(),
            active_cognitive_posture_profile=active_cognitive_posture_profile,
            meta_cognition_profile=meta_cognition_profile,
            cognitive_assessment_memory=cognitive_assessment_memory,
            compact_memory=compact_memory,
            current_candidates=current_candidates,
        )


    def _build_recent_reference_alignment_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        entry_count = 0
        score_total = 0.0
        weak_or_partial_count = 0
        missing_evidence_counts: Dict[str, int] = {}
        missing_agenda_counts: Dict[str, int] = {}

        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            reference_alignment = outcome.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")
            if not isinstance(reference_alignment, dict) or not reference_alignment:
                continue
            entry_count += 1
            score_total += self._clamp_endogenous_ratio(
                reference_alignment.get("alignment_score") or 0.0
            )
            quality = str(reference_alignment.get("alignment_quality") or "").strip().lower()
            if quality in {"weak", "partial", "drifted"}:
                weak_or_partial_count += 1
            for node in list(reference_alignment.get("missing_evidence_nodes") or [])[:4]:
                node_name = str(node).strip()
                if node_name:
                    missing_evidence_counts[node_name] = missing_evidence_counts.get(node_name, 0) + 1
            for node in list(reference_alignment.get("missing_agenda_nodes") or [])[:4]:
                node_name = str(node).strip()
                if node_name:
                    missing_agenda_counts[node_name] = missing_agenda_counts.get(node_name, 0) + 1
            if entry_count >= 4:
                break

        if entry_count <= 0:
            return {
                "available": False,
                "summary": "No recent reference alignment is available yet.",
            }

        def _dominant_key(counts: Dict[str, int]) -> str:
            if not counts:
                return ""
            return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

        average_alignment_score = self._clamp_endogenous_ratio(score_total / entry_count)
        missing_evidence_node_count = sum(missing_evidence_counts.values())
        missing_agenda_node_count = sum(missing_agenda_counts.values())
        return {
            "available": True,
            "entry_count": entry_count,
            "average_alignment_score": round(average_alignment_score, 4),
            "weak_or_partial_count": weak_or_partial_count,
            "primary_missing_evidence_node": _dominant_key(missing_evidence_counts) or None,
            "primary_missing_agenda_node": _dominant_key(missing_agenda_counts) or None,
            "missing_evidence_node_count": missing_evidence_node_count,
            "missing_agenda_node_count": missing_agenda_node_count,
            "summary": (
                "Recent reference alignment from history remains available; "
                f"entries={entry_count}; "
                f"avg_alignment={average_alignment_score:.2f}; "
                f"weak_or_partial={weak_or_partial_count}; "
                f"missing_evidence={missing_evidence_node_count}; "
                f"missing_agenda={missing_agenda_node_count}."
            ),
        }

    def _current_cognitive_control_policy(self) -> Dict[str, Any]:
        runtime_config = getattr(self.config, "service_runtime", None)
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        policy_model = getattr(charter_model, "cognitive_control_policy", None)
        if hasattr(policy_model, "model_dump"):
            return policy_model.model_dump(mode="json")
        return dict(policy_model or {})

    def _build_recent_lm_cognitive_assessment_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        current_judgement_counts: Dict[str, int] = {}
        dominant_constraint_counts: Dict[str, int] = {}
        why_not_improvement_counts: Dict[str, int] = {}
        self_iteration_target_counts: Dict[str, int] = {}
        self_iteration_hypothesis_counts: Dict[str, int] = {}
        stay_switch_counts: Dict[str, int] = {}
        switch_reason_counts: Dict[str, int] = {}
        entry_count = 0

        for outcome in outcomes[:12]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict) or not assessment:
                continue

            current_judgement = str(assessment.get("current_judgement") or "").strip()
            dominant_constraint = str(assessment.get("dominant_constraint") or "").strip()
            why_not_improvement_now = [
                str(item).strip()
                for item in list(assessment.get("why_not_improvement_now") or [])[:3]
                if str(item).strip()
            ]
            stay_or_switch = str(assessment.get("stay_or_switch") or "").strip().lower()
            switch_reason = str(assessment.get("switch_reason") or "").strip()
            self_iteration_target = str(
                assessment.get("self_iteration_target") or ""
            ).strip()
            self_iteration_hypothesis = str(
                assessment.get("self_iteration_hypothesis") or ""
            ).strip()
            if current_judgement:
                current_judgement_counts[current_judgement] = (
                    current_judgement_counts.get(current_judgement, 0) + 1
                )
            if dominant_constraint:
                dominant_constraint_counts[dominant_constraint] = (
                    dominant_constraint_counts.get(dominant_constraint, 0) + 1
                )
            for item in why_not_improvement_now:
                why_not_improvement_counts[item] = (
                    why_not_improvement_counts.get(item, 0) + 1
                )
            if self_iteration_target:
                self_iteration_target_counts[self_iteration_target] = (
                    self_iteration_target_counts.get(self_iteration_target, 0) + 1
                )
            if self_iteration_hypothesis:
                self_iteration_hypothesis_counts[self_iteration_hypothesis] = (
                    self_iteration_hypothesis_counts.get(self_iteration_hypothesis, 0) + 1
                )
            if stay_or_switch in {"stay", "switch"}:
                stay_switch_counts[stay_or_switch] = (
                    stay_switch_counts.get(stay_or_switch, 0) + 1
                )
            if switch_reason:
                switch_reason_counts[switch_reason] = (
                    switch_reason_counts.get(switch_reason, 0) + 1
                )
            entry_count += 1
            if entry_count >= 4:
                break

        if not entry_count:
            return {
                "available": False,
                "summary": "当前还没有可用的近期 LM 认知评估记忆。",
            }

        def _dominant(counts: Dict[str, int]) -> str:
            if not counts:
                return ""
            return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]

        dominant_constraint = ""
        if dominant_constraint_counts:
            dominant_constraint = sorted(
                dominant_constraint_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[0][0]
        return {
            "available": True,
            "dominant_constraint": dominant_constraint or None,
            "current_judgement": _dominant(current_judgement_counts) or None,
            "current_judgement_count": len(current_judgement_counts),
            "why_not_improvement_now": _dominant(why_not_improvement_counts) or None,
            "why_not_improvement_now_count": len(why_not_improvement_counts),
            "self_iteration_target": _dominant(self_iteration_target_counts) or None,
            "self_iteration_target_count": len(self_iteration_target_counts),
            "self_iteration_hypothesis": _dominant(self_iteration_hypothesis_counts) or None,
            "self_iteration_hypothesis_count": len(self_iteration_hypothesis_counts),
            "stay_or_switch": _dominant(stay_switch_counts) or None,
            "stay_or_switch_count": len(stay_switch_counts),
            "switch_reason": _dominant(switch_reason_counts) or None,
            "switch_reason_count": len(switch_reason_counts),
            "entry_count": entry_count,
            "summary": (
                "Recent LM cognitive assessments remain available from history; "
                f"dominant constraint={dominant_constraint or 'unknown'}."
            ),
        }

    def _build_recent_self_iteration_trend_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        target_counts: Dict[str, int] = {}
        hypothesis_counts: Dict[str, int] = {}
        stay_switch_counts: Dict[str, int] = {}
        switch_reason_counts: Dict[str, int] = {}
        recent_targets: list[str] = []
        entry_count = 0

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict) or not assessment:
                continue
            target = str(assessment.get("self_iteration_target") or "").strip()
            hypothesis = str(assessment.get("self_iteration_hypothesis") or "").strip()
            stay_or_switch = str(assessment.get("stay_or_switch") or "").strip().lower()
            switch_reason = str(assessment.get("switch_reason") or "").strip()
            if not target and not hypothesis:
                continue
            if target:
                target_counts[target] = target_counts.get(target, 0) + 1
                recent_targets.append(target)
            if hypothesis:
                hypothesis_counts[hypothesis] = hypothesis_counts.get(hypothesis, 0) + 1
            if stay_or_switch in {"stay", "switch"}:
                stay_switch_counts[stay_or_switch] = (
                    stay_switch_counts.get(stay_or_switch, 0) + 1
                )
            if switch_reason:
                switch_reason_counts[switch_reason] = (
                    switch_reason_counts.get(switch_reason, 0) + 1
                )
            entry_count += 1
            if entry_count >= 6:
                break

        if not entry_count:
            return {
                "available": False,
                "summary": "No recent self-iteration trend memory is available yet.",
            }

        ranked_targets = [
            item
            for item, _count in sorted(
                target_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        ranked_hypotheses = [
            item
            for item, _count in sorted(
                hypothesis_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        dominant_target = ranked_targets[0] if ranked_targets else ""
        unique_recent_targets = {
            item for item in recent_targets[:4] if str(item or "").strip()
        }
        target_stability = "mixed"
        if len(unique_recent_targets) <= 1 and dominant_target:
            target_stability = "stable"
        elif len(unique_recent_targets) >= 3:
            target_stability = "volatile"
        trend_state = "exploring"
        dominant_count = target_counts.get(dominant_target, 0) if dominant_target else 0
        if dominant_target and dominant_count >= 3 and target_stability == "stable":
            trend_state = "locked"
        elif dominant_target and dominant_count >= 2:
            trend_state = "consolidating"
        elif target_stability == "volatile":
            trend_state = "searching"
        return {
            "available": True,
            "dominant_target": dominant_target or None,
            "dominant_hypothesis": ranked_hypotheses[0] if ranked_hypotheses else None,
            "trend_state": trend_state,
            "target_stability": target_stability,
            "target_count": len(target_counts),
            "hypothesis_count": len(hypothesis_counts),
            "dominant_stay_or_switch": (
                sorted(stay_switch_counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
                if stay_switch_counts
                else None
            ),
            "stay_or_switch_count": len(stay_switch_counts),
            "dominant_switch_reason": (
                sorted(switch_reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[0][0]
                if switch_reason_counts
                else None
            ),
            "switch_reason_count": len(switch_reason_counts),
            "entry_count": entry_count,
            "summary": (
                "Recent self-iteration trend favors "
                f"{dominant_target or 'unknown'}; trend_state={trend_state}; "
                f"target_stability={target_stability}."
            ),
        }

    def _build_recent_switch_self_regulation_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        switch_quality_scores: list[float] = []
        stay_quality_scores: list[float] = []

        for outcome in outcomes[:16]:
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict) or not assessment:
                continue
            decision = str(assessment.get("stay_or_switch") or "").strip().lower()
            if decision not in {"stay", "switch"}:
                continue
            quality_score = self._clamp_endogenous_ratio(outcome.get("quality_score") or 0.0)
            if decision == "switch":
                switch_quality_scores.append(quality_score)
            else:
                stay_quality_scores.append(quality_score)

        if not switch_quality_scores and not stay_quality_scores:
            return {
                "available": False,
                "summary": "No switch self-regulation memory is available yet.",
            }

        def _avg(values: list[float]) -> float:
            if not values:
                return 0.0
            return self._clamp_endogenous_ratio(sum(values) / len(values))

        average_switch_quality = _avg(switch_quality_scores)
        average_stay_quality = _avg(stay_quality_scores)
        preferred_switch_bias = "balanced"
        if switch_quality_scores and stay_quality_scores:
            if average_switch_quality >= average_stay_quality + 0.12:
                preferred_switch_bias = "switch"
            elif average_stay_quality >= average_switch_quality + 0.12:
                preferred_switch_bias = "stay"
        elif switch_quality_scores:
            preferred_switch_bias = "switch"
        elif stay_quality_scores:
            preferred_switch_bias = "stay"

        return {
            "available": True,
            "preferred_switch_bias": preferred_switch_bias,
            "switch_effectiveness": (
                "strong" if average_switch_quality >= 0.65 else "weak"
            )
            if switch_quality_scores
            else "unknown",
            "stay_effectiveness": (
                "strong" if average_stay_quality >= 0.65 else "weak"
            )
            if stay_quality_scores
            else "unknown",
            "average_switch_quality": round(average_switch_quality, 4),
            "average_stay_quality": round(average_stay_quality, 4),
            "summary": (
                "Recent stay/switch outcomes suggest "
                f"preferred_bias={preferred_switch_bias}; "
                f"switch_quality={average_switch_quality:.2f}; "
                f"stay_quality={average_stay_quality:.2f}."
            ),
        }

    def _build_recent_post_task_effect_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        quality_scores: list[float] = []
        cognitive_scores: list[float] = []
        reference_scores: list[float] = []
        target_effect_counts: Dict[str, int] = {}

        for outcome in outcomes[:16]:
            event_type = str(outcome.get("event_type") or "").strip().lower()
            if event_type in {"", "planned"}:
                continue
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            cognitive_alignment = outcome.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            reference_alignment = outcome.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")
            assessment = outcome.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = metadata.get("llm_cognitive_assessment")
            if not isinstance(assessment, dict):
                assessment = evidence.get("llm_cognitive_assessment")
            target = str((assessment or {}).get("self_iteration_target") or "").strip()
            quality_score = self._clamp_endogenous_ratio(outcome.get("quality_score") or 0.0)
            cognitive_score = self._clamp_endogenous_ratio(
                (cognitive_alignment or {}).get("score") or 0.0
            )
            reference_score = self._clamp_endogenous_ratio(
                (reference_alignment or {}).get("alignment_score") or 0.0
            )
            if not quality_score and not cognitive_score and not reference_score:
                continue
            quality_scores.append(quality_score)
            cognitive_scores.append(cognitive_score)
            reference_scores.append(reference_score)
            if target:
                effect_label = "helped" if quality_score >= 0.65 and cognitive_score >= 0.55 else "unclear"
                if quality_score < 0.4 or reference_score < 0.4:
                    effect_label = "hurt"
                key = f"{target}:{effect_label}"
                target_effect_counts[key] = target_effect_counts.get(key, 0) + 1

        if not quality_scores and not cognitive_scores and not reference_scores:
            return {
                "available": False,
                "summary": "No recent post-task effect memory is available yet.",
            }

        def _avg(values: list[float]) -> float:
            if not values:
                return 0.0
            return self._clamp_endogenous_ratio(sum(values) / len(values))

        average_quality_score = _avg(quality_scores)
        average_cognitive_alignment_score = _avg(cognitive_scores)
        average_reference_alignment_score = _avg(reference_scores)
        effect_direction = "mixed"
        if (
            average_quality_score >= 0.65
            and average_cognitive_alignment_score >= 0.55
            and average_reference_alignment_score >= 0.55
        ):
            effect_direction = "improving"
        elif (
            average_quality_score < 0.4
            or average_cognitive_alignment_score < 0.4
            or average_reference_alignment_score < 0.4
        ):
            effect_direction = "degrading"
        dominant_target_effect = ""
        if target_effect_counts:
            dominant_target_effect = sorted(
                target_effect_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[0][0]
        return {
            "available": True,
            "effect_direction": effect_direction,
            "average_quality_score": round(average_quality_score, 4),
            "average_cognitive_alignment_score": round(
                average_cognitive_alignment_score,
                4,
            ),
            "average_reference_alignment_score": round(
                average_reference_alignment_score,
                4,
            ),
            "dominant_target_effect": dominant_target_effect or None,
            "summary": (
                "Recent post-task effects appear "
                f"{effect_direction}; avg_quality={average_quality_score:.2f}; "
                f"avg_cognitive_alignment={average_cognitive_alignment_score:.2f}; "
                f"avg_reference_alignment={average_reference_alignment_score:.2f}."
            ),
        }

    def _build_recent_meta_cognition_profile_summary(
        self,
        *,
        cognitive_assessment_memory: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        switch_self_regulation_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
        task_type_priors: Dict[str, Any],
        recent_reference_alignment: Dict[str, Any],
    ) -> Dict[str, Any]:
        current_judgement = str(
            cognitive_assessment_memory.get("current_judgement") or ""
        ).strip()
        dominant_constraint = str(
            cognitive_assessment_memory.get("dominant_constraint") or ""
        ).strip()
        top_self_iteration_domain = str(
            cognitive_assessment_memory.get("self_iteration_target")
            or self_iteration_trend_memory.get("dominant_target")
            or ""
        ).strip()
        top_self_iteration_hypothesis = str(
            cognitive_assessment_memory.get("self_iteration_hypothesis")
            or self_iteration_trend_memory.get("dominant_hypothesis")
            or ""
        ).strip()
        stay_or_switch_bias = str(
            self_iteration_trend_memory.get("dominant_stay_or_switch")
            or switch_self_regulation_memory.get("preferred_switch_bias")
            or ""
        ).strip()
        if stay_or_switch_bias == "balanced":
            stay_or_switch_bias = ""
        switch_bias_effectiveness = str(
            switch_self_regulation_memory.get("switch_effectiveness") or ""
        ).strip()
        if stay_or_switch_bias == "stay":
            switch_bias_effectiveness = str(
                switch_self_regulation_memory.get("stay_effectiveness") or ""
            ).strip()
        recent_effect_direction = str(
            post_task_effect_memory.get("effect_direction") or ""
        ).strip()
        grounding_pressure = "low"
        alignment_available = bool(recent_reference_alignment.get("available")) or any(
            key in recent_reference_alignment
            for key in ("average_alignment_score", "weak_or_partial_count")
        )
        weak_or_partial_count = max(
            0,
            int(recent_reference_alignment.get("weak_or_partial_count") or 0),
        )
        average_alignment_score = self._clamp_endogenous_ratio(
            recent_reference_alignment.get("average_alignment_score") or 0.0
        )
        if alignment_available:
            if weak_or_partial_count >= 2 or average_alignment_score < 0.4:
                grounding_pressure = "high"
            elif weak_or_partial_count >= 1 or average_alignment_score < 0.65:
                grounding_pressure = "medium"

        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip().lower()
        dominant_failure_mode = ""
        if grounding_pressure == "high":
            dominant_failure_mode = "grounding_instability"
        elif drift_state in {"drifting", "correcting"}:
            dominant_failure_mode = "proposal_selection_drift"
        elif recent_effect_direction == "degrading":
            dominant_failure_mode = "self_iteration_not_improving_outcomes"
        elif dominant_constraint:
            dominant_failure_mode = dominant_constraint

        top_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
        why_not_improvement_now = str(
            cognitive_assessment_memory.get("why_not_improvement_now") or ""
        ).strip()
        governance_posture = "review"
        if grounding_pressure == "high":
            governance_posture = "observation_or_review"
        elif recent_effect_direction == "degrading":
            governance_posture = "review"
        elif why_not_improvement_now:
            governance_posture = "review"
        elif current_judgement and any(
            token in current_judgement.lower()
            for token in ("review", "observe", "observation", "grounding")
        ):
            governance_posture = "review"
        elif top_task_type in {"observation", "review"}:
            governance_posture = top_task_type

        has_substantive_profile = any(
            [
                current_judgement,
                dominant_constraint,
                top_self_iteration_domain,
                top_self_iteration_hypothesis,
                stay_or_switch_bias,
                switch_bias_effectiveness,
                recent_effect_direction,
                dominant_failure_mode,
                grounding_pressure != "low",
            ]
        )
        priority_signals = [
            (
                f"grounding_pressure:{grounding_pressure}"
                if grounding_pressure != "low"
                else ""
            ),
            f"top_self_iteration_domain:{top_self_iteration_domain}" if top_self_iteration_domain else "",
            f"stay_or_switch_bias:{stay_or_switch_bias}" if stay_or_switch_bias else "",
            f"recent_effect_direction:{recent_effect_direction}" if recent_effect_direction else "",
            f"dominant_failure_mode:{dominant_failure_mode}" if dominant_failure_mode else "",
        ]
        priority_signals = [item for item in priority_signals if item]
        if not has_substantive_profile:
            return {
                "available": False,
                "summary": "No recent meta-cognition profile is available yet.",
            }

        return {
            "available": True,
            "current_judgement": current_judgement or None,
            "dominant_constraint": dominant_constraint or None,
            "grounding_pressure": grounding_pressure,
            "top_self_iteration_domain": top_self_iteration_domain or None,
            "top_self_iteration_hypothesis": top_self_iteration_hypothesis or None,
            "stay_or_switch_bias": stay_or_switch_bias or None,
            "switch_bias_effectiveness": switch_bias_effectiveness or None,
            "recent_effect_direction": recent_effect_direction or None,
            "dominant_failure_mode": dominant_failure_mode or None,
            "governance_posture": governance_posture,
            "priority_signals": priority_signals[:6],
            "summary": (
                "Recent meta-cognition indicates "
                f"judgement={current_judgement or 'unknown'}; "
                f"constraint={dominant_constraint or 'unknown'}; "
                f"grounding_pressure={grounding_pressure}; "
                f"self_iteration_domain={top_self_iteration_domain or 'unknown'}; "
                f"recent_effect_direction={recent_effect_direction or 'unknown'}."
            ),
        }

    def _current_active_cognitive_posture_profile(
        self,
        *,
        lm_reasoning_state: Optional[Dict[str, Any]] = None,
        history_snapshot: Optional[Dict[str, Any]] = None,
        deliberation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        policy = self._current_cognitive_control_policy()
        return self._resolve_cognitive_posture_profile(
            policy,
            lm_reasoning_state=lm_reasoning_state,
            drive_history=history_snapshot,
            deliberation=deliberation,
        )

    def _build_recent_cognitive_alignment_summary(
        self,
        *,
        history_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        outcomes = [
            dict(item)
            for item in list(history_snapshot.get("outcomes") or [])
            if isinstance(item, dict)
        ]
        entry_count = 0
        score_total = 0.0
        quality_counts: Dict[str, int] = {"strong": 0, "partial": 0, "weak": 0}
        reason_counts: Dict[str, int] = {}
        top_priority_counts: Dict[str, int] = {}
        posture_alignment_counts: Dict[str, int] = {}
        priority_basis_counts: Dict[str, int] = {}
        missing_posture_alignment_count = 0
        missing_priority_basis_count = 0

        for outcome in outcomes[:12]:
            cognitive_alignment = outcome.get("cognitive_alignment")
            metadata = dict(outcome.get("metadata") or {})
            evidence = dict(outcome.get("evidence") or {})
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict) or not cognitive_alignment:
                continue

            quality = str(cognitive_alignment.get("quality") or "partial").strip().lower()
            if quality not in quality_counts:
                quality = "partial"
            quality_counts[quality] += 1
            suggested_task_shape = str(
                cognitive_alignment.get("top_priority_task_type") or ""
            ).strip().lower()
            if suggested_task_shape:
                top_priority_counts[suggested_task_shape] = (
                    top_priority_counts.get(suggested_task_shape, 0) + 1
                )
            reasons = [
                str(reason).strip()
                for reason in list(cognitive_alignment.get("reasons") or [])[:4]
                if str(reason).strip()
            ]
            posture_alignment = outcome.get("llm_posture_alignment")
            if not isinstance(posture_alignment, list):
                posture_alignment = metadata.get("llm_posture_alignment")
            if not isinstance(posture_alignment, list):
                posture_alignment = evidence.get("llm_posture_alignment")
            normalized_posture_alignment = [
                str(item).strip()
                for item in list(posture_alignment or [])[:3]
                if str(item).strip()
            ]
            if normalized_posture_alignment:
                for item in normalized_posture_alignment:
                    posture_alignment_counts[item] = posture_alignment_counts.get(item, 0) + 1
            else:
                missing_posture_alignment_count += 1
            priority_basis = outcome.get("llm_priority_basis")
            if not isinstance(priority_basis, list):
                priority_basis = metadata.get("llm_priority_basis")
            if not isinstance(priority_basis, list):
                priority_basis = evidence.get("llm_priority_basis")
            normalized_priority_basis = [
                str(item).strip()
                for item in list(priority_basis or [])[:3]
                if str(item).strip()
            ]
            if normalized_priority_basis:
                for item in normalized_priority_basis:
                    priority_basis_counts[item] = priority_basis_counts.get(item, 0) + 1
            else:
                missing_priority_basis_count += 1
            for reason in reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            score_total += round(
                self._clamp_endogenous_ratio(cognitive_alignment.get("score") or 0.0),
                4,
            )
            entry_count += 1
            if entry_count >= 4:
                break

        if not entry_count:
            return {
                "available": False,
                "average_score": 0.0,
                "quality_counts": {},
                "dominant_task_shape": None,
                "summary": "No recent cognitive alignment feedback is available yet.",
                "reason_count": 0,
                "posture_alignment_signal_count": 0,
                "priority_basis_signal_count": 0,
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "entry_count": 0,
            }

        average_score = score_total / entry_count
        dominant_task_shape = None
        if top_priority_counts:
            dominant_task_shape = max(
                top_priority_counts.items(),
                key=lambda item: (item[1], item[0]),
            )[0]
        summary = (
            f"Recent cognitive alignment average={average_score:.2f}; "
            f"dominant quality="
            f"{max(quality_counts.items(), key=lambda item: (item[1], item[0]))[0]}."
        )
        return {
            "available": True,
            "average_score": round(self._clamp_endogenous_ratio(average_score), 4),
            "quality_counts": quality_counts,
            "dominant_task_shape": dominant_task_shape,
            "summary": summary,
            "reason_count": len(reason_counts),
            "posture_alignment_signal_count": len(posture_alignment_counts),
            "priority_basis_signal_count": len(priority_basis_counts),
            "missing_posture_alignment_count": missing_posture_alignment_count,
            "missing_priority_basis_count": missing_priority_basis_count,
            "entry_count": entry_count,
        }

    def _build_current_candidate_cognition_summary(
        self,
        *,
        candidate_items: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_count = 0
        cognitive_scores: list[float] = []
        reference_scores: list[float] = []
        lm_generated_count = 0

        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            candidate_count += 1
            metadata = dict(item.get("metadata") or {})
            evidence = dict(item.get("evidence") or {})
            llm_generated = bool(metadata.get("llm_task_generated") or evidence.get("llm_generated"))
            if llm_generated:
                lm_generated_count += 1
            cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")

            score = None
            if isinstance(cognitive_alignment, dict) and cognitive_alignment:
                score = round(
                    self._clamp_endogenous_ratio(cognitive_alignment.get("score") or 0.0),
                    4,
                )
                cognitive_scores.append(score)
            reference_score = None
            if isinstance(reference_alignment, dict) and reference_alignment:
                reference_score = round(
                    self._clamp_endogenous_ratio(
                        reference_alignment.get("alignment_score") or 0.0
                    ),
                    4,
                )
                reference_scores.append(reference_score)

        average_cognitive_alignment_score = (
            sum(cognitive_scores) / len(cognitive_scores) if cognitive_scores else 0.0
        )
        average_reference_alignment_score = (
            sum(reference_scores) / len(reference_scores) if reference_scores else 0.0
        )
        return {
            "count": candidate_count,
            "lm_generated_count": lm_generated_count,
            "average_cognitive_alignment_score": round(
                self._clamp_endogenous_ratio(average_cognitive_alignment_score),
                4,
            ),
            "average_reference_alignment_score": round(
                self._clamp_endogenous_ratio(average_reference_alignment_score),
                4,
            ),
        }

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
        self._record_endogenous_meta_governance_memory(
            history,
            mode=meta_mode["mode"],
            priority=meta_mode["confidence"],
            confidence=meta_mode["confidence"],
            context_key=context_key,
            recorded_at=recorded_at,
            status="active",
        )
        self._persist_endogenous_drive_history(history)
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
            self._record_endogenous_observation_memory(
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
        if self._resolve_cleared_endogenous_observation_targets(
            history,
            active_targets=active_targets,
            context_key=context_key,
            recorded_at=recorded_at,
        ):
            changed = True
        if changed:
            self._persist_endogenous_drive_history(history)

        refreshed_strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        refreshed_target_stats = dict(refreshed_strategy_memory.get("observation_target_stats") or {})
        return project_observation_program(
            entries_seed,
            target_stats=refreshed_target_stats,
        )

    def _consume_endogenous_governance_review_events(self) -> Dict[str, Any]:
        snapshot = self._load_endogenous_governance_events()
        events = list(snapshot.get("events") or [])
        consumed: list[Dict[str, Any]] = []
        updated_events: list[Dict[str, Any]] = []

        for item in events:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if (
                str(row.get("event_type") or "").strip() == "governance_review_request"
                and not row.get("consumed_at")
            ):
                row["consumed_at"] = datetime.now(timezone.utc).isoformat()
                row["consumed_action"] = "trigger_review_pass"
                consumed.append(
                    {
                        "event_id": row.get("event_id"),
                        "event_type": row.get("event_type"),
                        "context_key": row.get("context_key"),
                        "message": row.get("message"),
                    }
                )
            updated_events.append(row)

        if consumed:
            snapshot["events"] = updated_events
            self._persist_endogenous_governance_events(snapshot)

        return {
            "consumed": consumed,
            "count": len(consumed),
            "events": updated_events[:36],
        }

    def _consume_endogenous_alignment_events(self) -> Dict[str, Any]:
        events_snapshot = self._load_endogenous_governance_events()
        regulation_snapshot = self._load_endogenous_self_regulation()
        events = list(events_snapshot.get("events") or [])
        consumed: list[Dict[str, Any]] = []
        updated_events: list[Dict[str, Any]] = []
        applied = False

        for item in events:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if (
                str(row.get("event_type") or "").strip() == "autonomy_alignment_request"
                and not row.get("consumed_at")
            ):
                row["consumed_at"] = datetime.now(timezone.utc).isoformat()
                row["consumed_action"] = "increase_self_regulation"
                regulation_snapshot["dynamic_candidate_throttle_boost"] = min(
                    0.35,
                    float(regulation_snapshot.get("dynamic_candidate_throttle_boost") or 0.0) + 0.08,
                )
                regulation_snapshot["dynamic_observation_bias_boost"] = min(
                    0.30,
                    float(regulation_snapshot.get("dynamic_observation_bias_boost") or 0.0) + 0.06,
                )
                regulation_snapshot["last_reason"] = row.get("message") or row.get("rationale")
                consumed.append(
                    {
                        "event_id": row.get("event_id"),
                        "event_type": row.get("event_type"),
                        "context_key": row.get("context_key"),
                        "message": row.get("message"),
                    }
                )
                applied = True
            updated_events.append(row)

        if consumed:
            events_snapshot["events"] = updated_events
            self._persist_endogenous_governance_events(events_snapshot)
        if applied:
            self._persist_endogenous_self_regulation(regulation_snapshot)

        return {
            "consumed": consumed,
            "count": len(consumed),
            "regulation": dict(regulation_snapshot),
            "events": updated_events[:36],
        }

    def _consume_endogenous_truthfulness_alerts(self) -> Dict[str, Any]:
        events_snapshot = self._load_endogenous_governance_events()
        regulation_snapshot = self._load_endogenous_self_regulation()
        events = list(events_snapshot.get("events") or [])
        consumed: list[Dict[str, Any]] = []
        updated_events: list[Dict[str, Any]] = []
        applied = False

        for item in events:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if (
                str(row.get("event_type") or "").strip() == "truthfulness_alert"
                and not row.get("consumed_at")
            ):
                row["consumed_at"] = datetime.now(timezone.utc).isoformat()
                row["consumed_action"] = "increase_truthfulness_correction"
                regulation_snapshot["dynamic_truthfulness_bias_boost"] = min(
                    0.30,
                    float(regulation_snapshot.get("dynamic_truthfulness_bias_boost") or 0.0) + 0.08,
                )
                regulation_snapshot["dynamic_learning_expansion_suppression"] = min(
                    0.25,
                    float(regulation_snapshot.get("dynamic_learning_expansion_suppression") or 0.0) + 0.06,
                )
                regulation_snapshot["last_reason"] = row.get("message") or row.get("rationale")
                consumed.append(
                    {
                        "event_id": row.get("event_id"),
                        "event_type": row.get("event_type"),
                        "context_key": row.get("context_key"),
                        "message": row.get("message"),
                    }
                )
                applied = True
            updated_events.append(row)

        if consumed:
            events_snapshot["events"] = updated_events
            self._persist_endogenous_governance_events(events_snapshot)
        if applied:
            self._persist_endogenous_self_regulation(regulation_snapshot)

        return {
            "consumed": consumed,
            "count": len(consumed),
            "regulation": dict(regulation_snapshot),
            "events": updated_events[:36],
        }

    def _strategy_agenda_topic_bucket(
        self,
        history: Dict[str, Any],
        topic: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        history["strategy_memory"] = strategy_memory
        topic_name = str(topic or "").strip().lower()
        if not topic_name:
            topic_name = "unknown"
        topic_stats = strategy_memory.setdefault("agenda_topic_stats", {})
        return topic_stats.setdefault(
            topic_name,
            {
                "seen": 0,
                "active_cycles": 0,
                "resolved": 0,
                "dragging": 0,
                "last_priority": 0.0,
                "last_confidence": 0.0,
                "last_status": "unknown",
                "last_seen_at": None,
                "last_resolved_at": None,
                "last_context_key": None,
            },
        )

    def _record_endogenous_agenda_memory(
        self,
        history: Dict[str, Any],
        *,
        topic: Optional[str],
        priority: Any,
        confidence: Any,
        context_key: Optional[str],
        recorded_at: str,
        status: str,
    ) -> None:
        bucket = self._strategy_agenda_topic_bucket(history, topic)
        normalized_status = str(status or "active").strip().lower() or "active"
        bucket["seen"] = max(0, int(bucket.get("seen") or 0)) + 1
        if normalized_status == "active":
            bucket["active_cycles"] = max(0, int(bucket.get("active_cycles") or 0)) + 1
        elif normalized_status == "resolved":
            bucket["resolved"] = max(0, int(bucket.get("resolved") or 0)) + 1
            bucket["last_resolved_at"] = recorded_at
        elif normalized_status == "dragging":
            bucket["dragging"] = max(0, int(bucket.get("dragging") or 0)) + 1
        bucket["last_priority"] = round(self._clamp_endogenous_ratio(priority), 4)
        bucket["last_confidence"] = round(self._clamp_endogenous_ratio(confidence), 4)
        bucket["last_status"] = normalized_status
        bucket["last_seen_at"] = recorded_at
        bucket["last_context_key"] = (
            str(context_key or "").strip().lower() or bucket.get("last_context_key")
        )

    def _strategy_observation_target_bucket(
        self,
        history: Dict[str, Any],
        target: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        history["strategy_memory"] = strategy_memory
        target_name = str(target or "").strip().lower()
        if not target_name:
            target_name = "unknown"
        observation_stats = strategy_memory.setdefault("observation_target_stats", {})
        return observation_stats.setdefault(
            target_name,
            {
                "seen": 0,
                "recommended": 0,
                "resolved": 0,
                "stalled": 0,
                "last_priority": 0.0,
                "last_risk": 0.0,
                "last_status": "unknown",
                "last_seen_at": None,
                "last_resolved_at": None,
                "last_context_key": None,
            },
        )

    def _record_endogenous_observation_memory(
        self,
        history: Dict[str, Any],
        *,
        target: Optional[str],
        priority: Any,
        risk: Any,
        context_key: Optional[str],
        recorded_at: str,
        status: str,
    ) -> None:
        bucket = self._strategy_observation_target_bucket(history, target)
        normalized_status = str(status or "recommended").strip().lower() or "recommended"
        bucket["seen"] = max(0, int(bucket.get("seen") or 0)) + 1
        if normalized_status == "recommended":
            bucket["recommended"] = max(0, int(bucket.get("recommended") or 0)) + 1
        elif normalized_status == "resolved":
            bucket["resolved"] = max(0, int(bucket.get("resolved") or 0)) + 1
            bucket["last_resolved_at"] = recorded_at
        elif normalized_status == "stalled":
            bucket["stalled"] = max(0, int(bucket.get("stalled") or 0)) + 1
        bucket["last_priority"] = round(self._clamp_endogenous_ratio(priority), 4)
        bucket["last_risk"] = round(self._clamp_endogenous_ratio(risk), 4)
        bucket["last_status"] = normalized_status
        bucket["last_seen_at"] = recorded_at
        bucket["last_context_key"] = (
            str(context_key or "").strip().lower() or bucket.get("last_context_key")
        )

    def _resolve_cleared_endogenous_observation_targets(
        self,
        history: Dict[str, Any],
        *,
        active_targets: set[str],
        context_key: Optional[str],
        recorded_at: str,
    ) -> bool:
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        history["strategy_memory"] = strategy_memory
        observation_stats = dict(strategy_memory.get("observation_target_stats") or {})
        changed = False

        for target, stats in observation_stats.items():
            target_name = str(target or "").strip().lower()
            if not target_name or target_name in active_targets:
                continue
            bucket = dict(stats or {})
            recommended = max(0, int(bucket.get("recommended") or 0))
            resolved = max(0, int(bucket.get("resolved") or 0))
            last_status = str(bucket.get("last_status") or "").strip().lower()
            if recommended <= resolved or last_status == "resolved":
                continue
            self._record_endogenous_observation_memory(
                history,
                target=target_name,
                priority=bucket.get("last_priority") or 0.0,
                risk=bucket.get("last_risk") or 0.0,
                context_key=context_key,
                recorded_at=recorded_at,
                status="resolved",
            )
            changed = True

        return changed

    def _strategy_meta_governance_bucket(
        self,
        history: Dict[str, Any],
        mode: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        history["strategy_memory"] = strategy_memory
        mode_name = str(mode or "").strip().lower()
        if not mode_name:
            mode_name = "unknown"
        meta_stats = strategy_memory.setdefault("meta_governance_stats", {})
        return meta_stats.setdefault(
            mode_name,
            {
                "seen": 0,
                "active_cycles": 0,
                "resolved": 0,
                "stalled": 0,
                "last_priority": 0.0,
                "last_confidence": 0.0,
                "last_status": "unknown",
                "last_seen_at": None,
                "last_resolved_at": None,
                "last_context_key": None,
            },
        )

    def _record_endogenous_meta_governance_memory(
        self,
        history: Dict[str, Any],
        *,
        mode: Optional[str],
        priority: Any,
        confidence: Any,
        context_key: Optional[str],
        recorded_at: str,
        status: str,
    ) -> None:
        bucket = self._strategy_meta_governance_bucket(history, mode)
        normalized_status = str(status or "active").strip().lower() or "active"
        bucket["seen"] = max(0, int(bucket.get("seen") or 0)) + 1
        if normalized_status == "active":
            bucket["active_cycles"] = max(0, int(bucket.get("active_cycles") or 0)) + 1
        elif normalized_status == "resolved":
            bucket["resolved"] = max(0, int(bucket.get("resolved") or 0)) + 1
            bucket["last_resolved_at"] = recorded_at
        elif normalized_status == "stalled":
            bucket["stalled"] = max(0, int(bucket.get("stalled") or 0)) + 1
        bucket["last_priority"] = round(self._clamp_endogenous_ratio(priority), 4)
        bucket["last_confidence"] = round(self._clamp_endogenous_ratio(confidence), 4)
        bucket["last_status"] = normalized_status
        bucket["last_seen_at"] = recorded_at
        bucket["last_context_key"] = (
            str(context_key or "").strip().lower() or bucket.get("last_context_key")
        )

    def _strategy_focus_bucket(
        self,
        history: Dict[str, Any],
        focus: Optional[str],
        context_key: Optional[str] = None,
    ) -> Dict[str, int]:
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        history["strategy_memory"] = strategy_memory
        focus_name = str(focus or "").strip().lower()
        if not focus_name:
            focus_name = "unknown"
        focus_stats = strategy_memory.setdefault("focus_stats", {})
        bucket = focus_stats.setdefault(
            focus_name,
            {"judged": 0, "completed": 0, "failed": 0, "dragging": 0},
        )
        normalized_context = str(context_key or "").strip().lower()
        if normalized_context:
            contextual_focus_stats = strategy_memory.setdefault("contextual_focus_stats", {})
            contextual_bucket = contextual_focus_stats.setdefault(normalized_context, {})
            contextual_focus_bucket = contextual_bucket.setdefault(
                focus_name,
                {"judged": 0, "completed": 0, "failed": 0, "dragging": 0},
            )
            return contextual_focus_bucket
        return bucket

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

        history = self._load_endogenous_drive_history()
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
                global_focus_bucket = self._strategy_focus_bucket(history, preferred_focus)
                global_focus_bucket["judged"] += 1
                contextual_focus_bucket = self._strategy_focus_bucket(
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
                self._record_endogenous_agenda_memory(
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
            self._persist_endogenous_drive_history(history)
        return prepared

    def _restore_endogenous_evaluation_snapshots(
        self,
        *,
        drive_history: Dict[str, Any],
        governance_events: Dict[str, Any],
        cognition_state: Dict[str, Any],
    ) -> None:
        self._persist_endogenous_drive_history(drive_history)
        self._persist_endogenous_governance_events(governance_events)
        self._persist_endogenous_cognition_state(
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
        history_snapshot_before = self._load_endogenous_drive_history()
        governance_snapshot_before = self._load_endogenous_governance_events()
        cognition_snapshot_before = self._load_endogenous_cognition_state()
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
            cognition_state = self._build_endogenous_cognition_state(
                deliberation=deliberation,
                governance_channels=governance_channels,
                governance_event_stream=governance_event_stream,
                self_regulation=self_regulation,
                candidate_items=annotated_items,
                lm_reasoning_state=lm_reasoning_state,
            )
            self._persist_endogenous_cognition_state(cognition_state)
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
        history = self._load_endogenous_drive_history()
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
            global_focus_bucket = self._strategy_focus_bucket(history, preferred_focus)
            global_focus_bucket[outcome_bucket] += 1
            contextual_focus_bucket = self._strategy_focus_bucket(
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
            "quality_score": metadata.get("quality_score"),
            "learning_quality_score": evidence.get("learning_quality_score"),
            "result_status": execution_result.get("status"),
        }
        final_response = str(
            decision_context.get("autonomous_executor_final_response") or ""
        ).strip()
        if final_response:
            outcome["autonomous_executor_final_response"] = final_response[:4000]
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
                self._record_endogenous_agenda_memory(
                    history,
                    topic=topic,
                    priority=metadata.get("utility"),
                    confidence=metadata.get("utility"),
                    context_key=context_key,
                    recorded_at=recorded_at,
                    status=agenda_status,
                )
        history["outcomes"] = [outcome] + list(history.get("outcomes") or [])
        self._persist_endogenous_drive_history(history)

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

    def _request_task_metadata(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = dict(payload.get("metadata") or {})
        for key in ("governance_task_type", "task_family", "execution_kind", "rationale"):
            value = payload.get(key)
            if value is not None:
                metadata[key] = value
        for key in (
            "scheduled_for",
            "preset_time",
            "scheduled_at",
            "run_at",
            "execute_after",
            "time_slot",
            "window",
        ):
            value = payload.get(key)
            if value is not None and key not in metadata:
                metadata[key] = value
        metadata = self._schedule_allocator.normalize_metadata(metadata)
        explicit_execution_kind = str(metadata.get("execution_kind") or "").strip().lower()
        if explicit_execution_kind in {"body_switch", "body_improvement"} and not metadata.get("task_family"):
            metadata["task_family"] = explicit_execution_kind
        return metadata

    def _active_autonomous_chain_tasks(self) -> list[AutonomousChainTask]:
        """Return active autonomous-chain rows across API-B and API-A lanes."""
        rows: list[AutonomousChainTask] = []
        seen: set[str] = set()
        for task in [
            *self._autonomous_chain_store.list_api_b_judgement_tasks(),
            *self._autonomous_chain_store.list_api_a_execution_lane_tasks(),
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
                latest_context.get("autonomous_executor_final_response")
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
                            else evidence.get("learning_quality_score")
                        ),
                        "endogenous_drive_key": metadata.get("endogenous_drive_key"),
                    },
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _is_api_a_execution_lane_task_record(self, task: AutonomousChainTask) -> bool:
        status = str(task.status or "").strip().lower()
        return is_agent_pull_task(
            task,
            task_profile_policy=self._task_profile_policy,
        ) and status in {"approved", "running", "retry"}

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
            if self._is_api_a_execution_lane_task_record(task):
                continue
            rows.append(
                (
                    str(getattr(task, "updated_at", None) or getattr(task, "created_at", None) or ""),
                    self._autonomous_chain_task_summary_payload(task),
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _api_a_execution_lane_task_summaries(self, limit: int = 20) -> list[Dict[str, Any]]:
        rows: list[tuple[str, Dict[str, Any]]] = []
        for task in self._autonomous_chain_store.list_api_a_execution_lane_tasks():
            if not is_agent_pull_task(
                task,
                task_profile_policy=self._task_profile_policy,
            ):
                continue
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
                    async with session.get(url) as response:
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

    async def get_runtime_activity(self):
        snapshot = await self._fetch_gateway_activity_snapshot()
        return {
            "status": "ok",
            "gateway_address": self.config.execution.gateway_address,
            "activity": snapshot,
        }

    async def get_runtime_observation_input(self):
        payload = await self.evaluate_drive_input({})
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
        gate_active = bool(
            request.get("autonomous_chain_gate_active")
            or getattr(runtime, "autonomous_chain_gate_active", False)
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
                async with session.post(url, json=payload, timeout=10) as response:
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

        def schedule_candidate_items(candidates: list[Any]) -> list[Dict[str, Any]]:
            return self._schedule_allocator.apply_to_candidates(
                [candidate.to_api_b_judgement_item() for candidate in candidates],
                occupied_tokens=self._schedule_allocator.occupied_tokens(
                    self._active_autonomous_chain_tasks()
                ),
                now=datetime.now(),
            )

        context = EndogenousDriveEvaluationContext(
            runtime_config=self.config.service_runtime,
            resolve_drive_input_request=lambda payload: self._resolve_runtime_drive_input_request(
                payload,
                include_gate_default=True,
            ),
            load_self_regulation=self._load_endogenous_self_regulation,
            load_drive_history=self._load_endogenous_drive_history,
            normalize_strategy_memory=normalize_endogenous_strategy_memory,
            api_b_judgement_task_summaries=self._api_b_judgement_task_summaries,
            api_a_execution_lane_task_summaries=self._api_a_execution_lane_task_summaries,
            build_deliberation_report=self._endogenous_drive_engine.build_deliberation_report,
            generate_candidates=self._endogenous_drive_engine.generate_candidates,
            existing_drive_keys=self._existing_endogenous_drive_keys,
            schedule_candidate_items=schedule_candidate_items,
            lm_generation_application_state=self._lm_generation_application_state,
            derive_cognitive_self_regulation=self._derive_cognitive_self_regulation,
            release_cleared_observation_carryover=(
                self._release_cleared_historical_observation_carryover
            ),
            governance_channels_from_deliberation=self._governance_channels_from_deliberation,
            persist_evaluation=self._persist_endogenous_evaluation_for_candidates,
            load_governance_events=self._load_endogenous_governance_events,
            build_cognition_state=self._build_endogenous_cognition_state,
            record_ui_activity=self._record_supervisor_ui_activity,
            build_response_fields=self._build_drive_input_response_fields,
            drive_posture_from_deliberation=self._drive_posture_signal_from_deliberation,
            core_values=CORE_VALUES,
        )
        return await run_endogenous_drive_evaluation(
            request=request,
            context=context,
        )

    async def get_endogenous_governance_events(self) -> Dict[str, Any]:
        snapshot = self._load_endogenous_governance_events()
        return {
            "status": "ok",
            "updated_at": snapshot.get("updated_at"),
            "governance_event_stream": project_governance_event_stream(snapshot),
        }

    async def get_endogenous_self_regulation(self) -> Dict[str, Any]:
        regulation = self._load_endogenous_self_regulation()
        return {
            "status": "ok",
            "updated_at": regulation.get("updated_at"),
            "self_regulation": regulation,
            "corrective_mode": derive_corrective_mode(regulation),
        }

    async def get_endogenous_cognition_state(self) -> Dict[str, Any]:
        snapshot = self._load_endogenous_cognition_state()
        return {
            "status": "ok",
            "updated_at": snapshot.get("updated_at"),
            "cognition_state": dict(snapshot.get("state") or {}),
        }

    async def get_endogenous_governance_state(self) -> Dict[str, Any]:
        cognition_snapshot = self._load_endogenous_cognition_state()
        event_snapshot = self._load_endogenous_governance_events()
        regulation = self._load_endogenous_self_regulation()
        drive_history = self._load_endogenous_drive_history()
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
        snapshot = self._load_endogenous_governance_events()
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
                for semantic_key in [self._endogenous_governance_event_semantic_key(item)]
                if semantic_key
            }
            new_events: list[Dict[str, Any]] = []
            for event in generated_events:
                semantic_key = self._endogenous_governance_event_semantic_key(event)
                if semantic_key and semantic_key in existing_event_keys:
                    continue
                if semantic_key:
                    existing_event_keys.add(semantic_key)
                new_events.append(event)
            if not new_events:
                return project_governance_event_stream(snapshot)
            snapshot["events"] = new_events + list(snapshot.get("events") or [])
            self._persist_endogenous_governance_events(snapshot)
        return project_governance_event_stream(snapshot)

    async def _run_endogenous_drive_cycle(self) -> Dict[str, Any]:
        context = EndogenousDriveCycleContext(
            runtime_config=self.config.service_runtime,
            evaluate_drive=self.evaluate_endogenous_drive,
            drive_input_fields_from_evaluation=self._drive_input_fields_from_evaluation,
            load_drive_history=self._load_endogenous_drive_history,
            load_governance_events=self._load_endogenous_governance_events,
            load_cognition_state=self._load_endogenous_cognition_state,
            persist_evaluation=self._persist_endogenous_evaluation_for_candidates,
            restore_evaluation_snapshots=self._restore_endogenous_evaluation_snapshots,
            lm_generation_application_state=self._lm_generation_application_state,
            plan_autonomous_chain_task=self.plan_autonomous_chain_task,
            record_ui_activity=self._record_supervisor_ui_activity,
            touch_gateway_activity=self._touch_gateway_activity,
        )
        return await run_endogenous_drive_cycle(context=context)

    async def _fetch_gateway_cli_session(self, session_id: str) -> Dict[str, Any]:
        import aiohttp

        execution_config = getattr(self.config, "execution", None)
        gateway_address = getattr(execution_config, "gateway_address", "http://127.0.0.1:6000")
        url = f"{gateway_address}/v1/sessions/{session_id}"
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 404:
                    return {"session_id": session_id, "missing": True}
                if response.status >= 400:
                    raise HTTPException(
                        status_code=503,
                        detail=f"网关 owner 会话查询失败，返回状态 {response.status}",
                    )
                payload = await response.json()
        if not isinstance(payload, dict):
            return {}
        payload.setdefault("session_id", session_id)
        payload.setdefault("missing", False)
        return payload

    async def _recover_orphaned_agent_pull_tasks(self) -> int:
        recovered = 0
        for task in self._autonomous_chain_store.list_api_a_execution_lane_tasks(status="running"):
            if not is_agent_pull_task(
                task,
                task_profile_policy=self._task_profile_policy,
            ):
                continue
            metadata = dict(task.metadata or {})
            owner_session_id = str(metadata.get("owner_session_id") or "").strip()
            execution_source = str(metadata.get("execution_source") or "").strip().lower()
            if execution_source and execution_source != "cli_agent_pull":
                continue
            if not owner_session_id:
                logger.warning(
                    "跳过运行中 agent-pull 链路项 %s 的孤儿恢复："
                    "owner_session_id 缺失，当前无法确认归属。",
                    task.task_id,
                )
                self._autonomous_task_state.update_metadata(
                    task.task_id,
                    metadata={
                        "owner_session_missing_seen_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                continue
            try:
                owner_session = await self._fetch_gateway_cli_session(owner_session_id)
            except Exception as exc:
                logger.warning(
                    "跳过链路项 %s 的孤儿恢复：无法从网关确认 "
                    "owner CLI 会话 %s（%s）；当前保守地不做恢复。",
                    task.task_id,
                    owner_session_id,
                    exc,
                )
                continue
            owner_missing = bool(owner_session.get("missing"))
            owner_stale = bool(owner_session.get("is_stale")) or str(
                owner_session.get("lease_status") or ""
            ).strip().lower() == "stale"
            if not owner_missing and not owner_stale:
                continue
            self._autonomous_task_state.update_status(
                task.task_id,
                status="approved",
                actor="supervisor",
                reason=(
                    "Recovered orphaned agent-pull task because its owning autonomous "
                    "executor session is missing or stale."
                ),
                context={
                    "recovered": True,
                    "previous_owner_session_id": owner_session_id or None,
                    "owner_session_missing": owner_missing,
                    "owner_session_stale": owner_stale,
                    "owner_lease_status": owner_session.get("lease_status"),
                    "active_cli_session_id": owner_session.get("active_cli_session_id"),
                },
                event_type="recovery",
            )
            self._autonomous_task_state.update_metadata(
                task.task_id,
                metadata={
                    "recovered_from_orphaned_running": True,
                    "last_recovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            recovered += 1
        return recovered

    def _request_agent_session_id(self, request: Dict[str, Any]) -> str:
        session_id = str(request.get("session_id") or "").strip()
        if session_id:
            return session_id
        context = request.get("context")
        if isinstance(context, dict):
            session_id = str(context.get("session_id") or "").strip()
            if session_id:
                return session_id
        return ""

    def _ensure_agent_pull_task_owner(
        self,
        *,
        task: AutonomousChainTask,
        request: Dict[str, Any],
        decision: str,
        actor: str,
    ) -> str:
        if not is_agent_pull_task(
            task,
            task_profile_policy=self._task_profile_policy,
        ):
            return ""
        if decision not in {"running", "completed", "failed"}:
            return ""
        actor_normalized = str(actor or "").strip().lower()
        if actor_normalized not in {"agent", "cli_agent", "gateway"}:
            return ""

        session_id = self._request_agent_session_id(request)
        if not session_id:
            raise HTTPException(
                status_code=400,
                detail="Agent-pull task decisions require a session_id in request or context.",
            )

        metadata = dict(task.metadata or {})
        owner_session_id = str(metadata.get("owner_session_id") or "").strip()
        if decision == "running":
            if owner_session_id and owner_session_id != session_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "当前 agent-pull 链路项已被另一 CLI 会话认领 "
                        f"({owner_session_id})。"
                    ),
                )
            return session_id

        if not owner_session_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "当前 agent-pull 链路项处于运行中但缺少 owner_session_id；"
                    "由于归属未知，完成/失败写回已被拒绝。"
                ),
            )
        if owner_session_id != session_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "当前 agent-pull 链路项写回已被拒绝：请求会话 "
                    f"{session_id} 并不拥有链路项 {task.task_id}。"
                ),
            )
        return session_id

    def _get_autonomous_chain_cycle_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_autonomous_chain_cycle_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(self, "_autonomous_chain_cycle_lock", lock)
        return lock

    def _build_autonomous_chain_execution_request(
        self,
        task: AutonomousChainTask,
        *,
        decision_id: str,
        actor: str,
        reason: str,
        decision_context: Dict[str, Any],
    ) -> Optional[AutonomousChainExecutionRequest]:
        execution = dict(task.metadata.get("execution_request") or {})
        raw_kind = self._task_profile_policy.execution_kind(task) or "general_self_evolution"
        kind = "memory_maintenance" if raw_kind == "memory_maintenance" else "general_self_evolution"
        task_family = self._task_profile_policy.runtime_family(task)
        governance_task_type = self._task_profile_policy.governance_type(task)

        git_lineage = {
            **dict(task.evidence.get("git_lineage") or {}),
            **dict(execution.get("git_lineage") or {}),
        }
        rollback_plan = {
            **dict(task.constraints.get("rollback_plan") or {}),
            **dict(execution.get("rollback_plan") or {}),
        }
        governor_decision = {
            "decision": "approved_for_execution",
            "actor": actor,
            "reason": reason,
            "task_status": "approved",
        }
        if "governor_decision" in task.evidence:
            governor_decision["evidence_decision"] = task.evidence["governor_decision"]

        decision_fields = self._drive_input_fields_from_decision_context(decision_context)
        drive_input_evidence = dict(decision_fields.get("drive_input") or {})
        execution_request = AutonomousChainExecutionRequest(
            task_id=task.task_id,
            trace_id=task.trace_id,
            task_type=task.task_type,
            governance_task_type=governance_task_type,
            task_family=task_family,
            execution_kind=raw_kind,
            decision_id=decision_id,
            kind=kind,  # type: ignore[arg-type]
            source_actor=str(execution.get("source_actor") or actor or "mem_supervisor"),
            target_slot_id=(
                execution.get("target_slot_id")
                or task.metadata.get("target_slot_id")
                or task.constraints.get("target_slot_id")
            ),
            git_lineage=AutonomousChainGitLineage.model_validate(git_lineage),
            probe_report_ref=(
                execution.get("probe_report_ref")
                or task.evidence.get("probe_report_ref")
                or task.evidence.get("probe_report_path")
            ),
            drive_input_evidence=drive_input_evidence,
            governor_decision=governor_decision,
            rollback_plan=rollback_plan,
        )
        return execution_request

    @staticmethod
    def _normalize_execution_request_evidence_payload(
        execution_request_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload = dict(execution_request_payload or {})
        drive_input_evidence = dict(payload.get("drive_input_evidence") or {})
        payload.pop("activity_guard_evidence", None)
        if not drive_input_evidence:
            return payload
        payload["drive_input_evidence"] = dict(drive_input_evidence)
        return payload

    def _serialize_autonomous_chain_task(self, task: AutonomousChainTask) -> Dict[str, Any]:
        payload = task.model_dump(mode="json")
        execution_request_payload = payload.get("execution_request")
        if isinstance(execution_request_payload, dict):
            payload["execution_request"] = self._normalize_execution_request_evidence_payload(
                execution_request_payload
            )
        runtime_profile = self._task_profile_policy.runtime_profile(task)
        execution = dict(task.metadata.get("execution_request") or {})
        payload["governance_task_type"] = (
            execution.get("governance_task_type")
            or task.metadata.get("governance_task_type")
            or task.governance_task_type
            or task.metadata.get("governance_task_type")
            or runtime_profile.get("governance_task_type")
        )
        payload["task_family"] = (
            execution.get("task_family")
            or task.metadata.get("task_family")
            or task.task_family
            or task.metadata.get("task_family")
            or runtime_profile.get("task_family")
        )
        payload["execution_kind"] = (
            execution.get("execution_kind")
            or task.metadata.get("execution_kind")
            or task.execution_kind
            or task.metadata.get("execution_kind")
            or runtime_profile.get("execution_kind")
        )
        scheduled_for = self._schedule_allocator.task_schedule_token(task)
        if scheduled_for is not None:
            payload["scheduled_for"] = scheduled_for
        requested_kind = str(execution.get("kind") or "").strip() or None
        decision_history = payload.get("decision_history") or []
        latest_context: Dict[str, Any] = {}
        if isinstance(decision_history, list) and decision_history:
            latest = decision_history[-1]
            if isinstance(latest, dict):
                latest_context = dict(latest.get("context") or {})
        judgement_preview = self._judgement_preview_projection(
            latest_context=latest_context,
            current_task=task,
        )
        if judgement_preview:
            payload["judgement_preview"] = judgement_preview
        display_kind = (
            requested_kind
            or payload.get("execution_kind")
            or payload.get("task_family")
            or payload.get("governance_task_type")
            or payload.get("task_type")
        )
        payload["task_identity"] = {
            "task_id": payload.get("task_id"),
            "title": payload.get("title"),
            "task_type": payload.get("task_type"),
            "governance_task_type": payload.get("governance_task_type"),
            "task_family": payload.get("task_family"),
            "execution_kind": payload.get("execution_kind"),
            "runtime_task_family": runtime_profile.get("task_family"),
            "runtime_execution_kind": runtime_profile.get("execution_kind"),
            "requested_kind": requested_kind,
            "display_kind": display_kind,
            "summary": (
                f"{payload.get('title')} ({display_kind})"
                if payload.get("title") and display_kind
                else payload.get("title") or display_kind or payload.get("task_id")
            ),
        }
        return payload

    def _governance_action_label(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "approve": "转交",
            "defer": "延后",
            "cancel": "清退",
            "pause": "暂停",
            "retire": "退休建议",
            "merge": "合并建议",
            "reprioritize": "重排优先级",
            "reprioritise": "重排优先级",
        }.get(normalized, str(value or "").strip() or "判断动作")

    def _governance_priority_label(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return {
            "low": "低",
            "normal": "中",
            "high": "高",
        }.get(normalized, str(value or "").strip() or "未识别")

    def _governance_merge_target_title(self, task_id: Any) -> str:
        normalized = str(task_id or "").strip()
        if not normalized:
            return ""
        target = self._autonomous_chain_store.get_task(normalized)
        if target is None:
            return ""
        return str(target.title or "").strip()

    def _judgement_preview_projection(
        self,
        *,
        latest_context: Dict[str, Any],
        current_task: AutonomousChainTask,
    ) -> Dict[str, Any]:
        judgement_preview: Dict[str, Any] = {}
        notes: list[str] = []

        review_context = latest_context.get("supervisor_review_outcome")
        if isinstance(review_context, dict):
            review_payload = dict(review_context)
            action_label = self._governance_action_label(review_payload.get("action"))
            review_payload["action_label"] = action_label
            review_payload["summary"] = (
                f"监督者已采纳判断动作: {action_label}"
                + (
                    f" · {str(review_payload.get('reason') or '').strip()[:120]}"
                    if str(review_payload.get("reason") or "").strip()
                    else ""
                )
            )
            judgement_preview["review_outcome"] = review_payload
            notes.append(str(review_payload["summary"]))

        followup_context = latest_context.get("supervisor_followup_suggestion")
        if isinstance(followup_context, dict):
            followup_payload = dict(followup_context)
            action_label = self._governance_action_label(followup_payload.get("action"))
            followup_payload["action_label"] = action_label
            merge_target_title = self._governance_merge_target_title(
                followup_payload.get("merge_into")
            )
            if merge_target_title:
                followup_payload["merge_into_title"] = merge_target_title
            if followup_payload.get("merge_into") and merge_target_title:
                followup_extra = f" · 并入 {merge_target_title}"
            elif followup_payload.get("merge_into"):
                followup_extra = f" · 并入 {str(followup_payload.get('merge_into') or '')[:16]}"
            else:
                followup_extra = ""
            followup_payload["summary"] = (
                f"监督者保留建议: {action_label}{followup_extra}"
                + (
                    f" · {str(followup_payload.get('reason') or '').strip()[:120]}"
                    if str(followup_payload.get("reason") or "").strip()
                    else ""
                )
            )
            judgement_preview["followup_suggestion"] = followup_payload
            notes.append(str(followup_payload["summary"]))

        priority_context = latest_context.get("supervisor_priority_adjustment")
        if isinstance(priority_context, dict):
            priority_payload = dict(priority_context)
            priority_label = self._governance_priority_label(priority_payload.get("priority"))
            priority_payload["priority_label"] = priority_label
            priority_payload["summary"] = (
                f"监督者已重排优先级: {priority_label}"
                + (
                    f" · {str(priority_payload.get('reason') or '').strip()[:120]}"
                    if str(priority_payload.get("reason") or "").strip()
                    else ""
                )
            )
            judgement_preview["priority_adjustment"] = priority_payload
            notes.append(str(priority_payload["summary"]))

        if notes:
            judgement_preview["notes"] = notes[:3]
            judgement_preview["summary"] = notes[0]
        if judgement_preview:
            judgement_preview["task_title"] = str(current_task.title or "").strip()
        return judgement_preview

    def _build_supervisor_review_snapshot(
        self,
        tasks: list[AutonomousChainTask],
    ) -> list[Dict[str, Any]]:
        snapshot: list[Dict[str, Any]] = []
        for task in tasks:
            metadata = dict(task.metadata or {})
            evidence = dict(task.evidence or {})
            constraints = dict(task.constraints or {})
            snapshot.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "summary": task.summary,
                    "status": task.status,
                    "priority": task.priority,
                    "source": task.source,
                    "governance_task_type": self._task_profile_policy.governance_type(task),
                    "task_family": self._task_profile_policy.runtime_family(task),
                    "execution_kind": self._task_profile_policy.execution_kind(task),
                    "scheduled_for": self._schedule_allocator.task_schedule_token(task),
                    "metadata": {
                        "endogenous_drive_key": metadata.get("endogenous_drive_key"),
                        "utility": metadata.get("utility"),
                        "quality_score": metadata.get("quality_score"),
                        "learning_branch": metadata.get("learning_branch"),
                        "self_learning_mode": metadata.get("self_learning_mode"),
                    },
                    "evidence": {
                        "recent_errors": evidence.get("recent_errors"),
                        "uncertainty_high_count": evidence.get("uncertainty_high_count"),
                        "learning_quality_score": evidence.get("learning_quality_score"),
                        "topic_source": (
                            (evidence.get("endogenous_drive") or {}).get("topic_source")
                            or evidence.get("topic_source")
                        ),
                        "learning_branch": (
                            (evidence.get("endogenous_drive") or {}).get("learning_branch")
                            or evidence.get("learning_branch")
                        ),
                    },
                    "constraints": {
                        "execution_policy": constraints.get("execution_policy"),
                        "target_slot": constraints.get("target_slot"),
                        "must_commit": constraints.get("must_commit"),
                    },
                    "decision_history_count": len(task.decision_history or []),
                }
            )
        return snapshot

    def _coerce_supervisor_review_action(
        self,
        action: Any,
        *,
        current_status: str,
    ) -> Optional[str]:
        if not isinstance(action, str):
            return None
        normalized = self._LM_GOVERNANCE_ACTION_TO_STATUS.get(action.strip().lower())
        if normalized is None:
            return None
        if current_status in {"completed", "failed", "cancelled"}:
            return None
        return normalized

    def _extract_supervisor_followup_suggestion(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action = str(item.get("action") or "").strip().lower()
        if action not in self._LM_GOVERNANCE_SHADOW_ACTIONS:
            return None

        recommendation: Dict[str, Any] = {
            "action": action,
            "reason": str(item.get("reason") or "").strip()[:500],
        }
        if action == "merge":
            recommendation["merge_into"] = str(item.get("merge_into") or "").strip()[:200]
        return recommendation

    def _extract_supervisor_priority_recommendation(self, item: Dict[str, Any]) -> Optional[str]:
        action = str(item.get("action") or "").strip().lower()
        if action not in {"reprioritize", "reprioritise"}:
            return None
        priority = str(item.get("priority") or "").strip().lower()
        if priority not in {"low", "normal", "high"}:
            return None
        return priority

    async def _review_task_governance_with_supervisor(
        self,
        tasks: list[AutonomousChainTask],
        *,
        drive_input: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        if not tasks:
            return {}
        drive_input = dict(drive_input or {})

        try:
            from memai.model_config import resolve_mem_llm_client

            llm_client, _ = resolve_mem_llm_client(role="governance_reasoner")
            if llm_client is None:
                return {}
        except Exception:
            return {}

        api_b_judgement_snapshot = self._build_supervisor_review_snapshot(tasks)
        prompt = (
            "你是 VoidCube 的 API-B 判断层。你的职责不是产出新任务，"
            "而是观察并裁定当前 API-B 判断在途链路项。\n\n"
            "请基于当前 drive_input、API-B 判断在途快照和用户优先级，"
            "为每个链路项给出一个结构化判断动作建议。你可以使用以下动作：\n"
            "- approve: 建议当前任务本轮由 API-B 转交给 API-A 接手\n"
            "- defer: 建议当前任务继续等待\n"
            "- cancel: 建议当前任务清退/取消\n"
            "- pause: 建议当前任务暂停\n"
            "- retire: 建议该任务退休，但先仅记录建议，不直接落状态\n"
            "- merge: 建议该任务与另一任务合并，但先仅记录建议，不直接合并\n"
            "- reprioritize: 建议调整优先级，但先仅记录建议，不直接改优先级\n\n"
            "注意：\n"
            "1. 不要新增任务\n"
            "2. 不要改写 task_id\n"
            "3. 不要为同一任务返回多个动作\n"
            "4. 优先考虑避免重复、无证据、陈旧或与当前系统状态冲突的任务\n"
            "5. body_improvement 只有在学习证据足够时才建议 approve；这里的 approve 只表示转交 API-A 接手，不表示 Web 小屋可控制执行\n\n"
            "6. 同一个 scheduled_for / preset_time 只能保留一个活跃任务；"
            "如果时间重叠，按先后顺序只保留一个，不能与现有自主链计划时段重复，其余建议 defer 或 cancel；"
            "该保留/顺延建议由监督者 LM 判断\n\n"
            "输出 JSON 对象，格式为：\n"
            "{\n"
            '  "actions": [\n'
            '    {"task_id": "...", "action": "approve|defer|cancel|pause|retire|merge|reprioritize", "reason": "...", "merge_into": "...", "priority": "..."}\n'
            "  ]\n"
            "}\n\n"
            f"【drive_input】\n{json.dumps(drive_input, ensure_ascii=False, default=str)[:3000]}\n\n"
            f"【api_b_judgement】\n{json.dumps(api_b_judgement_snapshot, ensure_ascii=False, default=str)[:5000]}"
        )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    llm_client.complete_json,
                    system_prompt=(
                        "你是 VoidCube 的监督者身份。你观察并裁定 API-B 判断在途链路项的生命周期，"
                        "但不能绕过确定性状态机。你的回答必须保守、结构化、可审计。"
                    ),
                    user_payload={"governance_review": prompt},
                    task="scholar.revision",
                ),
                timeout=8.0,
            )
        except Exception:
            return {}

        if not isinstance(result, dict):
            return {}

        actions = result.get("actions")
        if not isinstance(actions, list):
            return {}

        reviewed: Dict[str, Dict[str, Any]] = {}
        for item in actions:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or "").strip()
            if not task_id:
                continue
            reviewed[task_id] = {
                "action": item.get("action"),
                "reason": str(item.get("reason") or "").strip()[:500],
            }
            followup_suggestion = self._extract_supervisor_followup_suggestion(item)
            if followup_suggestion is not None:
                reviewed[task_id]["followup_suggestion"] = followup_suggestion
            priority = self._extract_supervisor_priority_recommendation(item)
            if priority is not None:
                reviewed[task_id]["priority"] = priority
        return reviewed

    def _autonomous_chain_task_git_lineage(self, task: AutonomousChainTask) -> Dict[str, Any]:
        execution = dict(task.metadata.get("execution_request") or {})
        return {
            **dict(task.evidence.get("git_lineage") or {}),
            **dict(execution.get("git_lineage") or {}),
        }

    async def list_autonomous_chain_tasks(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ):
        normalized_status = None
        if status is not None:
            normalized_status = normalize_autonomous_chain_decision(status)
            if normalized_status is None or normalized_status == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported task status filter: {status}")
        tasks = self._autonomous_chain_store.list_chain_projection_tasks(
            status=normalized_status,
            include_cancelled=True,
        )
        if task_type:
            tasks = [t for t in tasks if self._task_profile_policy.governance_type(t) == str(task_type).strip()]
        if execution_kind:
            normalized_execution_kind = self._task_profile_policy.normalize_family(execution_kind)
            explicit_execution_kind = str(execution_kind).strip().lower()
            filtered_tasks = []
            for task in tasks:
                task_execution_kind = str(self._task_profile_policy.execution_kind(task) or "").strip().lower()
                serialized_execution_kind = str(
                    self._serialize_autonomous_chain_task(task).get("execution_kind") or ""
                ).strip().lower()
                if task_execution_kind == explicit_execution_kind:
                    filtered_tasks.append(task)
                    continue
                if serialized_execution_kind == explicit_execution_kind:
                    filtered_tasks.append(task)
                    continue
                if task_execution_kind == normalized_execution_kind:
                    filtered_tasks.append(task)
            tasks = filtered_tasks
        return {
            "tasks": [self._serialize_autonomous_chain_task(task) for task in tasks],
            "count": len(tasks),
        }

    async def get_autonomous_chain_task(self, task_id: str):
        task = self._autonomous_chain_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Autonomous-chain task not found: {task_id}")
        return self._serialize_autonomous_chain_task(task)

    async def clear_autonomous_chain_runtime(self, request: dict | None = None):
        del request
        # Administrative clear needs the entire storage snapshot, including records
        # that are no longer part of the autonomous-chain projections.
        tasks = list(self._autonomous_chain_store.list_tasks())
        cleared_counts: Dict[str, int] = {}
        for task in tasks:
            status = str(task.status)
            cleared_counts[status] = cleared_counts.get(status, 0) + 1

        self._autonomous_task_state.clear_tasks(tasks)

        if hasattr(self, "_clear_supervisor_ui_activity"):
            self._clear_supervisor_ui_activity()

        governor = getattr(self, "_governor", None)
        if governor is not None and hasattr(governor, "clear_runtime_projection"):
            governor.clear_runtime_projection()
        try:
            self._persist_endogenous_drive_history(self._endogenous_drive_history_default())
        except Exception:
            pass

        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.config.execution.gateway_address}/admin/activity/clear",
                    timeout=aiohttp.ClientTimeout(total=5),
                )
        except Exception:
            pass

        service_runtime = getattr(self, "_service_runtime", None)
        if service_runtime is not None:
            service_runtime.last_review_at = None
            service_runtime.next_review_at = None
            service_runtime.last_drive_at = None
            service_runtime.next_drive_at = None
            setattr(service_runtime, "suppress_candidate_refresh", True)

        if hasattr(self, "_watch_window_last_outcome"):
            self._watch_window_last_outcome = None

        return {
            "status": "cleared",
            "cleared_task_count": len(tasks),
            "cleared_status_counts": cleared_counts,
            "tasks_remaining": 0,
        }

    def _mem_governance_repository_path(self) -> Path:
        governor = getattr(self, "_governor", None)
        repository = getattr(governor, "governance_repository", None)
        repository_path = getattr(repository, "path", None)
        if repository_path:
            return Path(repository_path)
        storage_root = getattr(governor, "storage_root", None)
        if storage_root:
            return Path(storage_root) / "mem_governance.jsonl"
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
        )
        return runtime_root / "mem_governance.jsonl"

    def _load_mem_governance_events(self) -> list[Any]:
        governor = getattr(self, "_governor", None)
        repository = getattr(governor, "governance_repository", None)
        if repository is None:
            return []
        return repository.list_events()

    def _recover_autonomous_chain_store_from_mem_governance(
        self,
        *,
        replace: bool = False,
    ) -> Dict[str, Any]:
        existing_count = len(self._autonomous_chain_store.list_tasks())
        events = self._load_mem_governance_events()
        result = self._autonomous_chain_store.recover_from_governance_events(
            events,
            replace=replace,
        )
        return {
            **result,
            "existing_task_count": existing_count,
            "mem_governance_path": str(self._mem_governance_repository_path()),
        }

    async def recover_autonomous_chain_from_mem(self, request: dict | None = None):
        request = request or {}
        result = self._recover_autonomous_chain_store_from_mem_governance(
            replace=bool(request.get("replace", False)),
        )
        if result.get("added_task_count") or result.get("updated_task_count"):
            await self._touch_gateway_activity(
                "autonomous_chain_plan",
                metadata={
                    "action": "recover_from_mem_governance",
                    "added_task_count": result.get("added_task_count", 0),
                    "updated_task_count": result.get("updated_task_count", 0),
                },
            )
        return result

    async def plan_autonomous_chain_task(self, request: dict | None = None):
        request = request or {}
        items = request.get("items")

        created = []
        if isinstance(items, list) and items:
            for item in items:
                title = str(item.get("title") or "").strip()
                if not title:
                    raise HTTPException(status_code=400, detail="Each task item must include a title.")
                request_metadata = self._request_task_metadata(item)
                task = self._autonomous_task_state.create_task(
                    title=title,
                    summary=str(item.get("summary", "")),
                    trace_id=str(item.get("trace_id") or uuid.uuid4()),
                    task_type=self._task_profile_policy.request_type(item, metadata=request_metadata),
                    source=str(item.get("source", "self_learning")),
                    priority=str(item.get("priority", "normal")),
                    metadata=request_metadata,
                    evidence=dict(item.get("evidence") or {}),
                    constraints=dict(item.get("constraints") or {}),
                )
                created.append(task)
        else:
            title = str(request.get("title") or "").strip()
            if not title:
                raise HTTPException(status_code=400, detail="title is required")
            request_metadata = self._request_task_metadata(request)
            created.append(
                self._autonomous_task_state.create_task(
                    title=title,
                    summary=str(request.get("summary", "")),
                    trace_id=str(request.get("trace_id") or uuid.uuid4()),
                    task_type=self._task_profile_policy.request_type(request, metadata=request_metadata),
                    source=str(request.get("source", "self_learning")),
                    priority=str(request.get("priority", "normal")),
                    metadata=request_metadata,
                    evidence=dict(request.get("evidence") or {}),
                    constraints=dict(request.get("constraints") or {}),
                )
            )

        await self._touch_gateway_activity(
            "autonomous_chain_plan",
            metadata=self._build_autonomous_chain_activity_metadata(created, action="plan"),
        )
        if created:
            for task in created:
                self._record_endogenous_drive_outcome(task, event_type="planned")
            self._record_supervisor_ui_activity(
                "tasks_planned",
                scene="planning",
                summary=f"监督者已把 {len(created)} 个链路项纳入 API-B 判断在途存储。",
                metadata=self._build_autonomous_chain_activity_metadata(created, action="plan"),
            )

        return {
            "status": "planned",
            "tasks": [self._serialize_autonomous_chain_task(task) for task in created],
            "count": len(created),
        }

    async def decide_autonomous_chain_task(self, task_id: str, request: dict | None = None):
        request = request or {}
        task = self._autonomous_chain_store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Autonomous-chain task not found: {task_id}")

        normalized = normalize_autonomous_chain_decision(request.get("decision"))
        decision_context: Dict[str, Any] = {}

        if normalized is None or normalized == "auto":
            task_family = self._task_profile_policy.runtime_family(task)
            task_execution_kind = self._task_profile_policy.execution_kind(task)
            drive_input = await self._resolve_runtime_drive_input_request(
                request,
                default_task_family=task_family,
                default_execution_kind=task_execution_kind,
            )
            normalized, auto_reason = build_autonomous_chain_auto_decision(
                task=task,
                drive_input=drive_input,
                autonomous_chain_gate_active=getattr(
                    getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False
                ),
                task_profile_policy=self._task_profile_policy,
                active_tasks=self._active_autonomous_chain_tasks(),
                learning_history=self._autonomous_chain_store.list_writeback_history(
                    status="completed"
                ),
                now=datetime.now(timezone.utc),
                body_improvement_min_quality=float(
                    getattr(
                        self.config.service_runtime,
                        "body_improvement_min_quality",
                        60.0,
                    )
                    or 60.0
                ),
            )
            decision_context = self._normalize_runtime_decision_context(
                decision_context,
                drive_input=drive_input,
            )
            reason = str(request.get("reason") or auto_reason)
        else:
            reason = str(request.get("reason") or f"Task marked as {normalized} by supervisor decision.")
            request_context = request.get("context")
            if isinstance(request_context, dict) and request_context:
                decision_context = self._normalize_runtime_decision_context(
                    dict(request_context)
                )
            if normalized in {"completed", "failed"}:
                final_response = str(request.get("final_response") or "").strip()
                if final_response:
                    decision_context["autonomous_executor_final_response"] = final_response[:4000]

        if task.status == "cancelled":
            return {
                "status": "unchanged",
                "task": self._serialize_autonomous_chain_task(task),
                "reason": "Cancelled tasks are terminal and cannot be re-decided by the supervisor.",
            }

        actor = str(request.get("actor", "supervisor"))
        decision_id = str(request.get("decision_id") or uuid.uuid4())
        owner_session_id = self._ensure_agent_pull_task_owner(
            task=task,
            request=request,
            decision=normalized,
            actor=actor,
        )
        execution_request = None
        if normalized == "approved" and self._task_profile_policy.requires_execution_request(task):
            try:
                execution_request = self._build_autonomous_chain_execution_request(
                    task,
                    decision_id=decision_id,
                    actor=actor,
                    reason=reason,
                    decision_context=decision_context,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        if (
            normalized in {"completed", "failed"}
            and task.status == "approved"
            and is_agent_pull_task(
                task,
                task_profile_policy=self._task_profile_policy,
            )
            and owner_session_id
        ):
            task = self._autonomous_task_state.update_status(
                task_id,
                status="running",
                decision_id=str(uuid.uuid4()),
                actor=actor,
                reason=(
                    "监督者恢复过程曾把链路项重置为待执行；"
                    "当前已接纳这次晚到的 agent-pull 写回，并在终态写回前恢复运行中状态。"
                ),
                context={
                    **decision_context,
                    "session_id": owner_session_id,
                    "late_agent_pull_writeback": True,
                    "restored_from_status": "approved",
                },
                execution_request=None,
                event_type="writeback_reconcile",
            )

        updated_task = self._autonomous_task_state.update_status(
            task_id,
            status=normalized,
            decision_id=decision_id,
            actor=actor,
            reason=reason,
            context=decision_context,
            execution_request=execution_request,
            event_type="decision",
        )

        # Persist any metadata attached to the decision (e.g. executed_by_cli,
        # execution_result from CLI agent execution).
        decision_metadata = request.get("metadata")
        if normalized == "running" and owner_session_id:
            enriched_metadata = dict(decision_metadata or {})
            enriched_metadata.setdefault("owner_session_id", owner_session_id)
            enriched_metadata.setdefault("execution_source", "cli_agent_pull")
            decision_metadata = enriched_metadata
        if isinstance(decision_metadata, dict) and decision_metadata:
            self._autonomous_task_state.update_metadata(task_id, metadata=decision_metadata)

        promotion_candidate = None
        if normalized in {"approved", "running", "completed"}:
            promotion_candidate = (
                await self._propose_verified_conclusion_memory_promotion(
                    updated_task
                )
            )

        await self._touch_gateway_activity(
            self._planning_activity_kind_for_task(task.task_type),
            metadata=self._build_autonomous_chain_activity_metadata(
                [updated_task],
                action="decision",
                extra={"status": normalized},
            ),
        )
        self._record_supervisor_ui_activity(
            "task_decided",
            scene="planning",
            summary=f"监督者已将「{updated_task.title}」更新为 {normalized} 状态。",
            metadata={
                **self._task_activity_metadata(updated_task),
                "status": normalized,
            },
        )

        response = {
            "status": normalized,
            "task": self._serialize_autonomous_chain_task(updated_task),
        }
        if promotion_candidate is not None:
            response["memory_promotion_candidate"] = promotion_candidate
        return response

    async def review_autonomous_chain_tasks(self, request: dict | None = None):
        request = request or {}
        statuses = request.get("statuses") or ["planned", "deferred", "paused"]
        normalized_statuses = []
        for status in statuses:
            normalized = normalize_autonomous_chain_decision(str(status))
            if normalized is None or normalized == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported review status: {status}")
            normalized_statuses.append(normalized)

        drive_input = await self._resolve_runtime_drive_input_request(request)
        requested_task_family = self._task_profile_policy.normalize_family(
            request.get("execution_kind")
            or request.get("task_family")
            or drive_input.get("execution_kind")
            or drive_input.get("task_family")
        )
        requested_governance_task_type = self._task_profile_policy.normalize_type(requested_task_family)
        review_decision = (
            drive_input.get("task_family_decisions", {}).get(requested_task_family)
            or drive_input.get("governance_task_type_decisions", {}).get(
                requested_governance_task_type
            )
            or drive_input["decisions"]
        )
        default_review_status = (
            "approved" if review_decision["eligible_for_execution"] else "deferred"
        )

        candidate_tasks: list[AutonomousChainTask] = []
        for task in self._autonomous_chain_store.list_api_b_judgement_tasks():
            if task.status not in normalized_statuses or task.status == "cancelled":
                continue
            candidate_tasks.append(task)
        candidate_tasks.sort(key=self._schedule_allocator.task_sort_key)

        supervisor_review_actions = await self._review_task_governance_with_supervisor(
            candidate_tasks,
            drive_input=drive_input,
        )
        reserved_schedule_tokens = self._schedule_allocator.conflict_index(
            self._active_autonomous_chain_tasks(),
            exclude_task_ids={task.task_id for task in candidate_tasks}
        )

        reviewed = []
        reviewed_statuses = []
        for task in candidate_tasks:
            task_drive_input = drive_input
            task_family = self._task_profile_policy.runtime_family(task)
            if drive_input.get("task_family") != task_family:
                task_execution_kind = self._task_profile_policy.execution_kind(task)
                task_request = dict(request)
                task_drive_input = await self._resolve_runtime_drive_input_request(
                    task_request,
                    default_task_family=task_family,
                    default_execution_kind=task_execution_kind,
                )
            target_status, default_reason = build_autonomous_chain_auto_decision(
                task=task,
                drive_input=task_drive_input,
                autonomous_chain_gate_active=getattr(
                    getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False
                ),
                task_profile_policy=self._task_profile_policy,
                active_tasks=self._active_autonomous_chain_tasks(),
                learning_history=self._autonomous_chain_store.list_writeback_history(
                    status="completed"
                ),
                now=datetime.now(timezone.utc),
                body_improvement_min_quality=float(
                    getattr(
                        self.config.service_runtime,
                        "body_improvement_min_quality",
                        60.0,
                    )
                    or 60.0
                ),
            )
            decision_context: Dict[str, Any] = self._normalize_runtime_decision_context(
                drive_input=task_drive_input,
            )
            review_action = supervisor_review_actions.get(task.task_id)
            reprioritized = False
            if review_action:
                followup_suggestion = review_action.get("followup_suggestion")
                if isinstance(followup_suggestion, dict):
                    decision_context["supervisor_followup_suggestion"] = followup_suggestion
                priority_recommendation = self._extract_supervisor_priority_recommendation(review_action)
                if priority_recommendation is not None and priority_recommendation != str(task.priority):
                    task = self._autonomous_task_state.update_priority(
                        task.task_id,
                        priority=priority_recommendation,
                        actor=str(request.get("actor", "supervisor")),
                        reason=(
                            f"Supervisor review reprioritized task to {priority_recommendation}."
                        ),
                        context={
                            **decision_context,
                            "supervisor_priority_adjustment": {
                                "priority": priority_recommendation,
                                "reason": str(review_action.get("reason") or "").strip(),
                            },
                        },
                    )
                    decision_context["supervisor_priority_adjustment"] = {
                        "priority": priority_recommendation,
                        "reason": str(review_action.get("reason") or "").strip(),
                    }
                    reprioritized = True
                suggested_status = self._coerce_supervisor_review_action(
                    review_action.get("action"),
                    current_status=str(task.status),
                )
                if suggested_status is not None:
                    keep_deterministic_body_improvement_gate = (
                        self._task_profile_policy.execution_kind(task) == "body_improvement"
                        and target_status in {"cancelled", "deferred"}
                    )
                    preserve_agent_pull_approval = (
                        target_status == "approved"
                        and suggested_status in {"deferred", "paused"}
                        and is_agent_pull_task(
                            task,
                            task_profile_policy=self._task_profile_policy,
                        )
                    )
                    if preserve_agent_pull_approval:
                        decision_context["supervisor_followup_suggestion"] = {
                            "action": suggested_status,
                            "reason": str(review_action.get("reason") or "").strip(),
                            "preserved_status": target_status,
                        }
                    elif keep_deterministic_body_improvement_gate:
                        decision_context["supervisor_followup_suggestion"] = {
                            "action": suggested_status,
                            "reason": str(review_action.get("reason") or "").strip(),
                            "preserved_status": target_status,
                        }
                    else:
                        target_status = suggested_status
                    lm_reason = str(review_action.get("reason") or "").strip()
                    if (
                        lm_reason
                        and not keep_deterministic_body_improvement_gate
                    ):
                        default_reason = f"Supervisor review: {lm_reason}"
                    decision_context["supervisor_review_outcome"] = {
                        "action": suggested_status,
                        "reason": lm_reason,
                    }
                elif isinstance(followup_suggestion, dict):
                    default_reason = (
                        str(request.get("reason"))
                        or f"Supervisor follow-up suggestion recorded: "
                        f"{followup_suggestion.get('action', 'review')}."
                    )
                elif reprioritized and not str(request.get("reason") or "").strip():
                    default_reason = (
                        f"Supervisor review reprioritized task to "
                        f"{decision_context['supervisor_priority_adjustment']['priority']}."
                    )
            schedule_token = self._schedule_allocator.task_schedule_token(task)
            if target_status == "approved" and schedule_token:
                occupied = reserved_schedule_tokens.get(schedule_token)
                if occupied is not None:
                    target_status = "deferred"
                    decision_context["schedule_conflict"] = {
                        "scheduled_for": schedule_token,
                        "occupied_by_task_id": occupied.task_id,
                        "occupied_by_title": occupied.title,
                        "occupied_by_status": str(occupied.status),
                    }
                    default_reason = (
                        "该链路项暂缓：当前预设时点已被 "
                        f"「{occupied.title}」占用；同一个 scheduled_for 只能保留一个在途链路项。"
                    )
            execution_request = None
            if target_status == "approved":
                if self._task_profile_policy.requires_execution_request(task):
                    decision_id = str(request.get("decision_id") or uuid.uuid4())
                    try:
                        execution_request = self._build_autonomous_chain_execution_request(
                            task,
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=str(request.get("reason") or default_reason),
                            decision_context=self._normalize_runtime_decision_context(
                                drive_input=task_drive_input,
                            ),
                        )
                    except ValueError:
                        updated = self._autonomous_task_state.update_status(
                            task.task_id,
                            status="deferred",
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=(
                                "该链路项暂缓：当前自主交接缺少必要的谱系、目标槽位或回滚证据。"
                            ),
                            context=decision_context,
                            event_type="review",
                        )
                        reviewed.append(updated)
                        reviewed_statuses.append(updated.status)
                        continue
                else:
                    decision_id = str(request.get("decision_id") or uuid.uuid4())
            else:
                decision_id = str(request.get("decision_id") or uuid.uuid4())
            updated = self._autonomous_task_state.update_status(
                task.task_id,
                status=target_status,
                decision_id=decision_id,
                actor=str(request.get("actor", "supervisor")),
                reason=str(request.get("reason") or default_reason),
                context=decision_context,
                execution_request=execution_request,
                event_type="review",
            )
            if target_status == "approved":
                await self._propose_verified_conclusion_memory_promotion(updated)
            reviewed.append(updated)
            reviewed_statuses.append(updated.status)
            updated_schedule_token = self._schedule_allocator.task_schedule_token(updated)
            if target_status == "approved" and updated_schedule_token:
                reserved_schedule_tokens.setdefault(updated_schedule_token, updated)

        if reviewed:
            unique_statuses = sorted(set(reviewed_statuses))
            followup_suggestion_count = 0
            followup_action_counts: Dict[str, int] = {}
            priority_update_count = 0
            for task in reviewed:
                if not task.decision_history:
                    continue
                latest_context = dict(task.decision_history[-1].context or {})
                followup_suggestion = latest_context.get("supervisor_followup_suggestion")
                if not isinstance(followup_suggestion, dict):
                    pass
                else:
                    followup_suggestion_count += 1
                    action = str(followup_suggestion.get("action") or "unknown")
                    followup_action_counts[action] = followup_action_counts.get(action, 0) + 1
                if isinstance(latest_context.get("supervisor_priority_adjustment"), dict):
                    priority_update_count += 1
            self._record_supervisor_ui_activity(
                "tasks_reviewed",
                scene="planning",
                summary=(
                    f"监督者已复核 {len(reviewed)} 个链路项: {', '.join(unique_statuses)}。"
                    + (
                        f" 保留建议 {followup_suggestion_count} 条。"
                        if followup_suggestion_count > 0
                        else ""
                    )
                    + (
                        f" 优先级重排 {priority_update_count} 次。"
                        if priority_update_count > 0
                        else ""
                    )
                ),
                metadata=self._build_autonomous_chain_activity_metadata(
                    reviewed,
                    action="review",
                    extra={
                        "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                        "supervisor_followup_suggestions": followup_suggestion_count,
                        "supervisor_suggestion_action_counts": followup_action_counts,
                        "supervisor_priority_adjustments": priority_update_count,
                    },
                ),
            )
            await self._touch_gateway_activity(
                "autonomous_chain_plan",
                metadata=self._build_autonomous_chain_activity_metadata(
                    reviewed,
                    action="review",
                    extra={
                        "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                        "supervisor_followup_suggestions": followup_suggestion_count,
                        "supervisor_suggestion_action_counts": followup_action_counts,
                        "supervisor_priority_adjustments": priority_update_count,
                    },
                ),
            )
        else:
            unique_statuses = []

        response_fields = self._build_drive_input_response_fields(
            drive_input=drive_input,
        )
        return {
            "status": "reviewed",
            "decision": default_review_status,
            "reviewed_statuses": unique_statuses,
            "tasks": [self._serialize_autonomous_chain_task(task) for task in reviewed],
            "count": len(reviewed),
            **response_fields,
        }

    async def submit_self_learning_conclusion(self, request: dict | None = None):
        try:
            submission = SupervisorConclusionSubmission.model_validate(request or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        created = []
        for proposal in submission.proposals:
            proposal_metadata = {
                **submission.metadata,
                **proposal.metadata,
            }
            if proposal.governance_task_type is not None:
                proposal_metadata["governance_task_type"] = proposal.governance_task_type
            if proposal.task_family is not None:
                proposal_metadata["task_family"] = proposal.task_family
            if proposal.execution_kind is not None:
                proposal_metadata["execution_kind"] = proposal.execution_kind
            proposal_payload = {
                "task_type": proposal.task_type,
                "source": proposal.source,
                "governance_task_type": proposal.governance_task_type,
                "task_family": proposal.task_family,
                "execution_kind": proposal.execution_kind,
                "metadata": proposal_metadata,
            }
            task = self._autonomous_task_state.create_task(
                title=proposal.title,
                summary=proposal.summary,
                trace_id=str(submission.metadata.get("trace_id") or submission.conclusion_id or uuid.uuid4()),
                task_type=self._task_profile_policy.request_type(proposal_payload, metadata=proposal_metadata),
                source=proposal.source,
                priority=proposal.priority,
                metadata=proposal_metadata,
                evidence={
                    **submission.evidence,
                    **proposal.evidence,
                },
                constraints=dict(proposal.constraints),
            )
            created.append(self._serialize_autonomous_chain_task(task))

        if created:
            self._record_supervisor_ui_activity(
                "self_learning_submitted",
                scene="drive",
                summary=f"自主学习结论已提交 {len(created)} 个 API-B 判断在途提案。",
                metadata={
                    "count": len(created),
                    "conclusion_id": submission.conclusion_id,
                    "task_ids": [task.get("task_id") for task in created],
                },
            )
            await self._touch_gateway_activity(
                "self_learning",
                metadata={
                    "action": "self_learning_submission",
                    "count": len(created),
                    "conclusion_id": submission.conclusion_id,
                },
            )

        return {
            "status": "accepted",
            "source": submission.source,
            "conclusion_id": submission.conclusion_id,
            "topic_id": submission.topic_id,
            "count": len(created),
            "tasks": created,
        }

    async def _propose_verified_conclusion_memory_promotion(
        self,
        task: AutonomousChainTask,
    ) -> Optional[Dict[str, Any]]:
        """Persist a Governor-approved conclusion and request owner consent."""
        metadata = dict(task.metadata or {})
        if str(task.source or "").strip() != "self_learning" or not bool(
            metadata.get("verified")
        ):
            return None
        existing_status = str(
            metadata.get("memory_promotion_candidate_status") or ""
        ).strip()
        if existing_status in {"awaiting_user_consent", "already_governed"}:
            return {
                "status": existing_status,
                "candidate_id": metadata.get("memory_promotion_candidate_id"),
                "source_memory_id": metadata.get("evolution_memory_id"),
            }
        headers_auto = self._gateway_memory_headers(memory_actor="stellar_auto")
        headers_governor = self._gateway_memory_headers(memory_actor="governor")
        if not headers_auto or not headers_governor:
            return {"status": "deferred", "reason": "gateway_identity_unavailable"}

        execution_request = task.execution_request
        decision_id = str(
            getattr(execution_request, "decision_id", None)
            or (
                task.decision_history[-1].decision_id
                if task.decision_history
                else ""
            )
            or task.task_id
        )
        governance_ref = (
            f"autonomous-chain:{task.task_id}:decision:{decision_id}"
        )
        conclusion = str(
            task.evidence.get("summary")
            or task.summary
            or task.title
        ).strip()[:4000]
        if not conclusion:
            return {"status": "deferred", "reason": "conclusion_is_empty"}

        try:
            import aiohttp

            gateway_url = str(self.config.execution.gateway_address).rstrip("/")
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{gateway_url}/api/mem/remember",
                    json={
                        "title": f"Auto 结论：{str(task.title)[:280]}",
                        "summary": conclusion,
                        "topics": ["self_learning", "companion_candidate"],
                        "evidence_refs": [governance_ref],
                        "event_kind": "decision",
                        "importance": 0.85,
                        "source_actor": "stellar_auto_governed_conclusion",
                        "memory_domain": "evolution",
                    },
                    headers=headers_auto,
                ) as response:
                    memory_payload = await response.json()
                    if response.status != 200:
                        return {
                            "status": "deferred",
                            "reason": "evolution_memory_write_failed",
                            "http_status": response.status,
                        }
                memory_record = (
                    memory_payload.get("memory")
                    if isinstance(memory_payload, dict)
                    else None
                )
                source_memory_id = str(
                    (memory_record or {}).get("memory_id") or ""
                ).strip()
                if not source_memory_id:
                    return {
                        "status": "deferred",
                        "reason": "evolution_memory_id_missing",
                    }

                async with session.post(
                    f"{gateway_url}/api/mem/promotion-candidates",
                    json={
                        "source_memory_id": source_memory_id,
                        "source_type": "compressed",
                        "source_domain": "evolution",
                        "target_domain": "companion",
                        "reason": (
                            "Governor 已确认该 Auto 结论，可由本机所有者决定是否供日常陪伴召回。"
                        ),
                        "governance_ref": governance_ref,
                    },
                    headers=headers_governor,
                ) as response:
                    candidate_payload = await response.json()
                    if response.status == 409:
                        result = {
                            "status": "already_governed",
                            "source_memory_id": source_memory_id,
                        }
                    elif response.status != 200:
                        return {
                            "status": "deferred",
                            "reason": "promotion_candidate_write_failed",
                            "http_status": response.status,
                        }
                    else:
                        candidate = (
                            candidate_payload.get("candidate")
                            if isinstance(candidate_payload, dict)
                            else None
                        )
                        result = {
                            "status": "awaiting_user_consent",
                            "candidate_id": (candidate or {}).get("candidate_id"),
                            "source_memory_id": source_memory_id,
                        }
        except Exception as exc:
            logger.warning(
                "Verified conclusion promotion proposal failed for task %s: %s",
                task.task_id,
                exc,
            )
            return {
                "status": "deferred",
                "reason": "memory_promotion_service_unavailable",
            }

        self._autonomous_task_state.update_metadata(
            task.task_id,
            metadata={
                "evolution_memory_id": result.get("source_memory_id"),
                "memory_promotion_candidate_id": result.get("candidate_id"),
                "memory_promotion_candidate_status": result["status"],
                "memory_promotion_governance_ref": governance_ref,
            },
        )
        return result

    async def _handoff_autonomous_chain_execution_request(
        self,
        task: AutonomousChainTask,
        *,
        max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        execution_request = task.execution_request
        if execution_request is None:
            return None

        task_metadata = dict(task.metadata or {})
        if task.status == "running":
            return None

        await self._propose_verified_conclusion_memory_promotion(task)

        # ── Mark running BEFORE any await to prevent duplicate handoff ──
        self._autonomous_task_state.update_status(
            task.task_id,
            status="running",
            actor="supervisor",
            reason="自主交接已开始",
            event_type="execution_handoff_started",
        )
        self._autonomous_task_state.update_metadata(
            task.task_id,
            metadata={
                "executed_at": datetime.now(timezone.utc).isoformat(),
            },
            execution_request=execution_request,
        )

        payload = execution_request.model_dump(mode="json")
        try:
            result = await self._execution_facade.execute_autonomous_chain_request(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {
                "status": "execution_handoff_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            self._record_execution_handoff_failure(
                task,
                result=result,
                result_status=result["status"],
                max_retries=max_retries,
            )
            return result

        # ── Failure recovery: restore approved state so the task can be ──
        # retried on the next cycle.  Only explicit success statuses close the
        # task; empty or unknown statuses mean the executor did not confirm
        # completion.
        nested_result = (
            dict(result.get("result") or {}) if isinstance(result, dict) else {}
        )
        result_status = (
            nested_result.get("status")
            or (result.get("status") if isinstance(result, dict) else None)
        )
        normalized_result_status = (
            str(result_status).strip().lower() if result_status is not None else ""
        )
        _ERROR_STATUSES = frozenset({"error", "failed", "timeout", "unreachable"})
        _SUCCESS_STATUSES = frozenset(
            {
                "ok",
                "success",
                "executed",
                "completed",
                "complete",
                "compressed",
                "already_compressed",
                "learn_only_completed",
                "autonomous_chain_execution_executed",
                "autonomous_chain_execution_recorded",
            }
        )
        is_failure = (
            normalized_result_status in _ERROR_STATUSES
            or normalized_result_status not in _SUCCESS_STATUSES
        )
        if normalized_result_status == "upgrade_awaiting_user_consent":
            self._autonomous_task_state.update_metadata(
                task.task_id,
                metadata={
                    "execution_result": result,
                    "awaiting_user_consent_since": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )
            self._autonomous_task_state.update_status(
                task.task_id,
                status="awaiting_user_consent",
                actor="supervisor_executor",
                reason="Body candidate passed probe and Governor review; waiting for explicit user consent.",
                event_type="execution_awaiting_user_consent",
            )
            return result
        if is_failure:
            self._record_execution_handoff_failure(
                task,
                result=result,
                result_status=result_status,
                max_retries=max_retries,
            )
            return result

        # Success path — mark completed.  Reason text is split by execution
        # path so the audit trail is honest about WHO closed the task.  The
        # architectural baseline §3.4 says memory_maintenance is handled
        # internally by the memory service (not by API-A autonomous pull), so its
        # reason reflects the supervisor's internal completion. Body
        # upgrade / switch go through the executor body_lifecycle. API-A
        # pull handles autonomous-executor tasks separately, so this
        # success path is the supervisor's own.
        task_governance_type = self._task_profile_policy.governance_type(task)
        if task_governance_type == "memory_maintenance":
            actor = "supervisor_memory_service"
            completion_reason = (
                "Memory-maintenance task completed by the supervisor's "
                "internal memory service (baseline §3.4 — API-A pull only "
                "sees autonomous-executor tasks). "
                f"executor_status={str(result_status)[:60] if result_status else 'ok'}"
            )
        elif task_governance_type == "self_evolution":
            actor = "supervisor_executor"
            completion_reason = (
                "Autonomous-chain task completed by the supervisor's body "
                f"executor. executor_status={str(result_status)[:60] if result_status else 'ok'}"
            )
        else:
            actor = "supervisor"
            completion_reason = (
                f"自主交接已完成，执行结果：{str(result_status)[:100]}"
            )
        self._autonomous_task_state.update_status(
            task.task_id,
            status="completed",
            actor=actor,
            reason=completion_reason,
            event_type="execution_handoff_completed",
        )
        self._autonomous_task_state.update_metadata(
            task.task_id,
            metadata={"execution_result": result},
        )
        await self._touch_gateway_activity(
            "autonomous_chain_execute",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
            },
        )
        self._record_supervisor_ui_activity(
            "execution_handoff_started",
            scene="handoff",
            summary=f"已把「{task.title}」交接给执行面处理。",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
                "result_status": result_status,
            },
        )
        return result

    def _record_execution_handoff_failure(
        self,
        task: AutonomousChainTask,
        *,
        result: Dict[str, Any],
        result_status: Any,
        max_retries: int,
    ) -> None:
        current = self._autonomous_chain_store.get_task(task.task_id) or task
        failure_count = int(
            dict(current.metadata or {}).get("execution_failure_count") or 0
        ) + 1
        task_governance_type = self._task_profile_policy.governance_type(current)
        actor = (
            "supervisor_memory_service"
            if task_governance_type == "memory_maintenance"
            else "supervisor_executor"
        )
        terminal = failure_count >= max_retries
        self._autonomous_task_state.update_status(
            task.task_id,
            status="failed" if terminal else "approved",
            actor=actor,
            reason=(
                f"Execution handoff failed after {failure_count}/{max_retries} attempt(s); "
                f"executor_status={str(result_status)[:80] or 'unknown'}."
            ),
            event_type=(
                "execution_handoff_failed"
                if terminal
                else "execution_handoff_retry"
            ),
        )
        self._autonomous_task_state.update_metadata(
            task.task_id,
            metadata={
                "execution_failed": True,
                "execution_failure_count": failure_count,
                "execution_result": dict(result),
            },
        )

    def _reconcile_body_switch_consent_outcome(
        self,
        result: Dict[str, Any],
    ) -> None:
        task_link = dict(result.get("autonomous_task_link") or {})
        task_id = str(task_link.get("task_id") or "").strip()
        if not task_id:
            return
        task = self._autonomous_chain_store.get_task(task_id)
        if task is None or str(task.status) != "awaiting_user_consent":
            return
        status = str(result.get("status") or "").strip().lower()
        if status == "body_switch_activated":
            target_status = "completed"
            reason = "User approved the probe-passed body and activation completed."
        elif status == "body_switch_rejected":
            target_status = "cancelled"
            reason = "User rejected the body activation; the candidate returned to shell."
        else:
            return
        self._autonomous_task_state.update_metadata(
            task_id,
            metadata={"body_switch_consent_result": dict(result)},
        )
        self._autonomous_task_state.update_status(
            task_id,
            status=target_status,
            actor="user_consent",
            reason=reason,
            event_type="body_switch_consent_outcome",
        )

    async def _run_autonomous_chain_review_cycle(self) -> Dict[str, Any]:
        cycle_lock = self._get_autonomous_chain_cycle_lock()
        if cycle_lock.locked():
            logger.info("Skipping autonomous-chain review cycle because another cycle is already running.")
            return {
                "reviewed": 0,
                "handed_off": [],
                "recovered_orphaned": 0,
                "governance_consumption": {"count": 0, "consumed": []},
                "alignment_consumption": {"count": 0, "consumed": []},
                "truthfulness_consumption": {"count": 0, "consumed": []},
                "skipped": "cycle_already_running",
            }
        async with cycle_lock:
            return await self._run_autonomous_chain_review_cycle_locked()

    async def _run_autonomous_chain_review_cycle_locked(self) -> Dict[str, Any]:
        recovered_orphaned = await self._recover_orphaned_agent_pull_tasks()
        governance_consumption = self._consume_endogenous_governance_review_events()
        alignment_consumption = self._consume_endogenous_alignment_events()
        truthfulness_consumption = self._consume_endogenous_truthfulness_alerts()

        # ── Cleanup: auto-fail tasks stuck in "running" > 30 min ──
        stale_running = 0
        now = datetime.now(timezone.utc)
        for task in self._autonomous_chain_store.list_api_a_execution_lane_tasks(status="running"):
            started = task.metadata.get("executed_at") or task.metadata.get("execution_started_at")
            if started:
                try:
                    t = datetime.fromisoformat(str(started))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    if (now - t).total_seconds() > 1800:
                        self._autonomous_task_state.update_status(
                            task.task_id, status="failed",
                            reason="timeout: stuck >30min",
                            event_type="timeout",
                        )
                        stale_running += 1
                except Exception:
                    pass
        if stale_running:
            logger.warning("Auto-failed %d stale running tasks", stale_running)

        review_result = await self.review_autonomous_chain_tasks({})
        handed_off = []
        handoff_limit = int(
            getattr(self.config.service_runtime, "autonomous_chain_handoff_limit_per_cycle", 1)
            or 0
        )
        handoff_budget_exhausted = 0

        def _handoff_budget_available() -> bool:
            return handoff_limit <= 0 or len(handed_off) < handoff_limit

        # Pass 1: hand off tasks that were just transferred out of API-B
        # judgement in this review.
        handoff_considered_ids: set[str] = set()
        for task_payload in review_result.get("tasks", []):
            if task_payload.get("status") != "approved":
                continue

            task_payload_id = str(task_payload.get("task_id", "") or "")
            task = self._autonomous_chain_store.get_task(task_payload_id)
            if task is None:
                continue
            handoff_considered_ids.add(task.task_id)

            gov_type = self._task_profile_policy.governance_type(task)
            execution_kind = self._task_profile_policy.execution_kind(task)
            if gov_type == "self_learning" or execution_kind == "body_improvement":
                # Autonomous-executor tasks are pulled by API-A via Gateway /v1/tasks API.
                continue

            if task.execution_request is None:
                continue

            if not _handoff_budget_available():
                handoff_budget_exhausted += 1
                continue

            result = await self._handoff_autonomous_chain_execution_request(task)
            if result is not None:
                handed_off.append(
                    {
                        "task_id": task.task_id,
                        "status": result.get("status"),
                    }
                )

        # Pass 2: hand off any previously transferred tasks that were never
        # consumed, PLUS tasks whose previous handoff failed and were reset to
        # the handoff-ready `approved` enum for retry (execution_failed=True,
        # failure_count < max_retries).  Tasks in running state or
        # permanently failed are skipped here.
        handed_off_ids = {d["task_id"] for d in handed_off}
        for task in self._autonomous_chain_store.list_api_a_execution_lane_tasks(status="approved"):
            if task.task_id in handed_off_ids:
                continue
            if task.task_id in handoff_considered_ids:
                continue
            if task.status == "running":
                continue  # already running or permanently failed

            execution_kind = self._task_profile_policy.execution_kind(task)
            if self._task_profile_policy.governance_type(task) == "self_learning" or execution_kind == "body_improvement":
                # Autonomous-executor tasks are pulled by API-A via Gateway /v1/tasks API.
                # The supervisor only approves them; execution is API-A initiated.
                continue

            if task.execution_request is None:
                continue

            if not _handoff_budget_available():
                handoff_budget_exhausted += 1
                continue

            result = await self._handoff_autonomous_chain_execution_request(task)
            if result is not None:
                handed_off.append(
                    {"task_id": task.task_id, "status": result.get("status")}
                )

        return {
            "reviewed": review_result.get("count", 0),
            "handed_off": handed_off,
            "recovered_orphaned": recovered_orphaned,
            "governance_consumption": governance_consumption,
            "alignment_consumption": alignment_consumption,
            "truthfulness_consumption": truthfulness_consumption,
            "handoff_limit": handoff_limit,
            "handoff_budget_exhausted": handoff_budget_exhausted,
        }

    async def run_autonomous_cycle(self, request: dict | None = None) -> Dict[str, Any]:
        """Execute one full autonomous cycle: drive → plan → review → handoff.

        This is the single-endpoint entry point used by the ``/auto`` switch.
        It runs the same pipeline as the periodic background loops, but is
        triggered synchronously on demand so the autonomous chain responds
        immediately.

        Returns a summary of every phase so the CLI can report what happened.
        """
        request = request or {}
        focus = str(request.get("focus") or "").strip()
        phases: Dict[str, Any] = {}

        # ── Phase 1: Endogenous drive → form API-B judgement projections ──
        try:
            drive_result = await self._run_endogenous_drive_cycle()
            phases["drive"] = {
                "status": drive_result.get("status"),
                "planned": drive_result.get("planned", 0),
                "task_ids": [
                    task.get("task_id")
                    for task in drive_result.get("tasks", [])
                    if isinstance(task, dict)
                ],
            }
            now = datetime.now(timezone.utc)
            self._service_runtime.last_drive_at = now
            self._service_runtime.next_drive_at = now + timedelta(
                seconds=self.config.service_runtime.endogenous_drive_interval
            )
        except Exception as exc:
            phases["drive"] = {"status": "error", "error": str(exc)}

        # ── Phase 2: autonomous-chain review → approve & handoff ──
        try:
            cycle_result = await self._run_autonomous_chain_review_cycle()
            phases["review"] = {
                "reviewed": cycle_result.get("reviewed", 0),
                "handed_off": [
                    dict(item) if isinstance(item, dict) else {"task_id": str(item)}
                    for item in cycle_result.get("handed_off", [])
                ],
                "governance_consumption": dict(cycle_result.get("governance_consumption") or {}),
                "alignment_consumption": dict(cycle_result.get("alignment_consumption") or {}),
                "truthfulness_consumption": dict(cycle_result.get("truthfulness_consumption") or {}),
            }
            now = datetime.now(timezone.utc)
            self._service_runtime.last_review_at = now
            self._service_runtime.next_review_at = now + timedelta(
                seconds=self.config.service_runtime.autonomous_chain_review_interval
            )
        except Exception as exc:
            phases["review"] = {"status": "error", "error": str(exc)}

        # ── Phase 3: Record supervisor UI activity ──
        total_handed_off = len(phases.get("review", {}).get("handed_off", []))
        total_planned = phases.get("drive", {}).get("planned", 0)
        self._record_supervisor_ui_activity(
            "autonomous_cycle_completed",
            scene="handoff" if total_handed_off > 0 else "planning",
            summary=(
                f"自主链路一轮完成：新增 {total_planned} 个候选，"
                f"{total_handed_off} 个链路项已进入自主交接。"
            ),
            metadata={
                "phases": {
                    k: {sk: sv for sk, sv in v.items() if sk != "task_ids"}
                    for k, v in phases.items()
                },
                "total_planned": total_planned,
                "total_handed_off": total_handed_off,
                "focus": focus or None,
            },
        )

        return {
            "status": "completed",
            "phases": phases,
            "summary": {
                "planned": total_planned,
                "handed_off": total_handed_off,
                "governance_consumed": int(
                    phases.get("review", {}).get("governance_consumption", {}).get("count", 0)
                ),
                "alignment_consumed": int(
                    phases.get("review", {}).get("alignment_consumption", {}).get("count", 0)
                ),
                "truthfulness_consumed": int(
                    phases.get("review", {}).get("truthfulness_consumption", {}).get("count", 0)
                ),
                "focus": focus or None,
            },
        }

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

            from systems.evolution_boundary import normalize_repo_path

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

    async def _review_body_improvement(self, report):
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

        from systems.evolution_boundary import (
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
        return self._execution_facade.review_body(request)

