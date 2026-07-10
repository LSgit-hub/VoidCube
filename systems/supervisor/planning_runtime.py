from __future__ import annotations

import asyncio
import logging
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException
import aiohttp

from VoidCube_core.utils import atomic_json_write
from systems.runtime_task_profile import (
    derive_runtime_task_profile,
    normalize_runtime_task_family,
    normalize_runtime_task_type,
    resolve_broad_task_type,
)
from systems.self_learning.models import SupervisorConclusionSubmission
from systems.supervisor.endogenous_drive import (
    CORE_VALUES,
    TRUTHFULNESS_REVIEW_SIGNAL_THRESHOLD,
)
from systems.supervisor.autonomous_chain_store import (
    AutonomousChainExecutionRequest,
    AutonomousChainGitLineage,
    AutonomousChainTask,
)

logger = logging.getLogger("supervisor")


# ──────────────────────────────────────────────────────────────────────
# Supervisor scene taxonomy (baseline §3.4 / §3.6 / §13.2)
# ──────────────────────────────────────────────────────────────────────
# The supervisor (API-B) is the governance identity of Mem.  It only
# MANAGES the governance backlog and runs endogenous drive — it never executes
# learning or body-upgrade code.  Therefore the supervisor's `scene`
# field is restricted to the values below.  The Agent (API-A) is the
# only component that may surface "learning" / "execution" scenes.
#
#   idle         - at rest
#   planning     - deciding / approving / denying a governance-backlog item
#   memory       - actively touching long-term memory (Mem internal)
#   drive        - endogenous drive: cognitive evaluation / governance output
#   handoff      - handing an approved execution request to API-A / executor
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

    def _get_endogenous_drive_history_path(self) -> Path:
        path = getattr(self, "_endogenous_drive_history_path", None)
        if path is not None:
            return Path(path).resolve()
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
            or (Path(self.config.execution.git_repo_path) / ".soul-runtime")
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        resolved = runtime_root / "endogenous_drive_history.json"
        self._endogenous_drive_history_path = resolved
        return resolved

    def _get_endogenous_governance_events_path(self) -> Path:
        path = getattr(self, "_endogenous_governance_events_path", None)
        if path is not None:
            return Path(path).resolve()
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
            or (Path(self.config.execution.git_repo_path) / ".soul-runtime")
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        resolved = runtime_root / "endogenous_governance_events.json"
        self._endogenous_governance_events_path = resolved
        return resolved

    def _get_endogenous_cognition_state_path(self) -> Path:
        path = getattr(self, "_endogenous_cognition_state_path", None)
        if path is not None:
            return Path(path).resolve()
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
            or (Path(self.config.execution.git_repo_path) / ".soul-runtime")
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        resolved = runtime_root / "endogenous_cognition_state.json"
        self._endogenous_cognition_state_path = resolved
        return resolved

    def _get_endogenous_self_regulation_path(self) -> Path:
        path = getattr(self, "_endogenous_self_regulation_path", None)
        if path is not None:
            return Path(path).resolve()
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
            or (Path(self.config.execution.git_repo_path) / ".soul-runtime")
        ).resolve()
        runtime_root.mkdir(parents=True, exist_ok=True)
        resolved = runtime_root / "endogenous_self_regulation.json"
        self._endogenous_self_regulation_path = resolved
        return resolved

    def _load_endogenous_drive_history(self) -> Dict[str, Any]:
        path = self._get_endogenous_drive_history_path()
        if not path.exists():
            return self._endogenous_drive_history_default()
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return self._endogenous_drive_history_default()
        if not isinstance(raw, dict):
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
        snapshot["strategy_memory"] = self._normalize_endogenous_strategy_memory(
            raw.get("strategy_memory")
        )
        return self._trim_endogenous_drive_history(snapshot)

    def _load_endogenous_governance_events(self) -> Dict[str, Any]:
        path = self._get_endogenous_governance_events_path()
        if not path.exists():
            return self._endogenous_governance_events_default()
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return self._endogenous_governance_events_default()
        if not isinstance(raw, dict):
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
        path = self._get_endogenous_cognition_state_path()
        if not path.exists():
            return self._endogenous_cognition_state_default()
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return self._endogenous_cognition_state_default()
        if not isinstance(raw, dict):
            return self._endogenous_cognition_state_default()
        snapshot = self._endogenous_cognition_state_default()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["state"] = dict(raw.get("state") or {})
        return snapshot

    def _load_endogenous_self_regulation(self) -> Dict[str, Any]:
        path = self._get_endogenous_self_regulation_path()
        if not path.exists():
            return self._endogenous_self_regulation_default()
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return self._endogenous_self_regulation_default()
        if not isinstance(raw, dict):
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
        trimmed["strategy_memory"] = self._normalize_endogenous_strategy_memory(
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
        path = self._get_endogenous_drive_history_path()
        payload = self._trim_endogenous_drive_history(snapshot)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json_write(path, payload)

    def _persist_endogenous_governance_events(self, snapshot: Dict[str, Any]) -> None:
        path = self._get_endogenous_governance_events_path()
        payload = self._trim_endogenous_governance_events(snapshot)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json_write(path, payload)

    def _persist_endogenous_cognition_state(self, state: Dict[str, Any]) -> None:
        path = self._get_endogenous_cognition_state_path()
        payload = self._endogenous_cognition_state_default()
        payload["state"] = dict(state or {})
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json_write(path, payload)

    def _persist_endogenous_self_regulation(self, snapshot: Dict[str, Any]) -> None:
        path = self._get_endogenous_self_regulation_path()
        payload = dict(snapshot or {})
        payload["version"] = 1
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json_write(path, payload)

    def _history_for_endogenous_drive(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        judgements = [
            dict(item)
            for item in list(snapshot.get("judgements") or [])[:24]
            if isinstance(item, dict)
        ]
        outcomes = [
            dict(item)
            for item in list(snapshot.get("outcomes") or [])[:36]
            if isinstance(item, dict)
        ]
        return {
            "judgements": judgements,
            "outcomes": outcomes,
            "strategy_memory": self._normalize_endogenous_strategy_memory(
                snapshot.get("strategy_memory")
            ),
        }

    def _governance_events_for_runtime(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        events = [
            dict(item)
            for item in list(snapshot.get("events") or [])[:36]
            if isinstance(item, dict)
        ]
        return {
            "events": events,
        }

    def _derive_endogenous_corrective_mode(
        self,
        self_regulation: Dict[str, Any],
    ) -> Dict[str, Any]:
        throttle = max(0.0, float(self_regulation.get("dynamic_candidate_throttle_boost") or 0.0))
        observation = max(0.0, float(self_regulation.get("dynamic_observation_bias_boost") or 0.0))
        truthfulness = max(0.0, float(self_regulation.get("dynamic_truthfulness_bias_boost") or 0.0))
        learning_suppression = max(
            0.0,
            float(self_regulation.get("dynamic_learning_expansion_suppression") or 0.0),
        )
        active = {
            "candidate_throttle": round(throttle, 4),
            "observation_bias": round(observation, 4),
            "truthfulness_bias": round(truthfulness, 4),
            "learning_suppression": round(learning_suppression, 4),
        }
        mode = "rest"
        if truthfulness > 0.01 or learning_suppression > 0.01:
            mode = "corrective"
        elif throttle > 0.01 or observation > 0.01:
            mode = "guarded"
        return {
            "mode": mode,
            "active": mode != "rest",
            "last_reason": self_regulation.get("last_reason"),
            "active_boosts": active,
        }

    def _lm_reasoning_state_for_current_cycle(self) -> Dict[str, Any]:
        runtime_config = getattr(self.config, "service_runtime", None)
        if not bool(getattr(runtime_config, "endogenous_drive_lm_task_generation_enabled", False)):
            return {}
        engine = getattr(self, "_endogenous_drive_engine", None)
        if engine is None or not hasattr(engine, "get_latest_lm_task_generation_context"):
            return {}
        try:
            state = dict(engine.get_latest_lm_task_generation_context() or {})
        except Exception:
            return {}
        if str(state.get("status") or "").strip().lower() != "completed":
            return {}
        return state

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
            reflection.get("api_b_judgement_blockage_pressure")
            if reflection.get("api_b_judgement_blockage_pressure") is not None
            else reflection.get("governance_backlog_blockage_pressure")
            or 0.0
        ) >= 0.18:
            return adjusted
        if str(reflection.get("learning_yield_state") or "").strip().lower() not in {"mixed", "strong"}:
            return adjusted
        if max(
            0,
            int(
                perception.get("api_b_judgement_count")
                if perception.get("api_b_judgement_count") is not None
                else perception.get("governance_backlog_count")
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
            or dominant_constraint in {"api_b_judgement_blockage", "governance_backlog_blockage", "historical_underdelivery"}
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
                if payload.get("api_b_judgement_count") is not None
                else payload.get("governance_backlog_count")
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
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        request = dict(request or {})
        if "activity_guards" in request:
            raise HTTPException(
                status_code=400,
                detail="activity_guards is no longer accepted; use drive_input.",
            )
        default_governance_task_type = None
        if default_task_family is not None:
            default_governance_task_type = self._normalize_runtime_task_type(
                default_task_family
            )
        elif default_execution_kind is not None:
            default_governance_task_type = self._normalize_runtime_task_type(
                default_execution_kind
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
                    getattr(
                        getattr(self, "_service_runtime", None),
                        "autonomous_chain_gate_active",
                        False,
                    ),
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
                getattr(
                    getattr(self, "_service_runtime", None),
                    "autonomous_chain_gate_active",
                    False,
                ),
            )
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
            history_snapshot.get("strategy_memory")
        )
        corrective_mode = self._derive_endogenous_corrective_mode(self_regulation)
        attention_agenda = self._build_endogenous_attention_agenda(
            deliberation=deliberation,
            governance_channels=governance_channels,
            strategy_memory=strategy_memory,
        )
        uncertainty_ledger = self._build_endogenous_uncertainty_ledger(
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
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
        judgement_core = self._build_endogenous_judgement_core(
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
            history_snapshot.get("strategy_memory")
        )
        recent_events = [
            dict(item)
            for item in list(governance_event_stream.get("events") or [])[:12]
            if isinstance(item, dict)
        ]
        channel_counts = {
            "task_candidates": len(list(governance_channels.get("task_candidates") or [])),
            "observation_requests": len(list(governance_channels.get("observation_requests") or [])),
            "governance_review_requests": len(
                list(governance_channels.get("governance_review_requests") or [])
            ),
            "truthfulness_alerts": len(list(governance_channels.get("truthfulness_alerts") or [])),
            "autonomy_alignment_requests": len(
                list(governance_channels.get("autonomy_alignment_requests") or [])
            ),
        }

        return {
            "status": "evaluated",
            "enabled": bool(self.config.service_runtime.endogenous_drive_enabled),
            "identity": {
                "role": "endogenous_supervisory_core",
                "responsibility": (
                    "Perceive user, system, and self state; then govern autonomous "
                    "direction before execution."
                ),
                "execution_scope": "governance_only",
                "execution_chain_coupled": False,
            },
            "perception": perception,
            "world_model": world_model,
            "self_model": {
                "reflection": reflection,
                "adaptive_policy": adaptive_policy,
                "self_regulation": dict(self_regulation),
                "corrective_mode": corrective_mode,
            },
            "judgement_core": judgement_core,
            "governance": {
                "posture": drive_posture,
                "preferred_focus": adaptive_policy.get("preferred_focus"),
                "dominant_constraint": reflection.get("dominant_constraint"),
                "channel_counts": channel_counts,
                "channels": dict(governance_channels),
            },
            "proposal_cognition": proposal_cognition,
            "attention_agenda": attention_agenda,
            "uncertainty_ledger": uncertainty_ledger,
            "observation_program": observation_program,
            "meta_governance": meta_governance,
            "strategy_memory": {
                "focus_stats": dict(strategy_memory.get("focus_stats") or {}),
                "agenda_topic_stats": dict(strategy_memory.get("agenda_topic_stats") or {}),
                "observation_target_stats": dict(strategy_memory.get("observation_target_stats") or {}),
                "meta_governance_stats": dict(strategy_memory.get("meta_governance_stats") or {}),
                "context_key": context_key,
                "current_context_focus_stats": dict(
                    (strategy_memory.get("contextual_focus_stats") or {}).get(context_key) or {}
                ),
                "current_agenda_topic_stats": {
                    str(entry.get("topic") or "").strip().lower(): dict(
                        dict(strategy_memory.get("agenda_topic_stats") or {}).get(
                            str(entry.get("topic") or "").strip().lower()
                        ) or {}
                    )
                    for entry in list(attention_agenda.get("entries") or [])
                    if isinstance(entry, dict) and str(entry.get("topic") or "").strip()
                },
                "current_observation_target_stats": {
                    str(entry.get("target") or "").strip().lower(): dict(
                        dict(strategy_memory.get("observation_target_stats") or {}).get(
                            str(entry.get("target") or "").strip().lower()
                        ) or {}
                    )
                    for entry in list(observation_program.get("entries") or [])
                    if isinstance(entry, dict) and str(entry.get("target") or "").strip()
                },
                "current_meta_governance_stats": {
                    str(meta_governance.get("mode") or "").strip().lower(): dict(
                        dict(strategy_memory.get("meta_governance_stats") or {}).get(
                            str(meta_governance.get("mode") or "").strip().lower()
                        ) or {}
                    )
                    if str(meta_governance.get("mode") or "").strip()
                    else {}
                },
            },
            "recent_events": recent_events,
        }

    def _build_endogenous_judgement_core(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        attention_agenda: Dict[str, Any],
        uncertainty_ledger: Dict[str, Any],
        observation_program: Dict[str, Any],
        meta_governance: Dict[str, Any],
    ) -> Dict[str, Any]:
        reflection = dict(deliberation.get("reflection") or {})
        adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
        needs = [
            dict(item)
            for item in list(deliberation.get("needs") or [])[:6]
            if isinstance(item, dict)
        ]
        intents = [
            dict(item)
            for item in list(deliberation.get("intents") or [])[:6]
            if isinstance(item, dict)
        ]

        primary_need = dict(needs[0]) if needs else {}
        primary_intent: Dict[str, Any] = {}
        if primary_need:
            primary_need_type = str(primary_need.get("need_type") or "").strip()
            if primary_need_type:
                for intent in intents:
                    source_needs = [
                        str(item).strip()
                        for item in list(intent.get("source_needs") or [])
                        if str(item).strip()
                    ]
                    if primary_need_type in source_needs:
                        primary_intent = dict(intent)
                        break
        if not primary_intent and intents:
            primary_intent = dict(intents[0])
        governance_summary = {
            "preferred_focus": str(adaptive_policy.get("preferred_focus") or "").strip() or None,
            "dominant_constraint": str(reflection.get("dominant_constraint") or "").strip() or None,
            "posture_signal_type": str(
                dict(governance_channels.get("posture") or {}).get("signal_type") or ""
            ).strip()
            or None,
            "observation_request_count": len(
                list(governance_channels.get("observation_requests") or [])
            ),
            "governance_review_request_count": len(
                list(governance_channels.get("governance_review_requests") or [])
            ),
            "truthfulness_alert_count": len(
                list(governance_channels.get("truthfulness_alerts") or [])
            ),
            "autonomy_alignment_request_count": len(
                list(governance_channels.get("autonomy_alignment_requests") or [])
            ),
        }

        summary_parts = [
            (
                f"primary_need={str(primary_need.get('need_type') or '').strip()}"
                if str(primary_need.get("need_type") or "").strip()
                else ""
            ),
            (
                f"primary_intent={str(primary_intent.get('intent_type') or '').strip()}"
                if str(primary_intent.get("intent_type") or "").strip()
                else ""
            ),
            (
                f"focus={str(adaptive_policy.get('preferred_focus') or '').strip()}"
                if str(adaptive_policy.get("preferred_focus") or "").strip()
                else ""
            ),
            (
                f"constraint={str(reflection.get('dominant_constraint') or '').strip()}"
                if str(reflection.get("dominant_constraint") or "").strip()
                else ""
            ),
        ]
        summary = "Judgement core: " + "; ".join([item for item in summary_parts if item])
        if summary == "Judgement core: ":
            summary = "Judgement core is not available yet."

        return {
            "summary": summary,
            "primary_need": primary_need or None,
            "primary_intent": primary_intent or None,
            "governance_outputs": governance_summary,
            "active_needs": needs,
            "active_intents": intents,
        }

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
            lm_reasoning_state = self._lm_reasoning_state_for_current_cycle()
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
        compact_memory = self._compact_endogenous_proposal_memory(
            recent_reference_alignment=recent_reference_alignment,
            proposal_drift_memory=proposal_drift_memory,
            recent_cognitive_alignment=recent_cognitive_alignment,
            cognitive_assessment_memory=cognitive_assessment_memory,
            self_iteration_hypotheses=self_iteration_hypotheses,
            self_iteration_trend_memory=self_iteration_trend_memory,
            switch_self_regulation_memory=switch_self_regulation_memory,
            post_task_effect_memory=post_task_effect_memory,
        )

        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip() or "unknown"
        posture_name = str(active_cognitive_posture_profile.get("name") or "").strip() or "unknown"
        summary = (
            f"posture={posture_name}; "
            f"drift={drift_state}."
        )
        current_judgement = str(
            cognitive_assessment_memory.get("current_judgement") or ""
        ).strip()
        why_not_improvement_now_count = max(
            0,
            int(cognitive_assessment_memory.get("why_not_improvement_now_count") or 0),
        )

        return {
            "summary": summary,
            "lm_trace": {
                "available": bool(lm_reasoning_state),
                "status": str(lm_reasoning_state.get("status") or "").strip() or None,
                "model_role": str(lm_reasoning_state.get("model_role") or "").strip() or None,
                "charter_core_mission": str(
                    dict(lm_reasoning_state.get("charter") or {}).get("core_mission") or ""
                ).strip()
                or None,
                "proposal_count": max(0, int(lm_reasoning_state.get("proposal_count") or 0)),
            },
            "cognitive_control_policy": self._current_cognitive_control_policy(),
            "active_cognitive_posture_profile": active_cognitive_posture_profile,
            "meta_cognition_profile": {
                "available": bool(meta_cognition_profile.get("available")),
                "current_judgement": str(
                    meta_cognition_profile.get("current_judgement") or ""
                ).strip(),
                "dominant_constraint": str(
                    meta_cognition_profile.get("dominant_constraint") or ""
                ).strip(),
                "grounding_pressure": str(
                    meta_cognition_profile.get("grounding_pressure") or ""
                ).strip(),
                "dominant_failure_mode": str(
                    meta_cognition_profile.get("dominant_failure_mode") or ""
                ).strip(),
                "governance_posture": str(
                    meta_cognition_profile.get("governance_posture")
                    or meta_cognition_profile.get("recommended_task_posture")
                    or ""
                ).strip(),
                "priority_signals": [
                    str(item).strip()
                    for item in list(meta_cognition_profile.get("priority_signals") or [])[:4]
                    if str(item).strip()
                ],
                "self_iteration_focus": {
                    "domain": str(
                        meta_cognition_profile.get("top_self_iteration_domain") or ""
                    ).strip()
                    or None,
                    "hypothesis": str(
                        meta_cognition_profile.get("top_self_iteration_hypothesis") or ""
                    ).strip()
                    or None,
                },
            },
            "assessment_trace": {
                "available": bool(cognitive_assessment_memory.get("available")),
                "dominant_constraint": str(
                    cognitive_assessment_memory.get("dominant_constraint") or ""
                ).strip()
                or None,
                "current_judgement": current_judgement or None,
                "why_not_improvement_now": str(
                    cognitive_assessment_memory.get("why_not_improvement_now") or ""
                ).strip()
                or None,
                "why_not_improvement_now_count": why_not_improvement_now_count,
                "self_iteration_target": str(
                    cognitive_assessment_memory.get("self_iteration_target") or ""
                ).strip()
                or None,
                "self_iteration_hypothesis": str(
                    cognitive_assessment_memory.get("self_iteration_hypothesis") or ""
                ).strip()
                or None,
            },
            "auxiliary_memory": compact_memory,
            "current_candidates": current_candidates,
        }

    def _compact_endogenous_proposal_memory(
        self,
        *,
        recent_reference_alignment: Dict[str, Any],
        proposal_drift_memory: Dict[str, Any],
        recent_cognitive_alignment: Dict[str, Any],
        cognitive_assessment_memory: Dict[str, Any],
        self_iteration_hypotheses: Dict[str, Any],
        self_iteration_trend_memory: Dict[str, Any],
        switch_self_regulation_memory: Dict[str, Any],
        post_task_effect_memory: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        def _stored_count(item: Dict[str, Any], key: str) -> int:
            return max(0, int(item.get(key) or 0))

        def _signal_count(item: Dict[str, Any], keys: tuple[str, ...]) -> int:
            return max([0, *(_stored_count(item, key) for key in keys)])

        return {
            "recent_reference_alignment": {
                "available": bool(recent_reference_alignment.get("available")),
                "average_alignment_score": round(
                    self._clamp_endogenous_ratio(
                        recent_reference_alignment.get("average_alignment_score") or 0.0
                    ),
                    4,
                ),
                "weak_or_partial_count": _stored_count(
                    recent_reference_alignment,
                    "weak_or_partial_count",
                ),
                "entry_count": _stored_count(
                    recent_reference_alignment,
                    "entry_count",
                ),
                "primary_missing_evidence_node": str(
                    recent_reference_alignment.get("primary_missing_evidence_node") or ""
                ).strip()
                or None,
                "primary_missing_agenda_node": str(
                    recent_reference_alignment.get("primary_missing_agenda_node") or ""
                ).strip()
                or None,
                "missing_evidence_node_count": _stored_count(
                    recent_reference_alignment,
                    "missing_evidence_node_count",
                ),
                "missing_agenda_node_count": _stored_count(
                    recent_reference_alignment,
                    "missing_agenda_node_count",
                ),
            },
            "proposal_drift_memory": {
                "available": bool(proposal_drift_memory.get("available")),
                "average_score": round(
                    self._clamp_endogenous_ratio(
                        proposal_drift_memory.get("average_score") or 0.0
                    ),
                    4,
                ),
                "drift_state": str(
                    proposal_drift_memory.get("drift_state") or ""
                ).strip(),
                "quality_counts": dict(proposal_drift_memory.get("quality_counts") or {}),
                "posture_alignment_signal_count": _stored_count(
                    proposal_drift_memory,
                    "posture_alignment_signal_count",
                ),
                "priority_basis_signal_count": _stored_count(
                    proposal_drift_memory,
                    "priority_basis_signal_count",
                ),
                "missing_posture_alignment_count": _stored_count(
                    proposal_drift_memory,
                    "missing_posture_alignment_count",
                ),
                "missing_priority_basis_count": _stored_count(
                    proposal_drift_memory,
                    "missing_priority_basis_count",
                ),
                "posture_alignment_health": str(
                    proposal_drift_memory.get("posture_alignment_health") or ""
                ).strip(),
                "priority_basis_health": str(
                    proposal_drift_memory.get("priority_basis_health") or ""
                ).strip(),
                "dominant_posture_conflict_reason": str(
                    proposal_drift_memory.get("dominant_posture_conflict_reason") or ""
                ).strip()
                or None,
            },
            "recent_cognitive_alignment": {
                "available": bool(recent_cognitive_alignment.get("available")),
                "average_score": round(
                    self._clamp_endogenous_ratio(
                        recent_cognitive_alignment.get("average_score") or 0.0
                    ),
                    4,
                ),
                "quality_counts": dict(recent_cognitive_alignment.get("quality_counts") or {}),
                "dominant_task_shape": str(
                    recent_cognitive_alignment.get("dominant_task_shape") or ""
                ).strip()
                or None,
                "reason_count": _stored_count(
                    recent_cognitive_alignment,
                    "reason_count",
                ),
                "posture_alignment_signal_count": _stored_count(
                    recent_cognitive_alignment,
                    "posture_alignment_signal_count",
                ),
                "priority_basis_signal_count": _stored_count(
                    recent_cognitive_alignment,
                    "priority_basis_signal_count",
                ),
                "missing_posture_alignment_count": _stored_count(
                    recent_cognitive_alignment,
                    "missing_posture_alignment_count",
                ),
                "missing_priority_basis_count": _stored_count(
                    recent_cognitive_alignment,
                    "missing_priority_basis_count",
                ),
                "entry_count": _stored_count(
                    recent_cognitive_alignment,
                    "entry_count",
                ),
            },
            "cognitive_assessment_memory": {
                "available": bool(cognitive_assessment_memory.get("available")),
                "dominant_constraint": str(
                    cognitive_assessment_memory.get("dominant_constraint") or ""
                ).strip(),
                "current_judgement_count": max(
                    _signal_count(
                        cognitive_assessment_memory,
                        ("current_judgement_count",),
                    ),
                    1 if str(cognitive_assessment_memory.get("current_judgement") or "").strip() else 0,
                ),
                "self_iteration_target_count": max(
                    _signal_count(
                        cognitive_assessment_memory,
                        ("self_iteration_target_count", "target_count"),
                    ),
                    1
                    if str(cognitive_assessment_memory.get("self_iteration_target") or "").strip()
                    else 0,
                ),
                "self_iteration_hypothesis_count": max(
                    _signal_count(
                        cognitive_assessment_memory,
                        ("self_iteration_hypothesis_count", "hypothesis_count"),
                    ),
                    1
                    if str(
                        cognitive_assessment_memory.get("self_iteration_hypothesis") or ""
                    ).strip()
                    else 0,
                ),
            },
            "self_iteration_hypotheses": {
                "available": bool(self_iteration_hypotheses.get("available")),
                "dominant_hypothesis": str(
                    self_iteration_hypotheses.get("dominant_hypothesis") or ""
                ).strip(),
                "top_target_domain": str(
                    self_iteration_hypotheses.get("top_target_domain") or ""
                ).strip(),
                "hypothesis_count": max(
                    _stored_count(self_iteration_hypotheses, "hypothesis_count"),
                    1
                    if str(self_iteration_hypotheses.get("dominant_hypothesis") or "").strip()
                    else 0,
                ),
            },
            "self_iteration_trend_memory": {
                "available": bool(self_iteration_trend_memory.get("available")),
                "dominant_target": str(
                    self_iteration_trend_memory.get("dominant_target") or ""
                ).strip(),
                "trend_state": str(
                    self_iteration_trend_memory.get("trend_state") or ""
                ).strip(),
                "target_stability": str(
                    self_iteration_trend_memory.get("target_stability") or ""
                ).strip(),
                "target_count": max(
                    _signal_count(
                        self_iteration_trend_memory,
                        ("target_count", "target_signal_count"),
                    ),
                    1 if str(self_iteration_trend_memory.get("dominant_target") or "").strip() else 0,
                ),
                "hypothesis_count": max(
                    _signal_count(
                        self_iteration_trend_memory,
                        ("hypothesis_count", "hypothesis_signal_count"),
                    ),
                    1
                    if str(self_iteration_trend_memory.get("dominant_hypothesis") or "").strip()
                    else 0,
                ),
                "stay_or_switch_count": max(
                    _signal_count(
                        self_iteration_trend_memory,
                        ("stay_or_switch_count", "stay_or_switch_signal_count"),
                    ),
                    1
                    if str(
                        self_iteration_trend_memory.get("dominant_stay_or_switch") or ""
                    ).strip()
                    else 0,
                ),
                "switch_reason_count": max(
                    _signal_count(
                        self_iteration_trend_memory,
                        ("switch_reason_count", "switch_reason_signal_count"),
                    ),
                    1
                    if str(
                        self_iteration_trend_memory.get("dominant_switch_reason") or ""
                    ).strip()
                    else 0,
                ),
            },
            "switch_self_regulation_memory": {
                "available": bool(switch_self_regulation_memory.get("available")),
                "preferred_switch_bias": str(
                    switch_self_regulation_memory.get("preferred_switch_bias") or ""
                ).strip(),
                "switch_effectiveness": str(
                    switch_self_regulation_memory.get("switch_effectiveness") or ""
                ).strip(),
                "stay_effectiveness": str(
                    switch_self_regulation_memory.get("stay_effectiveness") or ""
                ).strip(),
                "average_switch_quality": round(
                    self._clamp_endogenous_ratio(
                        switch_self_regulation_memory.get("average_switch_quality") or 0.0
                    ),
                    4,
                ),
                "average_stay_quality": round(
                    self._clamp_endogenous_ratio(
                        switch_self_regulation_memory.get("average_stay_quality") or 0.0
                    ),
                    4,
                ),
                "stay_or_switch_count": _signal_count(
                    switch_self_regulation_memory,
                    ("stay_or_switch_count", "stay_or_switch_signal_count"),
                ),
            },
            "post_task_effect_memory": {
                "available": bool(post_task_effect_memory.get("available")),
                "effect_direction": str(
                    post_task_effect_memory.get("effect_direction") or ""
                ).strip(),
                "average_quality_score": round(
                    self._clamp_endogenous_ratio(
                        post_task_effect_memory.get("average_quality_score") or 0.0
                    ),
                    4,
                ),
                "average_cognitive_alignment_score": round(
                    self._clamp_endogenous_ratio(
                        post_task_effect_memory.get("average_cognitive_alignment_score")
                        or 0.0
                    ),
                    4,
                ),
                "average_reference_alignment_score": round(
                    self._clamp_endogenous_ratio(
                        post_task_effect_memory.get("average_reference_alignment_score")
                        or 0.0
                    ),
                    4,
                ),
                "dominant_target_effect": str(
                    post_task_effect_memory.get("dominant_target_effect") or ""
                ).strip()
                or None,
            },
        }

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

    def _derive_endogenous_meta_governance_mode(
        self,
        *,
        attention_agenda: Dict[str, Any],
        uncertainty_ledger: Dict[str, Any],
        observation_program: Dict[str, Any],
        self_regulation: Dict[str, Any],
        reflection: Dict[str, Any],
        adaptive_policy: Dict[str, Any],
        strategy_memory: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        agenda_entries = [
            dict(item)
            for item in list(attention_agenda.get("entries") or [])
            if isinstance(item, dict)
        ]
        ledger_entries = [
            dict(item)
            for item in list(uncertainty_ledger.get("entries") or [])
            if isinstance(item, dict)
        ]
        observation_entries = [
            dict(item)
            for item in list(observation_program.get("entries") or [])
            if isinstance(item, dict)
        ]

        dominant_agenda = agenda_entries[0] if agenda_entries else {}
        dominant_uncertainty = ledger_entries[0] if ledger_entries else {}
        dominant_observation = observation_entries[0] if observation_entries else {}
        current_focus = str(adaptive_policy.get("preferred_focus") or "").strip().lower()
        dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()
        corrective_mode = self._derive_endogenous_corrective_mode(self_regulation)
        normalized_strategy_memory = self._normalize_endogenous_strategy_memory(strategy_memory)
        meta_governance_stats = dict(normalized_strategy_memory.get("meta_governance_stats") or {})

        observation_priority = float(dominant_observation.get("priority") or 0.0)
        uncertainty_risk = float(dominant_uncertainty.get("risk") or 0.0)
        agenda_priority = float(dominant_agenda.get("priority") or 0.0)
        candidate_throttle = float(adaptive_policy.get("candidate_throttle") or 0.0)
        observation_bias = float(adaptive_policy.get("observation_bias") or 0.0)
        autonomy_readiness = float(reflection.get("autonomy_readiness") or 0.0)
        last_mode = None
        last_mode_stats: Dict[str, Any] = {}
        if meta_governance_stats:
            last_mode, last_mode_stats = max(
                meta_governance_stats.items(),
                key=lambda item: (
                    int(item[1].get("seen") or 0),
                    int(item[1].get("active_cycles") or 0),
                    float(item[1].get("last_confidence") or 0.0),
                ),
            )
            last_mode = str(last_mode or "").strip().lower() or None
            last_mode_stats = dict(last_mode_stats or {})

        mode_scores = {
            "observe": (
                observation_priority * 0.42
                + observation_bias * 0.22
                + uncertainty_risk * 0.2
                + (0.1 if current_focus == "observation" else 0.0)
                + (0.08 if dominant_constraint in {"weak_learning_yield", "historical_underdelivery", "api_b_judgement_blockage", "governance_backlog_blockage"} else 0.0)
                + (0.06 if last_mode == "observe" else 0.0)
                - (0.04 if last_mode == "expand" and uncertainty_risk < 0.3 else 0.0)
            ),
            "correct": (
                float(self_regulation.get("dynamic_truthfulness_bias_boost") or 0.0) * 0.38
                + float(self_regulation.get("dynamic_learning_expansion_suppression") or 0.0) * 0.24
                + uncertainty_risk * 0.18
                + (0.08 if corrective_mode.get("mode") == "corrective" else 0.0)
                + (0.05 if last_mode == "correct" else 0.0)
            ),
            "expand": (
                agenda_priority * 0.34
                + float(adaptive_policy.get("learning_expansion_bias") or 0.0) * 0.26
                + max(0.0, 0.58 - candidate_throttle) * 0.2
                + max(0.0, autonomy_readiness - 0.35) * 0.1
                - uncertainty_risk * 0.12
                + (0.05 if last_mode == "expand" else 0.0)
                - (0.03 if last_mode == "observe" and uncertainty_risk > 0.45 else 0.0)
            ),
            "conserve": (
                candidate_throttle * 0.35
                + max(0.0, 0.52 - autonomy_readiness) * 0.22
                + float(self_regulation.get("dynamic_candidate_throttle_boost") or 0.0) * 0.18
                + (0.06 if current_focus == "governance_hygiene" else 0.0)
                + (0.04 if last_mode == "conserve" else 0.0)
            ),
        }
        mode = max(mode_scores.items(), key=lambda item: item[1])[0]
        confidence = self._clamp_endogenous_ratio(max(mode_scores.values()))
        if confidence < 0.2:
            mode = "observe" if uncertainty_risk >= agenda_priority else "conserve"
        elif last_mode and last_mode == mode and last_mode_stats:
            confidence = self._clamp_endogenous_ratio(
                confidence + min(0.08, float(last_mode_stats.get("active_cycles") or 0) * 0.01)
            )

        guardrails = []
        if mode in {"observe", "correct"}:
            guardrails.append("prioritize evidence collection before expansion")
        if mode == "expand":
            guardrails.append("avoid expanding when uncertainty remains unresolved")
        if mode == "conserve":
            guardrails.append("limit new candidate volume until pressure decays")
        if corrective_mode.get("active"):
            guardrails.append("respect active self-regulation boosts")

        stability = "stable"
        if confidence >= 0.72:
            stability = "strong"
        elif confidence >= 0.45:
            stability = "moderate"
        elif confidence > 0.0:
            stability = "fragile"

        drivers = [
            f"agenda={dominant_agenda.get('topic') or 'none'}",
            f"uncertainty={dominant_uncertainty.get('domain') or 'none'}",
            f"observation={dominant_observation.get('target') or 'none'}",
            f"current_focus={current_focus or 'unknown'}",
            f"dominant_constraint={dominant_constraint or 'none'}",
            f"last_mode={last_mode or 'none'}",
        ]
        return {
            "mode": mode,
            "confidence": round(confidence, 4),
            "drivers": drivers,
            "guardrails": guardrails,
            "stability": stability,
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
        meta_mode = self._derive_endogenous_meta_governance_mode(
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

    def _derive_endogenous_agenda_persistence_state(
        self,
        topic_stats: Dict[str, Any],
    ) -> str:
        seen = max(0, int(topic_stats.get("seen") or 0))
        dragging = max(0, int(topic_stats.get("dragging") or 0))
        resolved = max(0, int(topic_stats.get("resolved") or 0))
        active_cycles = max(0, int(topic_stats.get("active_cycles") or 0))
        last_status = str(topic_stats.get("last_status") or "").strip().lower()

        if dragging >= 2 or (dragging >= 1 and active_cycles >= 3):
            return "dragging"
        if resolved >= 2 and resolved >= active_cycles:
            return "stabilizing"
        if seen >= 3 or active_cycles >= 3:
            return "persistent"
        if last_status == "resolved":
            return "cooling"
        return "emerging"

    def _build_endogenous_attention_agenda(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        strategy_memory: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        needs = [
            dict(item)
            for item in list(deliberation.get("needs") or [])
            if isinstance(item, dict)
        ]
        intents = [
            dict(item)
            for item in list(deliberation.get("intents") or [])
            if isinstance(item, dict)
        ]
        signals = [
            dict(item)
            for item in list(deliberation.get("signals") or [])
            if isinstance(item, dict)
        ]
        reflection = dict(deliberation.get("reflection") or {})
        adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
        preferred_focus = str(adaptive_policy.get("preferred_focus") or "").strip().lower()
        normalized_strategy_memory = self._normalize_endogenous_strategy_memory(strategy_memory)
        agenda_topic_stats = dict(normalized_strategy_memory.get("agenda_topic_stats") or {})

        perspective_map = {
            "stabilize_memory_continuity": "system_continuity",
            "repair_truthfulness": "user_alignment",
            "expand_learning_frontier": "self_growth",
            "prepare_body_growth": "self_growth",
            "clear_governance_backlog": "governance_hygiene",
            "observe_before_acting": "self_regulation",
        }
        channel_counts = {
            name: len(list(governance_channels.get(name) or []))
            for name in (
                "task_candidates",
                "observation_requests",
                "governance_review_requests",
                "truthfulness_alerts",
                "autonomy_alignment_requests",
            )
        }
        entries: list[Dict[str, Any]] = []

        for need in needs:
            need_type = str(need.get("need_type") or "").strip()
            if not need_type:
                continue
            matching_intent = next(
                (
                    intent for intent in intents
                    if need_type in set(intent.get("source_needs") or [])
                ),
                None,
            )
            matching_signal = next(
                (
                    signal for signal in signals
                    if need_type in set(signal.get("source_needs") or [])
                ),
                None,
            )
            agenda_priority = self._clamp_endogenous_ratio(
                float(need.get("severity") or 0.0) * 0.45
                + float(need.get("urgency") or 0.0) * 0.35
                + float(need.get("confidence") or 0.0) * 0.20
            )
            if need_type == "observe_before_acting":
                agenda_priority = self._clamp_endogenous_ratio(
                    agenda_priority
                    + float(adaptive_policy.get("observation_bias") or 0.0) * 0.18
                    + (0.12 if preferred_focus == "observation" else 0.0)
                    + (0.08 if reflection.get("dominant_constraint") not in {None, "", "none"} else 0.0)
                )
            observation_required = (
                need_type == "observe_before_acting"
                or str((matching_intent or {}).get("output_channel") or "").strip() == "drive_signal"
            )
            blocked_by = None
            if need_type in {"observe_before_acting", "clear_governance_backlog"}:
                blocked_by = reflection.get("dominant_constraint")
            elif need_type == "prepare_body_growth" and reflection.get("body_growth_blocked"):
                blocked_by = "body_growth_cooldown"
            topic_memory = dict(agenda_topic_stats.get(need_type) or {})
            persistence_state = self._derive_endogenous_agenda_persistence_state(topic_memory)
            trending = "steady"
            if persistence_state in {"persistent", "dragging"}:
                trending = "warming"
            elif persistence_state in {"stabilizing", "cooling"}:
                trending = "cooling"
            entries.append(
                {
                    "agenda_id": f"agenda:{need_type}",
                    "topic": need_type,
                    "perspective": perspective_map.get(need_type, "governance"),
                    "objective": (
                        (matching_intent or {}).get("intent_type")
                        or need_type
                    ),
                    "priority": round(agenda_priority, 4),
                    "urgency": round(float(need.get("urgency") or 0.0), 4),
                    "confidence": round(float(need.get("confidence") or 0.0), 4),
                    "target_horizon": (matching_intent or {}).get("target_horizon"),
                    "recommended_channel": (matching_intent or {}).get("output_channel"),
                    "supporting_signal": (matching_signal or {}).get("signal_type"),
                    "observation_required": observation_required,
                    "blocked_by": blocked_by,
                    "persistence_state": persistence_state,
                    "trend": trending,
                    "seen_count": max(0, int(topic_memory.get("seen") or 0)),
                    "active_cycles": max(0, int(topic_memory.get("active_cycles") or 0)),
                    "resolved_count": max(0, int(topic_memory.get("resolved") or 0)),
                    "dragging_count": max(0, int(topic_memory.get("dragging") or 0)),
                    "last_status": topic_memory.get("last_status"),
                    "why_now": need.get("rationale"),
                }
            )

        entries.sort(key=lambda item: item.get("priority") or 0.0, reverse=True)
        top_focus = str(adaptive_policy.get("preferred_focus") or "unknown").strip().lower() or "unknown"
        if entries:
            summary = (
                f"The endogenous core is prioritizing {entries[0]['topic']} while "
                f"holding {len(entries)} active agenda item(s) under {top_focus} focus; "
                f"top agenda persistence is {entries[0]['persistence_state']}."
            )
        else:
            summary = "The endogenous core has no active agenda items for the current cycle."
        return {
            "summary": summary,
            "active_count": len(entries),
            "preferred_focus": top_focus,
            "channel_counts": channel_counts,
            "entries": entries[:6],
        }

    def _build_endogenous_uncertainty_ledger(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        self_regulation: Dict[str, Any],
    ) -> Dict[str, Any]:
        perception = dict(deliberation.get("perception") or {})
        world_model = dict(deliberation.get("world_model") or {})
        reflection = dict(deliberation.get("reflection") or {})
        adaptive_policy = dict(deliberation.get("adaptive_policy") or {})
        corrective_mode = self._derive_endogenous_corrective_mode(self_regulation)
        entries: list[Dict[str, Any]] = []
        autonomy_alignment_requests = len(
            list(governance_channels.get("autonomy_alignment_requests") or [])
        )

        correction_signals = int(perception.get("correction_signals") or 0)
        if correction_signals > 0:
            risk = self._clamp_endogenous_ratio(
                float(world_model.get("truthfulness_pressure") or 0.0) * 0.55
                + min(correction_signals, 6) / 6.0 * 0.45
            )
            entries.append(
                {
                    "ledger_id": "uncertainty:truthfulness",
                    "domain": "truthfulness",
                    "risk": round(risk, 4),
                    "confidence": round(
                        self._clamp_endogenous_ratio(
                            0.56 + float(adaptive_policy.get("truthfulness_bias") or 0.0) * 0.24
                        ),
                        4,
                    ),
                    "hypothesis": (
                        "Recent correction pressure may reflect unresolved truthfulness debt rather than isolated noise."
                    ),
                    "why_uncertain": (
                        "The drive sees rising errors or high-uncertainty answers, but it still needs targeted review to confirm whether a stable truthfulness issue exists."
                    ),
                    "observation_target": "truthfulness",
                    "recommended_probe": "review recent uncertain answers and correction signals",
                    "evidence": [
                        f"correction_signals={correction_signals}",
                        f"recent_errors={int(perception.get('recent_errors') or 0)}",
                        f"uncertainty_count={int(perception.get('uncertainty_count') or 0)}",
                    ],
                }
            )

        api_b_judgement_pressure = float(
            reflection.get("api_b_judgement_blockage_pressure")
            if reflection.get("api_b_judgement_blockage_pressure") is not None
            else reflection.get("governance_backlog_blockage_pressure")
            or 0.0
        )
        if api_b_judgement_pressure >= 0.28 or str(world_model.get("governance_load_state") or "").strip() in {"busy", "strained"}:
            risk = self._clamp_endogenous_ratio(
                api_b_judgement_pressure * 0.7
                + (0.2 if str(world_model.get("governance_load_state") or "").strip() == "strained" else 0.08)
            )
            entries.append(
                {
                    "ledger_id": "uncertainty:api_b_judgement_blockage",
                    "domain": "api_b_judgement",
                    "risk": round(risk, 4),
                    "confidence": round(
                        self._clamp_endogenous_ratio(
                            0.58 + float(adaptive_policy.get("governance_hygiene_bias") or 0.0) * 0.18
                        ),
                        4,
                    ),
                    "hypothesis": (
                        "Additional autonomous output may worsen backlog drag before governance review debt is reduced."
                    ),
                    "why_uncertain": (
                        "Backlog pressure is visible, but the drive still needs to inspect whether the backlog is blocked by stale work, review debt, or repeated low-yield candidates."
                    ),
                    "observation_target": "api_b_judgement_blockage",
                    "recommended_probe": "inspect stale, deferred, and pending-review endogenous tasks",
                    "evidence": [
                        f"api_b_judgement_blockage_state={reflection.get('api_b_judgement_blockage_state') or reflection.get('governance_backlog_blockage_state')}",
                        f"api_b_judgement_count={int(perception.get('api_b_judgement_count') if perception.get('api_b_judgement_count') is not None else perception.get('governance_backlog_count') or 0)}",
                        f"pending_review_count={int(perception.get('pending_review_count') or 0)}",
                    ],
                }
            )

        learning_yield_state = str(reflection.get("learning_yield_state") or "").strip().lower()
        if learning_yield_state in {"cold", "mixed"} or str(reflection.get("dominant_constraint") or "") == "weak_learning_yield":
            risk = self._clamp_endogenous_ratio(
                max(0.0, 0.65 - float(reflection.get("autonomy_readiness") or 0.0)) * 0.6
                + (0.18 if learning_yield_state == "cold" else 0.08)
            )
            entries.append(
                {
                    "ledger_id": "uncertainty:learning_yield",
                    "domain": "learning_yield",
                    "risk": round(risk, 4),
                    "confidence": round(
                        self._clamp_endogenous_ratio(
                            0.48 + float(adaptive_policy.get("observation_bias") or 0.0) * 0.24
                        ),
                        4,
                    ),
                    "hypothesis": (
                        "Further learning expansion may create low-yield tasks before existing evidence is properly consolidated."
                    ),
                    "why_uncertain": (
                        "The drive can see mixed or weak learning signals, but it lacks enough evidence to know whether the problem is topic choice, backlog drag, or low follow-through."
                    ),
                    "observation_target": "learning_yield",
                    "recommended_probe": "compare recent learning quality against downstream task completion and review outcomes",
                    "evidence": [
                        f"learning_yield_state={learning_yield_state or 'unknown'}",
                        f"autonomy_readiness={round(float(reflection.get('autonomy_readiness') or 0.0), 4)}",
                        f"candidate_throttle={round(float(adaptive_policy.get('candidate_throttle') or 0.0), 4)}",
                    ],
                }
            )

        autonomy_readiness = float(reflection.get("autonomy_readiness") or 0.0)
        dominant_constraint = str(reflection.get("dominant_constraint") or "").strip().lower()
        if (
            autonomy_alignment_requests > 0
            or autonomy_readiness <= 0.45
            or dominant_constraint in {"weak_learning_yield", "historical_underdelivery", "api_b_judgement_blockage", "governance_backlog_blockage"}
        ):
            risk = self._clamp_endogenous_ratio(
                max(0.0, 0.58 - autonomy_readiness) * 0.75
                + float(adaptive_policy.get("observation_bias") or 0.0) * 0.18
                + autonomy_alignment_requests * 0.08
            )
            entries.append(
                {
                    "ledger_id": "uncertainty:autonomy_alignment",
                    "domain": "autonomy_alignment",
                    "risk": round(risk, 4),
                    "confidence": round(
                        self._clamp_endogenous_ratio(
                            0.52 + float(adaptive_policy.get("observation_bias") or 0.0) * 0.2
                        ),
                        4,
                    ),
                    "hypothesis": (
                        "The current autonomous posture may be expanding or planning faster than the system can responsibly validate."
                    ),
                    "why_uncertain": (
                        "Readiness and observation pressure suggest the core should verify alignment before treating more output as safe."
                    ),
                    "observation_target": dominant_constraint or "autonomy_alignment",
                    "recommended_probe": "inspect whether current posture should remain guarded or corrective on the next endogenous cycle",
                    "evidence": [
                        f"autonomy_readiness={round(autonomy_readiness, 4)}",
                        f"observation_bias={round(float(adaptive_policy.get('observation_bias') or 0.0), 4)}",
                        f"autonomy_alignment_requests={autonomy_alignment_requests}",
                        f"dominant_constraint={dominant_constraint or 'none'}",
                    ],
                }
            )

        if corrective_mode.get("active"):
            entries.append(
                {
                    "ledger_id": "uncertainty:self_regulation_decay",
                    "domain": "self_regulation",
                    "risk": round(
                        self._clamp_endogenous_ratio(
                            float(self_regulation.get("dynamic_candidate_throttle_boost") or 0.0) * 0.4
                            + float(self_regulation.get("dynamic_truthfulness_bias_boost") or 0.0) * 0.6
                        ),
                        4,
                    ),
                    "confidence": round(0.62, 4),
                    "hypothesis": (
                        "Temporary corrective mode may still be shaping governance posture even if the original trigger is fading."
                    ),
                    "why_uncertain": (
                        "Short-term boosts decay automatically, so the drive should verify whether the pressure is still real before treating guarded posture as the new normal."
                    ),
                    "observation_target": "self_regulation",
                    "recommended_probe": "re-evaluate whether corrective boosts are still justified after the next endogenous cycle",
                    "evidence": [
                        f"corrective_mode={corrective_mode.get('mode')}",
                        f"last_reason={corrective_mode.get('last_reason')}",
                    ],
                }
            )

        truthfulness_alerts = len(list(governance_channels.get("truthfulness_alerts") or []))
        if truthfulness_alerts > 0 and not any(item.get("domain") == "truthfulness" for item in entries):
            entries.append(
                {
                    "ledger_id": "uncertainty:latent_truthfulness",
                    "domain": "truthfulness",
                    "risk": round(self._clamp_endogenous_ratio(0.4 + truthfulness_alerts * 0.12), 4),
                    "confidence": round(0.54, 4),
                    "hypothesis": "Truthfulness alerts may indicate a latent evidence-quality problem.",
                    "why_uncertain": "The alerts are present, but the correction pattern has not yet been fully explained by the current snapshot.",
                    "observation_target": "truthfulness",
                    "recommended_probe": "inspect which observation requests escalated into truthfulness alerts",
                    "evidence": [f"truthfulness_alerts={truthfulness_alerts}"],
                }
            )

        entries.sort(key=lambda item: item.get("risk") or 0.0, reverse=True)
        highest_risk_domain = entries[0]["domain"] if entries else None
        summary = (
            f"The endogenous core is tracking {len(entries)} active uncertainty item(s); "
            f"highest current risk is {highest_risk_domain}."
            if entries
            else "The endogenous core sees no active uncertainty requiring explicit tracking right now."
        )
        return {
            "summary": summary,
            "active_count": len(entries),
            "highest_risk_domain": highest_risk_domain,
            "entries": entries[:6],
        }

    def _derive_endogenous_observation_persistence_state(
        self,
        target_stats: Dict[str, Any],
    ) -> str:
        recommended = max(0, int(target_stats.get("recommended") or 0))
        resolved = max(0, int(target_stats.get("resolved") or 0))
        stalled = max(0, int(target_stats.get("stalled") or 0))
        seen = max(0, int(target_stats.get("seen") or 0))
        last_status = str(target_stats.get("last_status") or "").strip().lower()

        if stalled >= 2 or (stalled >= 1 and recommended >= 3):
            return "stalled"
        if resolved >= 2 and resolved >= recommended:
            return "stabilizing"
        if recommended >= 3 or seen >= 3:
            return "persistent"
        if last_status == "resolved":
            return "cooling"
        return "emerging"

    def _build_endogenous_observation_program(
        self,
        *,
        uncertainty_ledger: Dict[str, Any],
        governance_channels: Dict[str, Any],
        strategy_memory: Optional[Dict[str, Any]],
        history: Dict[str, Any],
        context_key: str,
    ) -> Dict[str, Any]:
        normalized_strategy_memory = self._normalize_endogenous_strategy_memory(strategy_memory)
        observation_target_stats = dict(normalized_strategy_memory.get("observation_target_stats") or {})
        observation_requests = [
            dict(item)
            for item in list(governance_channels.get("observation_requests") or [])
            if isinstance(item, dict)
        ]
        entries_seed: list[Dict[str, Any]] = []

        requests_by_target: Dict[str, Dict[str, Any]] = {}
        for request in observation_requests:
            payload = dict(request.get("payload") or {})
            target = str(payload.get("observation_target") or "").strip().lower()
            if target and target not in requests_by_target:
                requests_by_target[target] = request

        for ledger_entry in list(uncertainty_ledger.get("entries") or []):
            if not isinstance(ledger_entry, dict):
                continue
            target = str(
                ledger_entry.get("observation_target")
                or ledger_entry.get("domain")
                or ""
            ).strip().lower()
            if not target:
                continue
            observation_request = dict(requests_by_target.get(target) or {})
            risk = self._clamp_endogenous_ratio(ledger_entry.get("risk") or 0.0)
            priority = self._clamp_endogenous_ratio(
                risk * 0.72
                + self._clamp_endogenous_ratio(ledger_entry.get("confidence") or 0.0) * 0.18
                + (0.08 if observation_request else 0.0)
            )
            evidence_items = list(ledger_entry.get("evidence") or [])
            recommended_probe = str(ledger_entry.get("recommended_probe") or "").strip()
            entries_seed.append(
                {
                    "program_id": f"observe:{target}",
                    "target": target,
                    "source_domain": ledger_entry.get("domain"),
                    "priority": round(priority, 4),
                    "risk": round(risk, 4),
                    "confidence": round(
                        self._clamp_endogenous_ratio(ledger_entry.get("confidence") or 0.0),
                        4,
                    ),
                    "recommended_probe": recommended_probe,
                    "evidence_goal": (
                        f"Reduce uncertainty around {target} by collecting direct evidence about: "
                        f"{recommended_probe}."
                        if recommended_probe
                        else f"Reduce uncertainty around {target}."
                    ),
                    "linked_request_signal": observation_request.get("signal_type"),
                    "request_message": observation_request.get("message"),
                    "supporting_evidence_count": len(evidence_items),
                }
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

        refreshed_strategy_memory = self._normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        refreshed_target_stats = dict(refreshed_strategy_memory.get("observation_target_stats") or {})
        entries: list[Dict[str, Any]] = []
        for entry in entries_seed:
            target = str(entry.get("target") or "").strip().lower()
            target_memory = dict(refreshed_target_stats.get(target) or {})
            persistence_state = self._derive_endogenous_observation_persistence_state(target_memory)
            entries.append(
                {
                    **entry,
                    "persistence_state": persistence_state,
                    "last_status": target_memory.get("last_status"),
                    "seen_count": max(0, int(target_memory.get("seen") or 0)),
                    "recommended_count": max(0, int(target_memory.get("recommended") or 0)),
                    "resolved_count": max(0, int(target_memory.get("resolved") or 0)),
                    "stalled_count": max(0, int(target_memory.get("stalled") or 0)),
                    "recommended_next_step": (
                        "collect_observation"
                        if float(entry.get("risk") or 0.0) >= 0.45
                        or persistence_state in {"persistent", "stalled"}
                        else "monitor"
                    ),
                }
            )

        entries.sort(key=lambda item: item.get("priority") or 0.0, reverse=True)
        summary = (
            f"The endogenous core has prepared {len(entries)} observation target(s); "
            f"highest priority target is {entries[0]['target']}."
            if entries
            else "The endogenous core does not currently require an explicit observation program."
        )
        highest_priority_target = entries[0]["target"] if entries else None

        return {
            "summary": summary,
            "active_count": len(entries),
            "highest_priority_target": highest_priority_target,
            "entries": entries[:6],
        }

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

    def _normalize_endogenous_strategy_memory(
        self,
        raw: Any,
    ) -> Dict[str, Any]:
        focus_stats: Dict[str, Dict[str, int]] = {}
        contextual_focus_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        agenda_topic_stats: Dict[str, Dict[str, Any]] = {}
        observation_target_stats: Dict[str, Dict[str, Any]] = {}
        meta_governance_stats: Dict[str, Dict[str, Any]] = {}
        source = dict(raw or {}) if isinstance(raw, dict) else {}
        raw_focus_stats = source.get("focus_stats")
        if isinstance(raw_focus_stats, dict):
            for focus, stats in raw_focus_stats.items():
                focus_name = str(focus or "").strip().lower()
                if not focus_name or not isinstance(stats, dict):
                    continue
                focus_stats[focus_name] = {
                    "judged": max(0, int(stats.get("judged") or 0)),
                    "completed": max(0, int(stats.get("completed") or 0)),
                    "failed": max(0, int(stats.get("failed") or 0)),
                    "dragging": max(0, int(stats.get("dragging") or 0)),
                }
        raw_contextual = source.get("contextual_focus_stats")
        if isinstance(raw_contextual, dict):
            for context_key, focus_map in raw_contextual.items():
                normalized_context = str(context_key or "").strip().lower()
                if not normalized_context or not isinstance(focus_map, dict):
                    continue
                context_bucket: Dict[str, Dict[str, int]] = {}
                for focus, stats in focus_map.items():
                    focus_name = str(focus or "").strip().lower()
                    if not focus_name or not isinstance(stats, dict):
                        continue
                    context_bucket[focus_name] = {
                        "judged": max(0, int(stats.get("judged") or 0)),
                        "completed": max(0, int(stats.get("completed") or 0)),
                        "failed": max(0, int(stats.get("failed") or 0)),
                        "dragging": max(0, int(stats.get("dragging") or 0)),
                    }
                if context_bucket:
                    contextual_focus_stats[normalized_context] = context_bucket
        raw_agenda_topic_stats = source.get("agenda_topic_stats")
        if isinstance(raw_agenda_topic_stats, dict):
            for topic, stats in raw_agenda_topic_stats.items():
                topic_name = str(topic or "").strip().lower()
                if not topic_name or not isinstance(stats, dict):
                    continue
                agenda_topic_stats[topic_name] = {
                    "seen": max(0, int(stats.get("seen") or 0)),
                    "active_cycles": max(0, int(stats.get("active_cycles") or 0)),
                    "resolved": max(0, int(stats.get("resolved") or 0)),
                    "dragging": max(0, int(stats.get("dragging") or 0)),
                    "last_priority": round(
                        self._clamp_endogenous_ratio(stats.get("last_priority") or 0.0),
                        4,
                    ),
                    "last_confidence": round(
                        self._clamp_endogenous_ratio(stats.get("last_confidence") or 0.0),
                        4,
                    ),
                    "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                    "last_seen_at": stats.get("last_seen_at"),
                    "last_resolved_at": stats.get("last_resolved_at"),
                    "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
                }
        raw_observation_target_stats = source.get("observation_target_stats")
        if isinstance(raw_observation_target_stats, dict):
            for target, stats in raw_observation_target_stats.items():
                target_name = str(target or "").strip().lower()
                if not target_name or not isinstance(stats, dict):
                    continue
                observation_target_stats[target_name] = {
                    "seen": max(0, int(stats.get("seen") or 0)),
                    "recommended": max(0, int(stats.get("recommended") or 0)),
                    "resolved": max(0, int(stats.get("resolved") or 0)),
                    "stalled": max(0, int(stats.get("stalled") or 0)),
                    "last_priority": round(
                        self._clamp_endogenous_ratio(stats.get("last_priority") or 0.0),
                        4,
                    ),
                    "last_risk": round(
                        self._clamp_endogenous_ratio(stats.get("last_risk") or 0.0),
                        4,
                    ),
                    "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                    "last_seen_at": stats.get("last_seen_at"),
                    "last_resolved_at": stats.get("last_resolved_at"),
                    "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
                }
        raw_meta_governance_stats = source.get("meta_governance_stats")
        if isinstance(raw_meta_governance_stats, dict):
            for mode, stats in raw_meta_governance_stats.items():
                mode_name = str(mode or "").strip().lower()
                if not mode_name or not isinstance(stats, dict):
                    continue
                meta_governance_stats[mode_name] = {
                    "seen": max(0, int(stats.get("seen") or 0)),
                    "active_cycles": max(0, int(stats.get("active_cycles") or 0)),
                    "resolved": max(0, int(stats.get("resolved") or 0)),
                    "stalled": max(0, int(stats.get("stalled") or 0)),
                    "last_priority": round(
                        self._clamp_endogenous_ratio(stats.get("last_priority") or 0.0),
                        4,
                    ),
                    "last_confidence": round(
                        self._clamp_endogenous_ratio(stats.get("last_confidence") or 0.0),
                        4,
                    ),
                    "last_status": str(stats.get("last_status") or "unknown").strip().lower() or "unknown",
                    "last_seen_at": stats.get("last_seen_at"),
                    "last_resolved_at": stats.get("last_resolved_at"),
                    "last_context_key": str(stats.get("last_context_key") or "").strip().lower() or None,
                }
        return {
            "focus_stats": focus_stats,
            "contextual_focus_stats": contextual_focus_stats,
            "agenda_topic_stats": agenda_topic_stats,
            "observation_target_stats": observation_target_stats,
            "meta_governance_stats": meta_governance_stats,
        }

    def _strategy_agenda_topic_bucket(
        self,
        history: Dict[str, Any],
        topic: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = self._normalize_endogenous_strategy_memory(
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
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
        strategy_memory = self._normalize_endogenous_strategy_memory(
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
        if normalized in {"approved", "deferred", "paused", "awaiting_review", "retry", "running"}:
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
        agenda_entries = self._build_endogenous_attention_agenda(
            deliberation=deliberation,
            governance_channels=self._governance_channels_from_deliberation(deliberation),
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
            "governance_task_type": self._task_governance_type(task),
            "task_family": self._task_runtime_family(task),
            "execution_kind": self._task_execution_kind(task),
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
            "governance_hygiene": "governance_backlog",
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
        if status in {"failed", "deferred", "awaiting_review"}:
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

    def _normalize_runtime_task_family(self, value: Optional[str]) -> str:
        return str(
            normalize_runtime_task_family(value, default="general_self_evolution")
        )

    def _normalize_runtime_task_type(self, value: Optional[str]) -> str:
        return str(normalize_runtime_task_type(value, default="self_evolution"))

    def _task_runtime_family(self, task: AutonomousChainTask) -> str:
        execution = dict(task.metadata.get("execution_request") or {})
        runtime_task_profile = derive_runtime_task_profile(
            task_type=task.task_type,
            governance_task_type=(
                execution.get("governance_task_type")
                or task.governance_task_type
                or task.metadata.get("governance_task_type")
            ),
            task_family=(
                execution.get("task_family")
                or task.task_family
                or task.metadata.get("task_family")
            ),
            execution_kind=(
                execution.get("execution_kind")
                or task.execution_kind
                or task.metadata.get("execution_kind")
            ),
            kind=execution.get("kind"),
            default_task_family="general_self_evolution",
        )
        return str(runtime_task_profile["task_family"] or "general_self_evolution")

    def _task_execution_kind(self, task: AutonomousChainTask) -> Optional[str]:
        execution = dict(task.metadata.get("execution_request") or {})
        explicit_execution_kind = (
            execution.get("execution_kind")
            or task.metadata.get("execution_kind")
            or task.execution_kind
        )
        normalized_explicit_kind = (
            self._normalize_runtime_task_family(explicit_execution_kind)
            if explicit_execution_kind
            else None
        )
        if normalized_explicit_kind == "body_upgrade":
            explicit_lower = str(explicit_execution_kind or "").strip().lower()
            if explicit_lower in {"body_improvement", "body_switch", "body_upgrade"}:
                return explicit_lower

        task_family = self._task_runtime_family(task)
        if task_family in {
            "memory_maintenance",
            "general_self_evolution",
            "body_upgrade",
        }:
            return task_family
        return normalized_explicit_kind

    def _task_runtime_profile(self, task: AutonomousChainTask) -> Dict[str, Any]:
        execution = dict(task.metadata.get("execution_request") or {})
        return derive_runtime_task_profile(
            task_type=task.task_type,
            governance_task_type=(
                execution.get("governance_task_type")
                or task.governance_task_type
                or task.metadata.get("governance_task_type")
            ),
            task_family=(
                execution.get("task_family")
                or task.task_family
                or task.metadata.get("task_family")
            ),
            execution_kind=(
                execution.get("execution_kind")
                or task.execution_kind
                or task.metadata.get("execution_kind")
            ),
            kind=execution.get("kind"),
            default_task_family="general_self_evolution",
        )

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
        metadata = self._normalize_task_schedule_metadata(metadata)
        explicit_execution_kind = str(metadata.get("execution_kind") or "").strip().lower()
        if explicit_execution_kind in {"body_switch", "body_improvement"} and not metadata.get("task_family"):
            metadata["task_family"] = explicit_execution_kind
        return metadata

    def _request_task_type(
        self,
        payload: Dict[str, Any],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        merged_metadata = dict(metadata or payload.get("metadata") or {})
        return resolve_broad_task_type(
            task_type=payload.get("task_type"),
            governance_task_type=merged_metadata.get("governance_task_type"),
            task_family=merged_metadata.get("task_family"),
            execution_kind=merged_metadata.get("execution_kind"),
            source=payload.get("source"),
        )

    def _drive_input_request_profile(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return derive_runtime_task_profile(
            governance_task_type=request.get("governance_task_type"),
            task_family=request.get("task_family"),
            execution_kind=request.get("execution_kind"),
            default_task_family="general_self_evolution",
        )

    def _task_governance_type(self, task: AutonomousChainTask) -> str:
        return str(self._task_runtime_profile(task)["governance_task_type"])

    def _task_requires_execution_request(self, task: AutonomousChainTask) -> bool:
        execution_kind = self._task_execution_kind(task)
        if execution_kind == "body_improvement":
            return False
        return self._task_governance_type(task) in {"self_evolution", "memory_maintenance"}

    def _normalize_scheduled_for_value(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).isoformat()
        except ValueError:
            return text

    def _normalize_task_schedule_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(metadata or {})
        scheduled_for = None
        for key in (
            "scheduled_for",
            "preset_time",
            "scheduled_at",
            "run_at",
            "execute_after",
            "time_slot",
            "window",
        ):
            scheduled_for = self._normalize_scheduled_for_value(normalized.get(key))
            if scheduled_for:
                break
        if scheduled_for:
            normalized["scheduled_for"] = scheduled_for
        return normalized

    def _task_schedule_token_from_sources(self, *sources: Any) -> Optional[str]:
        for source in sources:
            if not isinstance(source, dict):
                continue
            for key in (
                "scheduled_for",
                "preset_time",
                "scheduled_at",
                "run_at",
                "execute_after",
                "time_slot",
                "window",
            ):
                scheduled_for = self._normalize_scheduled_for_value(source.get(key))
                if scheduled_for:
                    return scheduled_for
        return None

    def _task_schedule_token(self, task: AutonomousChainTask) -> Optional[str]:
        execution = dict(task.metadata.get("execution_request") or {})
        evidence = dict(task.evidence or {})
        endogenous_drive = dict(evidence.get("endogenous_drive") or {})
        return self._task_schedule_token_from_sources(
            task.metadata,
            task.constraints,
            evidence,
            endogenous_drive,
            execution,
        )

    def _schedule_slot_interval_seconds(self) -> int:
        review_interval = int(
            getattr(self.config.service_runtime, "autonomous_chain_review_interval", 300) or 300
        )
        return max(300, review_interval)

    def _align_scheduled_for(self, when: datetime) -> datetime:
        slot_seconds = self._schedule_slot_interval_seconds()
        base = when.replace(second=0, microsecond=0)
        since_midnight = (
            base.hour * 3600
            + base.minute * 60
            + base.second
        )
        remainder = since_midnight % slot_seconds
        if remainder == 0:
            return base
        return base + timedelta(seconds=(slot_seconds - remainder))

    def _occupied_scheduled_for_tokens(self) -> set[str]:
        terminal = {"completed", "failed", "cancelled"}
        occupied: set[str] = set()
        for task in self._active_autonomous_chain_tasks():
            if str(task.status or "").strip().lower() in terminal:
                continue
            token = self._task_schedule_token(task)
            if token:
                occupied.add(token)
        return occupied

    def _allocate_scheduled_for_tokens(
        self,
        *,
        count: int,
        now: Optional[datetime] = None,
        occupied_tokens: Optional[set[str]] = None,
    ) -> list[str]:
        if count <= 0:
            return []

        current = now or datetime.now()
        occupied = set(occupied_tokens or set())
        scheduled: list[str] = []
        slot_seconds = self._schedule_slot_interval_seconds()

        cursor = self._align_scheduled_for(current)
        while len(scheduled) < count:
            cursor = self._align_scheduled_for(cursor)
            token = cursor.isoformat()
            if token not in occupied:
                occupied.add(token)
                scheduled.append(token)
            cursor = cursor + timedelta(seconds=slot_seconds)
        return scheduled

    def _apply_scheduled_for_to_candidate_items(
        self,
        candidate_items: list[Dict[str, Any]],
        *,
        now: Optional[datetime] = None,
    ) -> list[Dict[str, Any]]:
        if not candidate_items:
            return []

        occupied = self._occupied_scheduled_for_tokens()
        prepared: list[Dict[str, Any]] = []
        missing_indexes: list[int] = []

        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row_metadata = self._normalize_task_schedule_metadata(dict(row.get("metadata") or {}))
            row["metadata"] = row_metadata
            existing_token = self._task_schedule_token_from_sources(
                row,
                row_metadata,
                row.get("constraints"),
                row.get("evidence"),
            )
            if existing_token:
                if existing_token in occupied:
                    row_metadata["requested_scheduled_for"] = existing_token
                    row_metadata["schedule_token_reallocated"] = True
                    row.pop("scheduled_for", None)
                    row_metadata.pop("scheduled_for", None)
                    missing_indexes.append(len(prepared))
                else:
                    row["scheduled_for"] = existing_token
                    row_metadata["scheduled_for"] = existing_token
                    occupied.add(existing_token)
            else:
                missing_indexes.append(len(prepared))
            prepared.append(row)

        allocated = self._allocate_scheduled_for_tokens(
            count=len(missing_indexes),
            now=now,
            occupied_tokens=occupied,
        )
        for row_index, token in zip(missing_indexes, allocated):
            if row_index >= len(prepared):
                continue
            prepared[row_index]["scheduled_for"] = token
            prepared[row_index].setdefault("metadata", {})
            prepared[row_index]["metadata"]["scheduled_for"] = token
        return prepared

    def _task_sort_key(self, task: AutonomousChainTask) -> tuple[int, str, str]:
        status = str(task.status or "").strip().lower()
        order = {
            "running": 0,
            "approved": 1,
            "planned": 2,
            "deferred": 3,
            "paused": 4,
            "completed": 5,
            "failed": 6,
            "cancelled": 7,
        }
        created_at = getattr(task, "created_at", None)
        updated_at = getattr(task, "updated_at", None)
        created_text = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or "")
        updated_text = updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at or "")
        return (order.get(status, 99), created_text, updated_text)

    def _build_schedule_conflict_index(
        self,
        *,
        exclude_task_ids: Optional[set[str]] = None,
    ) -> Dict[str, AutonomousChainTask]:
        terminal = {"completed", "failed", "cancelled"}
        excluded = exclude_task_ids or set()
        conflicts: Dict[str, AutonomousChainTask] = {}
        for task in sorted(
            self._active_autonomous_chain_tasks(),
            key=self._task_sort_key,
        ):
            if task.task_id in excluded:
                continue
            if str(task.status or "").strip().lower() in terminal:
                continue
            schedule_token = self._task_schedule_token(task)
            if not schedule_token:
                continue
            conflicts.setdefault(schedule_token, task)
        return conflicts

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
        profile = self._task_runtime_profile(task)
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
        scheduled_for = self._task_schedule_token(task)
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
            {self._task_governance_type(task) for task in tasks}
        )
        metadata["task_families"] = sorted(
            {self._task_runtime_family(task) for task in tasks}
        )
        execution_kinds = sorted(
            {
                execution_kind
                for execution_kind in (self._task_execution_kind(task) for task in tasks)
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
            if self._task_runtime_family(task) != "self_learning":
                continue
            metadata = dict(task.metadata or {})
            completed_at = (
                metadata.get("completed_at")
                or getattr(task, "updated_at", None)
                or getattr(task, "created_at", None)
            )
            rows.append(
                (
                    str(completed_at or ""),
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "summary": task.summary,
                        "completed_at": completed_at,
                        "quality_score": metadata.get("quality_score"),
                        "endogenous_drive_key": metadata.get("endogenous_drive_key"),
                    },
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _is_api_a_execution_lane_task_record(self, task: AutonomousChainTask) -> bool:
        status = str(task.status or "").strip().lower()
        return self._is_agent_pull_task(task) and status in {"approved", "running", "retry"}

    def _autonomous_chain_task_summary_payload(
        self,
        task: AutonomousChainTask,
    ) -> Dict[str, Any]:
        return {
            "task_id": task.task_id,
            "title": task.title,
            "status": str(task.status),
            "governance_task_type": self._task_governance_type(task),
            "task_family": self._task_runtime_family(task),
            "execution_kind": self._task_execution_kind(task),
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
            if not self._is_agent_pull_task(task):
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
        normalized = self._normalize_runtime_task_type(task_type)
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

    def _parse_activity_timestamp(self, value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
            # Gateway activity timestamps are naive UTC; keep comparisons in
            # that same clock domain to avoid local-time skew.
            if parsed.tzinfo is not None:
                from datetime import timezone
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            return None

    def _idle_seconds_since(self, timestamp: Optional[datetime], *, now: datetime) -> Optional[float]:
        if timestamp is None:
            return None
        return max((now - timestamp).total_seconds(), 0.0)

    async def get_runtime_activity(self):
        snapshot = await self._fetch_gateway_activity_snapshot()
        return {
            "status": "ok",
            "gateway_address": self.config.execution.gateway_address,
            "activity": snapshot,
        }

    def _project_runtime_observation_input(
        self,
        payload: dict | None,
        *,
        snapshot_source: str = "live",
    ) -> dict:
        raw = dict(payload or {})
        activity = dict(raw.get("activity") or {})
        counts = dict(activity.get("counts") or {})
        recent_metadata = dict(activity.get("recent_metadata") or {})
        active_sessions_raw = raw.get("active_sessions")
        if active_sessions_raw is None:
            active_sessions_raw = activity.get("active_sessions")
        try:
            active_sessions = max(0, int(active_sessions_raw or 0))
        except (TypeError, ValueError):
            active_sessions = 0

        user_chain_signal = dict(raw.get("user_chain_signal") or {})
        quiet_after_raw = user_chain_signal.get("quiet_after_seconds")
        try:
            quiet_after_seconds = max(0, int(quiet_after_raw or 600))
        except (TypeError, ValueError):
            quiet_after_seconds = 600

        user_chain_signal["scope"] = (
            str(user_chain_signal.get("scope") or "soft_signal_only").strip()
            or "soft_signal_only"
        )
        user_chain_signal["active_sessions"] = active_sessions
        user_chain_signal["quiet_after_seconds"] = quiet_after_seconds
        if "is_quiet" not in user_chain_signal:
            user_chain_signal["is_quiet"] = active_sessions <= 0

        activity["active_sessions"] = active_sessions
        activity["counts"] = counts
        activity["recent_metadata"] = recent_metadata

        return {
            "activity": activity,
            "user_chain_signal": user_chain_signal,
            "snapshot_source": str(snapshot_source or "live").strip() or "live",
        }

    async def get_runtime_observation_input(self):
        payload = await self.evaluate_drive_input({})
        observation_input = self._project_runtime_observation_input(
            payload,
            snapshot_source="live",
        )
        return {
            "status": "ok",
            "gateway_address": self.config.execution.gateway_address,
            "observation_input": observation_input,
        }

    async def evaluate_drive_input(self, request: dict | None = None):
        request = request or {}
        snapshot = await self._fetch_gateway_activity_snapshot()

        now_override = request.get("now")
        if isinstance(now_override, str):
            try:
                now = datetime.fromisoformat(now_override)
                if now.tzinfo is not None:
                    from datetime import timezone
                    now = now.astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid now override: {exc}")
        else:
            now = datetime.utcnow()

        service_cfg = self.config.service_runtime
        user_idle_threshold = int(
            request.get(
                "user_idle_seconds",
                getattr(service_cfg, "activity_guard_user_seconds", 600),
            )
        )
        memory_idle_threshold = int(
            request.get(
                "memory_idle_seconds",
                getattr(service_cfg, "activity_guard_memory_seconds", 600),
            )
        )
        workflow_idle_threshold = int(
            request.get(
                "workflow_idle_seconds",
                getattr(service_cfg, "activity_guard_workflow_seconds", 600),
            )
        )
        requested_task_profile = self._drive_input_request_profile(request)
        requested_governance_task_type = str(requested_task_profile["governance_task_type"])
        requested_task_family = str(requested_task_profile["task_family"])

        last_user_request_at = self._parse_activity_timestamp(snapshot.get("last_user_request_at"))
        last_memory_task_at = self._parse_activity_timestamp(snapshot.get("last_memory_task_at"))
        last_self_learning_activity_at = self._parse_activity_timestamp(
            snapshot.get("last_self_learning_activity_at")
        )
        last_autonomous_chain_plan_at = self._parse_activity_timestamp(
            snapshot.get("last_autonomous_chain_plan_at")
        )
        last_autonomous_chain_execute_at = self._parse_activity_timestamp(
            snapshot.get("last_autonomous_chain_execute_at")
        )
        last_autonomous_chain_activity_at = self._parse_activity_timestamp(
            snapshot.get("last_autonomous_chain_activity_at")
        )
        active_cli_executor = dict(snapshot.get("active_cli_executor") or {})
        active_cli_lane = str(active_cli_executor.get("agent_lane") or "").strip().lower()
        active_cli_lease_status = str(active_cli_executor.get("lease_status") or "").strip().lower()
        active_cli_execution_idle_seconds: Optional[float] = None
        if active_cli_lane == "supervisor_task":
            try:
                active_cli_execution_idle_seconds = max(
                    0.0,
                    float(active_cli_executor.get("idle_seconds") or 0.0),
                )
            except (TypeError, ValueError):
                active_cli_execution_idle_seconds = 0.0
        active_cli_execution_is_stale = (
            not active_cli_executor
            or active_cli_lane != "supervisor_task"
            or bool(active_cli_executor.get("is_stale"))
            or active_cli_lease_status == "stale"
        )

        user_idle_seconds = self._idle_seconds_since(last_user_request_at, now=now)
        memory_idle_seconds = self._idle_seconds_since(last_memory_task_at, now=now)
        self_learning_idle_seconds = self._idle_seconds_since(last_self_learning_activity_at, now=now)
        autonomous_chain_plan_idle_seconds = self._idle_seconds_since(
            last_autonomous_chain_plan_at,
            now=now,
        )
        autonomous_chain_execute_idle_seconds = self._idle_seconds_since(
            last_autonomous_chain_execute_at,
            now=now,
        )
        autonomous_chain_idle_seconds = self._idle_seconds_since(last_autonomous_chain_activity_at, now=now)
        autonomous_execution_idle_candidates = [
            value
            for value in (
                autonomous_chain_execute_idle_seconds,
                active_cli_execution_idle_seconds,
            )
            if value is not None
        ]
        autonomous_execution_idle_seconds = (
            min(autonomous_execution_idle_candidates)
            if autonomous_execution_idle_candidates
            else None
        )

        # ── correction_signals for truthfulness drive ──
        # Source of truth: Gateway activity_state (architectural baseline §4.2
        # — gateway is the activity fact source).  Counts are best-effort —
        # a missing field defaults to 0 so the candidate simply does not fire
        # when no error/uncertainty has been reported in the current session.
        counts = dict(snapshot.get("counts") or {})
        raw_error_count = snapshot.get("error_count")
        if raw_error_count is None:
            raw_error_count = counts.get("error_count") or counts.get("recent_errors")
        raw_uncertainty_count = snapshot.get("uncertainty_high_count")
        if raw_uncertainty_count is None:
            raw_uncertainty_count = counts.get("uncertainty_high_count") or counts.get("high_uncertainty")
        try:
            error_count = int(raw_error_count) if raw_error_count is not None else 0
        except (TypeError, ValueError):
            error_count = 0
        try:
            uncertainty_count = int(raw_uncertainty_count) if raw_uncertainty_count is not None else 0
        except (TypeError, ValueError):
            uncertainty_count = 0
        # Decay: a half-life of 4 hours reduces the count toward 0 unless new
        # signals keep arriving. This keeps truthfulness candidates from being
        # permanently produced by one old error long after the system has
        # self-corrected. We use recent user-chain quiet time as a coarse proxy
        # for "how long has the system been calm" — when the user chain has
        # been quiet for a long time, an old error should weigh less, since a
        # working session would have produced new activity. This is a
        # best-effort heuristic (Gateway does not expose per-signal timestamps)
        # and matches the architectural baseline §4.2 "activity facts come
        # from gateway" without requiring a new field.
        if user_idle_seconds is None:
            user_idle_hours = 24.0
        else:
            user_idle_hours = min(user_idle_seconds / 3600.0, 24.0)
        decay_factor = max(0.0, 1.0 - user_idle_hours / 4.0)
        correction_signals = int(round((error_count + uncertainty_count) * decay_factor))

        user_chain_quiet = (
            user_idle_seconds is None
            or user_idle_seconds >= user_idle_threshold
        )
        has_memory_idle = memory_idle_seconds is None or memory_idle_seconds >= memory_idle_threshold
        has_api_a_execution_idle = (
            active_cli_execution_is_stale
            and (
                autonomous_execution_idle_seconds is None
                or autonomous_execution_idle_seconds >= workflow_idle_threshold
            )
        )
        has_self_learning_idle = (
            self_learning_idle_seconds is None
            or self_learning_idle_seconds >= workflow_idle_threshold
        )
        has_autonomous_chain_plan_idle = (
            autonomous_chain_plan_idle_seconds is None
            or autonomous_chain_plan_idle_seconds >= workflow_idle_threshold
        )
        has_autonomous_chain_execute_idle = (
            autonomous_chain_execute_idle_seconds is None
            or autonomous_chain_execute_idle_seconds >= workflow_idle_threshold
        )
        has_autonomous_chain_idle = (
            autonomous_chain_idle_seconds is None
            or autonomous_chain_idle_seconds >= workflow_idle_threshold
        )

        # Whole-day automatic execution (baseline §6): the time-of-day
        # execution window and the "wait for the user to be idle" gate have
        # been removed. Supervisor autonomous-chain review runs on isolated subagents
        # editing shell-slot code, so it does not disturb the user's CLI.
        # User activity is now a SOFT signal (observability + cognition input)
        # rather than a hard gate. The remaining has_*_idle checks below are
        # anti-self-collision concurrency guards — they are NOT "wait for the
        # user" gates.
        active_sessions = int(snapshot.get("active_sessions") or 0)
        user_chain_signal = {
            "scope": "soft_signal_only",
            "active_sessions": active_sessions,
            "is_quiet": bool(user_chain_quiet and active_sessions <= 0),
            "recent_user_idle_seconds": user_idle_seconds,
            "quiet_after_seconds": user_idle_threshold,
        }
        governance_task_type_decisions = {
            "user": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
            "self_learning": {
                "eligible_for_planning": True,
                "eligible_for_execution": (
                    has_api_a_execution_idle
                    and has_memory_idle
                    and has_self_learning_idle
                    and has_autonomous_chain_plan_idle
                ),
            },
            "memory_maintenance": {
                "eligible_for_planning": True,
                "eligible_for_execution": has_memory_idle,
            },
            "self_evolution": {
                "eligible_for_planning": (
                    has_autonomous_chain_plan_idle
                ),
                "eligible_for_execution": (
                    has_api_a_execution_idle
                    and has_memory_idle
                    and has_autonomous_chain_plan_idle
                    and has_autonomous_chain_execute_idle
                ),
            },
        }
        task_family_decisions = {
            "user": dict(governance_task_type_decisions["user"]),
            "self_learning": dict(governance_task_type_decisions["self_learning"]),
            "memory_maintenance": dict(governance_task_type_decisions["memory_maintenance"]),
            "general_self_evolution": dict(governance_task_type_decisions["self_evolution"]),
            "body_upgrade": dict(governance_task_type_decisions["self_evolution"]),
            "body_switch": dict(governance_task_type_decisions["self_evolution"]),
        }
        selected_task_decisions = task_family_decisions[requested_task_family]

        autonomous_chain_gate_active = bool(
            request.get("autonomous_chain_gate_active")
            or getattr(getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False)
        )
        if autonomous_chain_gate_active:
            # With the autonomous-chain gate active, self_learning and
            # memory_maintenance planning is no longer blocked on user-idle
            # style signals. Execution still follows its own runtime decision.
            governance_task_type_decisions["self_learning"]["eligible_for_planning"] = True
            governance_task_type_decisions["memory_maintenance"]["eligible_for_planning"] = True
            task_family_decisions["self_learning"]["eligible_for_planning"] = True
            task_family_decisions["memory_maintenance"]["eligible_for_planning"] = True

        return {
            "status": "evaluated",
            "evaluated_at": now.isoformat(),
            "gateway_address": self.config.execution.gateway_address,
            "governance_task_type": requested_governance_task_type,
            "task_family": requested_task_family,
            "execution_kind": requested_task_profile.get("execution_kind"),
            "task_profile": requested_task_profile,
            "activity": snapshot,
            "shell_slot": self._current_shell_slot_context(),
            "completed_learning_tasks": self._completed_learning_task_summaries(),
            "correction_signals": correction_signals,
            "error_count": error_count,
            "uncertainty_high_count": uncertainty_count,
            "correction_signal_decay": {
                "factor": round(decay_factor, 4),
                "user_idle_hours": round(user_idle_hours, 2),
                "half_life_hours": 4.0,
            },
            "idle_seconds": {
                "user": user_idle_seconds,
                "api_a_execution": autonomous_execution_idle_seconds,
                "memory": memory_idle_seconds,
                "self_learning": self_learning_idle_seconds,
                "autonomous_chain_plan": autonomous_chain_plan_idle_seconds,
                "autonomous_chain_execute": autonomous_chain_execute_idle_seconds,
                "autonomous_chain": autonomous_chain_idle_seconds,
            },
            "thresholds": {
                "user_idle_seconds": user_idle_threshold,
                "memory_idle_seconds": memory_idle_threshold,
                "workflow_idle_seconds": workflow_idle_threshold,
                "cli_lease_stale_after_seconds": (
                    dict(snapshot.get("active_cli_executor") or {}).get("stale_after_seconds")
                ),
            },
            "user_chain_signal": user_chain_signal,
            "checks": {
                "has_memory_idle": has_memory_idle,
                "has_api_a_execution_idle": has_api_a_execution_idle,
                "has_self_learning_idle": has_self_learning_idle,
                "has_autonomous_chain_plan_idle": has_autonomous_chain_plan_idle,
                "has_autonomous_chain_execute_idle": has_autonomous_chain_execute_idle,
                "has_autonomous_chain_idle": has_autonomous_chain_idle,
            },
            "governance_task_type_decisions": governance_task_type_decisions,
            "task_family_decisions": task_family_decisions,
            "autonomous_chain_gate_active": autonomous_chain_gate_active,
            "decisions": {
                "eligible_for_planning": selected_task_decisions["eligible_for_planning"],
                "eligible_for_execution": selected_task_decisions["eligible_for_execution"],
            },
        }

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
        """Evaluate the endogenous cognition state and governance-backlog projections."""

        request = request or {}
        record_activity = bool(request.get("record_activity", True))
        persist_evaluation = bool(request.get("persist_evaluation", True))
        drive_input = await self._resolve_runtime_drive_input_request(
            request,
            include_gate_default=True,
        )
        persisted_self_regulation = self._load_endogenous_self_regulation()
        api_b_judgement_tasks = self._api_b_judgement_task_summaries(limit=24)
        api_a_execution_lane_tasks = self._api_a_execution_lane_task_summaries(limit=24)
        drive_input["api_b_judgement_tasks"] = api_b_judgement_tasks
        drive_input["api_a_execution_lane_tasks"] = api_a_execution_lane_tasks
        drive_input["autonomous_chain_live_tasks"] = [
            *api_b_judgement_tasks,
            *api_a_execution_lane_tasks,
        ]
        drive_input["endogenous_drive_policy"] = {
            "learning_topic_cooldown_hours": int(
                getattr(
                    self.config.service_runtime,
                    "endogenous_drive_learning_topic_cooldown_hours",
                    24,
                ) or 24
            ),
            "body_improvement_cooldown_hours": int(
                getattr(
                    self.config.service_runtime,
                    "endogenous_drive_body_improvement_cooldown_hours",
                    12,
                ) or 12
            ),
            "topic_overlap_threshold": float(
                getattr(
                    self.config.service_runtime,
                    "endogenous_drive_topic_overlap_threshold",
                    0.6,
                ) or 0.6
            ),
            "body_improvement_min_quality": float(
                getattr(
                    self.config.service_runtime,
                    "body_improvement_min_quality",
                    60.0,
                ) or 60.0
            ),
            "body_improvement_editable_dirs": list(
                getattr(
                    self.config.service_runtime,
                    "body_improvement_editable_dirs",
                    ["skills/", "tools/", "agent/", "prompts/"],
                ) or ["skills/", "tools/", "agent/", "prompts/"]
            ),
            "body_improvement_forbidden_patterns": list(
                getattr(
                    self.config.service_runtime,
                    "body_improvement_forbidden_patterns",
                    ["**/credential*", "**/.env*", "systems/**"],
                ) or ["**/credential*", "**/.env*", "systems/**"]
            ),
            "body_improvement_max_files": int(
                getattr(
                    self.config.service_runtime,
                    "body_improvement_max_files",
                    5,
                ) or 5
            ),
        }
        drive_input["drive_history"] = self._history_for_endogenous_drive(
            self._load_endogenous_drive_history()
        )
        self_regulation = dict(persisted_self_regulation)
        for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        ):
            drive_input["endogenous_drive_policy"][key] = float(
                self_regulation.get(key) or 0.0
            )
        max_candidates = int(
            request.get(
                "max_candidates",
                self.config.service_runtime.endogenous_drive_max_candidates,
            )
        )

        def _candidate_backlog_items(candidates: list[Any]) -> list[Dict[str, Any]]:
            return self._apply_scheduled_for_to_candidate_items(
                [candidate.to_backlog_item() for candidate in candidates],
            )

        def _lm_proposals_for_second_candidate_pass() -> Optional[list[Dict[str, Any]]]:
            runtime_config = getattr(self.config, "service_runtime", None)
            if not bool(getattr(runtime_config, "endogenous_drive_lm_task_generation_enabled", False)):
                return None
            engine = getattr(self, "_endogenous_drive_engine", None)
            if engine is None or not hasattr(engine, "get_latest_lm_task_generation_context"):
                return None
            try:
                state = dict(engine.get_latest_lm_task_generation_context() or {})
            except Exception:
                return None
            if not str(state.get("status") or "").strip():
                return None
            if not hasattr(engine, "get_latest_lm_task_generation_proposals"):
                return []
            try:
                return [
                    dict(item)
                    for item in list(engine.get_latest_lm_task_generation_proposals() or [])
                    if isinstance(item, dict)
                ]
            except Exception:
                return []

        deliberation = self._endogenous_drive_engine.build_deliberation_report(
            drive_input=drive_input,
        )
        deliberation_dict = deliberation.to_dict()
        candidates = self._endogenous_drive_engine.generate_candidates(
            drive_input=drive_input,
            existing_drive_keys=self._existing_endogenous_drive_keys(),
            max_candidates=max_candidates,
            deliberation_report=deliberation,
        )
        candidate_items = _candidate_backlog_items(candidates)
        lm_reasoning_state = self._lm_reasoning_state_for_current_cycle()
        cognitive_self_regulation = self._derive_cognitive_self_regulation(
            drive_history=drive_input["drive_history"],
            lm_reasoning_state=lm_reasoning_state,
            deliberation=deliberation_dict,
        )
        cognitive_self_regulation = self._release_cleared_historical_observation_carryover(
            persisted_self_regulation=self_regulation,
            cognitive_self_regulation=cognitive_self_regulation,
            deliberation=deliberation_dict,
            lm_reasoning_state=lm_reasoning_state,
            drive_history=drive_input["drive_history"],
        )
        combined_self_regulation = dict(self_regulation)
        for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        ):
            combined_self_regulation[key] = round(
                min(
                    1.0,
                    float(self_regulation.get(key) or 0.0)
                    + float(cognitive_self_regulation.get(key) or 0.0),
                ),
                4,
            )
        combined_reason_parts = [
            str(self_regulation.get("last_reason") or "").strip(),
            str(cognitive_self_regulation.get("last_reason") or "").strip(),
        ]
        combined_self_regulation["last_reason"] = "; ".join(
            [item for item in combined_reason_parts if item]
        ) or None

        for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        ):
            drive_input["endogenous_drive_policy"][key] = float(
                combined_self_regulation.get(key) or 0.0
            )
        if any(float(cognitive_self_regulation.get(key) or 0.0) > 0.0 for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        )):
            deliberation = self._endogenous_drive_engine.build_deliberation_report(
                drive_input=drive_input,
            )
            deliberation_dict = deliberation.to_dict()
            candidates = self._endogenous_drive_engine.generate_candidates(
                drive_input=drive_input,
                existing_drive_keys=self._existing_endogenous_drive_keys(),
                max_candidates=max_candidates,
                deliberation_report=deliberation,
                lm_proposals_override=_lm_proposals_for_second_candidate_pass(),
            )
            candidate_items = _candidate_backlog_items(candidates)

        governance_channels = self._governance_channels_from_deliberation(
            deliberation_dict
        )
        if persist_evaluation:
            persisted_evaluation = self._persist_endogenous_evaluation_for_candidates(
                deliberation=deliberation_dict,
                drive_input=drive_input,
                governance_channels=governance_channels,
                self_regulation=combined_self_regulation,
                candidate_items=candidate_items,
                lm_reasoning_state=lm_reasoning_state,
            )
            candidate_items = list(persisted_evaluation["candidate_items"])
            governance_event_stream = dict(persisted_evaluation["governance_event_stream"])
            cognition_state = dict(persisted_evaluation["cognition_state"])
        else:
            governance_event_stream = self._governance_events_for_runtime(
                self._load_endogenous_governance_events()
            )
            cognition_state = self._build_endogenous_cognition_state(
                deliberation=deliberation_dict,
                governance_channels=governance_channels,
                governance_event_stream=governance_event_stream,
                self_regulation=combined_self_regulation,
                candidate_items=candidate_items,
                lm_reasoning_state=lm_reasoning_state,
            )
        if record_activity:
            self._record_supervisor_ui_activity(
                "endogenous_drive_evaluated",
                scene="planning",
                summary=f"内生驱动已完成一轮认知评估，并形成了 {len(candidates)} 个候选判断投影。",
                metadata={
                    "count": len(candidates),
                    "candidate_keys": [candidate.stable_key for candidate in candidates],
                    "candidates": [dict(item) for item in candidate_items],
                    "deliberation": deliberation_dict,
                "cognition_state": cognition_state,
                },
            )
        response_fields = self._build_drive_input_response_fields(
            drive_input=drive_input,
        )
        return {
            "status": "evaluated",
            "enabled": self.config.service_runtime.endogenous_drive_enabled,
            "core_values": CORE_VALUES,
            **response_fields,
            "deliberation": deliberation_dict,
            "candidates": candidate_items,
            "count": len(candidates),
            "drive_posture": self._drive_posture_signal_from_deliberation(
                deliberation_dict
            ),
            "governance_channels": governance_channels,
            "governance_event_stream": governance_event_stream,
            "self_regulation": combined_self_regulation,
            "cognitive_self_regulation": cognitive_self_regulation,
            "cognition_state": cognition_state,
        }

    async def get_endogenous_governance_events(self) -> Dict[str, Any]:
        snapshot = self._load_endogenous_governance_events()
        return {
            "status": "ok",
            "updated_at": snapshot.get("updated_at"),
            "governance_event_stream": self._governance_events_for_runtime(snapshot),
        }

    async def get_endogenous_self_regulation(self) -> Dict[str, Any]:
        regulation = self._load_endogenous_self_regulation()
        return {
            "status": "ok",
            "updated_at": regulation.get("updated_at"),
            "self_regulation": regulation,
            "corrective_mode": self._derive_endogenous_corrective_mode(regulation),
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
            "governance_event_stream": self._governance_events_for_runtime(event_snapshot),
            "self_regulation": regulation,
            "corrective_mode": self._derive_endogenous_corrective_mode(regulation),
            "strategy_memory": self._normalize_endogenous_strategy_memory(
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
                return self._governance_events_for_runtime(snapshot)
            snapshot["events"] = new_events + list(snapshot.get("events") or [])
            self._persist_endogenous_governance_events(snapshot)
        return self._governance_events_for_runtime(snapshot)

    def _gate_endogenous_candidates_by_posture(
        self,
        *,
        candidate_items: list[Dict[str, Any]],
        drive_posture: Dict[str, Any],
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        if not candidate_items:
            return [], []

        posture_payload = dict(drive_posture.get("payload") or {})
        preferred_focus = str(posture_payload.get("preferred_focus") or "").strip().lower()
        candidate_budget = int(posture_payload.get("candidate_budget") or 0)
        observation_mode = preferred_focus == "observation"

        if not observation_mode:
            return list(candidate_items), []

        allowed_candidate_kinds = {
            "truthfulness_review",
            "governance_hygiene_review",
        }
        kept: list[Dict[str, Any]] = []
        deferred: list[Dict[str, Any]] = []

        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            metadata = dict(row.get("metadata") or {})
            score_breakdown = dict(metadata.get("score_breakdown") or {})
            candidate_kind = str(score_breakdown.get("candidate_kind") or "").strip().lower()
            if candidate_kind in allowed_candidate_kinds:
                kept.append(row)
                continue

            row_metadata = dict(metadata)
            row_metadata["deferred_by_drive_posture"] = True
            row_metadata["deferred_drive_posture_focus"] = preferred_focus
            row["metadata"] = row_metadata
            deferred.append(
                {
                    "title": row.get("title"),
                    "stable_key": row.get("stable_key"),
                    "candidate_kind": candidate_kind,
                    "reason": (
                        "Deferred before governance-backlog insertion because the endogenous drive "
                        "selected observation posture and this candidate is not a "
                        "stability-oriented governance action."
                    ),
                }
            )

        if candidate_budget > 0 and len(kept) > candidate_budget:
            trimmed = kept[candidate_budget:]
            kept = kept[:candidate_budget]
            for row in trimmed:
                metadata = dict(row.get("metadata") or {})
                score_breakdown = dict(metadata.get("score_breakdown") or {})
                deferred.append(
                    {
                        "title": row.get("title"),
                        "stable_key": row.get("stable_key"),
                        "candidate_kind": str(score_breakdown.get("candidate_kind") or "").strip().lower(),
                        "reason": (
                            "Deferred before governance-backlog insertion because observation posture "
                            f"limits endogenous backlog growth to budget {candidate_budget}."
                        ),
                    }
                )

        return kept, deferred

    async def _run_endogenous_drive_cycle(self) -> Dict[str, Any]:
        if not self.config.service_runtime.endogenous_drive_enabled:
            return {"status": "disabled", "planned": 0, "tasks": []}

        evaluation = await self.evaluate_endogenous_drive(
            {"record_activity": False, "persist_evaluation": False}
        )
        drive_posture = dict(evaluation.get("drive_posture") or {})
        governance_channels = dict(evaluation.get("governance_channels") or {})
        governance_event_stream = dict(evaluation.get("governance_event_stream") or {})
        raw_candidate_items = [
            candidate
            for candidate in evaluation.get("candidates", [])
            if isinstance(candidate, dict)
        ]
        candidate_items, deferred_candidates = self._gate_endogenous_candidates_by_posture(
            candidate_items=raw_candidate_items,
            drive_posture=drive_posture,
        )
        if not candidate_items:
            self._record_supervisor_ui_activity(
                "endogenous_drive_idle",
                scene="idle",
                summary="内生驱动本轮未形成新的 API-B 判断在途投影。",
                metadata={
                    "drive_posture": drive_posture,
                    "governance_channels": governance_channels,
                    "governance_event_stream": governance_event_stream,
                    "deferred_candidates": deferred_candidates,
                } if drive_posture else None,
            )
            response_fields = self._drive_input_fields_from_evaluation(evaluation)
            return {
                "status": "idle",
                "planned": 0,
                "tasks": [],
                **response_fields,
                "drive_posture": drive_posture,
                "governance_channels": governance_channels,
                "governance_event_stream": governance_event_stream,
                "deferred_candidates": deferred_candidates,
            }

        evaluation_fields = self._drive_input_fields_from_evaluation(evaluation)
        persistence_history_snapshot = self._load_endogenous_drive_history()
        persistence_governance_snapshot = self._load_endogenous_governance_events()
        persistence_cognition_snapshot = self._load_endogenous_cognition_state()
        persisted_evaluation = self._persist_endogenous_evaluation_for_candidates(
            deliberation=dict(evaluation.get("deliberation") or {}),
            drive_input=dict(evaluation_fields.get("drive_input") or {}),
            governance_channels=governance_channels,
            self_regulation=dict(evaluation.get("self_regulation") or {}),
            candidate_items=candidate_items,
            lm_reasoning_state=self._lm_reasoning_state_for_current_cycle(),
        )
        candidate_items = list(persisted_evaluation["candidate_items"])
        governance_event_stream = dict(persisted_evaluation["governance_event_stream"])

        try:
            plan_result = await self.plan_autonomous_chain_task({"items": candidate_items})
        except Exception:
            self._restore_endogenous_evaluation_snapshots(
                drive_history=persistence_history_snapshot,
                governance_events=persistence_governance_snapshot,
                cognition_state=persistence_cognition_snapshot,
            )
            raise
        created_tasks = plan_result.get("tasks", [])
        if not created_tasks:
            self._restore_endogenous_evaluation_snapshots(
                drive_history=persistence_history_snapshot,
                governance_events=persistence_governance_snapshot,
                cognition_state=persistence_cognition_snapshot,
            )
        if created_tasks:
            self._record_supervisor_ui_activity(
                "endogenous_drive_planned",
                scene="planning",
                summary=f"内生驱动新增了 {len(created_tasks)} 个 API-B 判断在途链路项投影。",
                metadata={
                    "drive_posture": drive_posture,
                    "governance_channels": governance_channels,
                    "governance_event_stream": governance_event_stream,
                    "deferred_candidates": deferred_candidates,
                    "task_ids": [task.get("task_id") for task in created_tasks],
                    "tasks": [dict(task) for task in created_tasks if isinstance(task, dict)],
                    "endogenous_drive_keys": [
                        task.get("metadata", {}).get("endogenous_drive_key")
                        for task in created_tasks
                    ],
                },
            )
            await self._touch_gateway_activity(
                "autonomous_chain_plan",
                metadata={
                    "action": "endogenous_drive",
                    "count": len(created_tasks),
                    "endogenous_drive_keys": [
                        task.get("metadata", {}).get("endogenous_drive_key")
                        for task in created_tasks
                    ],
                },
            )

        return {
            "status": "planned",
            "planned": len(created_tasks),
            "tasks": created_tasks,
            **evaluation_fields,
            "drive_posture": drive_posture,
            "governance_channels": governance_channels,
            "governance_event_stream": governance_event_stream,
            "deferred_candidates": deferred_candidates,
        }

    def _normalize_autonomous_chain_decision(self, decision: Optional[str]) -> Optional[str]:
        if decision is None:
            return None
        normalized = decision.strip().lower()
        mapping = {
            "planned": "planned",
            "approve": "approved",
            "approved": "approved",
            "defer": "deferred",
            "deferred": "deferred",
            "fail": "failed",
            "failed": "failed",
            "pause": "paused",
            "paused": "paused",
            "cancel": "cancelled",
            "cancelled": "cancelled",
            "run": "running",
            "running": "running",
            "complete": "completed",
            "completed": "completed",
            "auto": "auto",
        }
        return mapping.get(normalized)

    def _build_autonomous_chain_auto_decision(
        self,
        *,
        task: AutonomousChainTask,
        drive_input: Optional[Dict[str, Any]] = None,
        autonomous_chain_gate_active: bool = False,
    ) -> tuple[str, str]:
        drive_input = dict(drive_input or {})
        task_type = self._task_governance_type(task)
        task_family = self._task_runtime_family(task)
        if self._is_agent_pull_task(task):
            execution_kind = self._task_execution_kind(task)
            if execution_kind == "body_improvement":
                if self._has_pending_self_learning_prerequisite(task):
                    return (
                        "deferred",
                        "Body-improvement task deferred because there are still planned/approved/running self-learning tasks awaiting completion. Supervisor must let learning evidence settle before code-improvement execution is released.",
                    )
                return (
                    "approved",
                    "Agent-pull body-improvement task transferred by API-B for API-A autonomous execution. Autonomous-chain baseline keeps this path pull -> execute -> write back.",
                )
            return (
                "approved",
                "Agent-pull self-learning task transferred by API-B for API-A autonomous execution. Autonomous-chain baseline keeps this path pull -> execute -> write back.",
            )

        # With the autonomous-chain gate active, self_learning and
        # memory_maintenance can execute without waiting for user-chain quiet
        # signals. Other task families still follow their runtime decisions.
        if autonomous_chain_gate_active:
            if task_type == "self_learning":
                return (
                    "approved",
                    "Autonomous-chain gate active: self-learning task transferred without waiting for user-chain quiet signals. Learn-only constraints still apply.",
                )
            if task_type == "memory_maintenance":
                return (
                    "approved",
                    "Autonomous-chain gate active: memory-maintenance task transferred without waiting for user-chain quiet signals.",
                )

        decision = (
            drive_input.get("task_family_decisions", {}).get(task_family)
            or drive_input.get("governance_task_type_decisions", {}).get(task_type)
            or drive_input["decisions"]
        )

        if decision["eligible_for_execution"]:
            if task_type == "self_learning":
                return (
                    "approved",
                    "该学习链路项已由 API-B 转交：当前没有冲突中的内部流程活动；用户链路只作为软感知信号，不构成自学习证据工作的硬门控。",
                )
            if task_type == "memory_maintenance":
                return (
                    "approved",
                    "该记忆维护链路项已由 API-B 转交：当前运行时与记忆并发护栏满足要求；用户链路仍只作为软感知信号。",
                )
            return (
                "approved",
                "该链路项已由 API-B 转交，将进入下一轮自主交接；当前运行时并发护栏满足要求。",
            )
        if task_type == "self_learning":
            return (
                "deferred",
                "该学习链路项暂缓：当前已有内部流程或子系统在途工作；这次延后来自并发护栏，而不是用户空闲门控。",
            )
        if task_type == "memory_maintenance":
            return (
                "deferred",
                "该记忆维护链路项暂缓：当前仍有运行时或记忆侧工作在途；用户链路仍只作为软感知信号，并非这里的执行门。",
            )
        return (
            "deferred",
            "该链路项暂缓：当前运行时并发护栏尚未满足；任务继续留在 API-B 判断在途中等待后续复核。",
        )

    def _has_pending_self_learning_prerequisite(
        self,
        body_task: Optional[AutonomousChainTask] = None,
    ) -> bool:
        backlog_self_learning_pending = False
        for task in self._active_autonomous_chain_tasks():
            if self._task_governance_type(task) != "self_learning":
                continue
            if task.status not in {"planned", "approved", "running"}:
                continue
            if task.status == "running":
                return True
            backlog_self_learning_pending = True
        if not backlog_self_learning_pending:
            return False
        if body_task is None:
            return True
        prior_self_learning_deferrals = sum(
            1
            for decision in body_task.decision_history
            if str(decision.status) == "deferred"
            and "self-learning tasks awaiting completion" in str(decision.reason)
        )
        return prior_self_learning_deferrals == 0

    def _is_agent_pull_task(self, task: AutonomousChainTask) -> bool:
        execution_kind = self._task_execution_kind(task)
        return (
            self._task_governance_type(task) == "self_learning"
            or execution_kind == "body_improvement"
        )

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
            if not self._is_agent_pull_task(task):
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
                self._autonomous_chain_store.update_metadata(
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
            self._update_task_status(
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
            self._autonomous_chain_store.update_metadata(
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
        if not self._is_agent_pull_task(task):
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
        raw_kind = self._task_execution_kind(task) or "general_self_evolution"
        kind = "memory_maintenance" if raw_kind == "memory_maintenance" else "general_self_evolution"
        task_family = self._task_runtime_family(task)
        governance_task_type = self._task_governance_type(task)

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
        runtime_profile = self._task_runtime_profile(task)
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
        scheduled_for = self._task_schedule_token(task)
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
            # Legacy mirror for older cached consumers; new read models use judgement_preview.
            payload["governance_preview"] = judgement_preview
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

    def _governance_preview_projection(
        self,
        *,
        latest_context: Dict[str, Any],
        current_task: AutonomousChainTask,
    ) -> Dict[str, Any]:
        """Compatibility alias. New callers should use _judgement_preview_projection."""
        return self._judgement_preview_projection(
            latest_context=latest_context,
            current_task=current_task,
        )

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
                    "governance_task_type": self._task_governance_type(task),
                    "task_family": self._task_runtime_family(task),
                    "execution_kind": self._task_execution_kind(task),
                    "scheduled_for": self._task_schedule_token(task),
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

        backlog_snapshot = self._build_supervisor_review_snapshot(tasks)
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
            "如果时间重叠，按先后顺序只保留一个，不能与现有定时任务重复，其余建议 defer 或 cancel；"
            "该保留/顺延建议由监督者 LM 判断\n\n"
            "输出 JSON 对象，格式为：\n"
            "{\n"
            '  "actions": [\n'
            '    {"task_id": "...", "action": "approve|defer|cancel|pause|retire|merge|reprioritize", "reason": "...", "merge_into": "...", "priority": "..."}\n'
            "  ]\n"
            "}\n\n"
            f"【drive_input】\n{json.dumps(drive_input, ensure_ascii=False, default=str)[:3000]}\n\n"
            f"【api_b_judgement】\n{json.dumps(backlog_snapshot, ensure_ascii=False, default=str)[:5000]}"
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
            normalized_status = self._normalize_autonomous_chain_decision(status)
            if normalized_status is None or normalized_status == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported task status filter: {status}")
        tasks = self._autonomous_chain_store.list_chain_projection_tasks(
            status=normalized_status,
            include_cancelled=True,
        )
        if task_type:
            tasks = [t for t in tasks if self._task_governance_type(t) == str(task_type).strip()]
        if execution_kind:
            normalized_execution_kind = self._normalize_runtime_task_family(execution_kind)
            explicit_execution_kind = str(execution_kind).strip().lower()
            filtered_tasks = []
            for task in tasks:
                task_execution_kind = str(self._task_execution_kind(task) or "").strip().lower()
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

        self._autonomous_chain_store.clear_tasks()

        if hasattr(self, "_clear_supervisor_ui_activity"):
            self._clear_supervisor_ui_activity()

        governor = getattr(self, "_governor", None)
        if governor is not None and hasattr(governor, "clear_history"):
            governor.clear_history()
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
        storage_root = getattr(governor, "storage_root", None)
        if storage_root:
            return Path(storage_root) / "mem_governance.jsonl"
        runtime_root = Path(
            getattr(self, "_runtime_root", None)
            or self.config.soul_store_path
            or (Path(self.config.execution.git_repo_path) / ".soul-runtime")
        )
        return runtime_root / "mem_governance.jsonl"

    def _load_mem_governance_events(self) -> list[Any]:
        repo_path = self._mem_governance_repository_path()
        if not repo_path.exists():
            return []
        from memai.governance_repository import GovernanceEventRepository

        return GovernanceEventRepository(repo_path).list_events()

    def _recover_autonomous_chain_store_from_mem_governance(
        self,
        *,
        only_if_empty: bool = False,
        replace: bool = False,
    ) -> Dict[str, Any]:
        existing_count = len(self._autonomous_chain_store.list_tasks())
        if only_if_empty and existing_count > 0:
            return {
                "status": "skipped",
                "reason": "runtime_store_not_empty",
                "existing_task_count": existing_count,
                "mem_governance_path": str(self._mem_governance_repository_path()),
            }
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
            only_if_empty=bool(request.get("only_if_empty", False)),
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
                task = self._autonomous_chain_store.create_task(
                    title=title,
                    summary=str(item.get("summary", "")),
                    trace_id=str(item.get("trace_id") or uuid.uuid4()),
                    task_type=self._request_task_type(item, metadata=request_metadata),
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
                self._autonomous_chain_store.create_task(
                    title=title,
                    summary=str(request.get("summary", "")),
                    trace_id=str(request.get("trace_id") or uuid.uuid4()),
                    task_type=self._request_task_type(request, metadata=request_metadata),
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

        normalized = self._normalize_autonomous_chain_decision(request.get("decision"))
        decision_context: Dict[str, Any] = {}

        if normalized is None or normalized == "auto":
            task_family = self._task_runtime_family(task)
            task_execution_kind = self._task_execution_kind(task)
            drive_input = await self._resolve_runtime_drive_input_request(
                request,
                default_task_family=task_family,
                default_execution_kind=task_execution_kind,
            )
            normalized, auto_reason = self._build_autonomous_chain_auto_decision(
                task=task,
                drive_input=drive_input,
                autonomous_chain_gate_active=getattr(
                    getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False
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
        if normalized == "approved" and self._task_requires_execution_request(task):
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
            and self._is_agent_pull_task(task)
            and owner_session_id
        ):
            task = self._update_task_status(
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

        updated_task = self._update_task_status(
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
            self._autonomous_chain_store.update_metadata(task_id, metadata=decision_metadata)

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

        return {
            "status": normalized,
            "task": self._serialize_autonomous_chain_task(updated_task),
        }

    async def review_autonomous_chain_tasks(self, request: dict | None = None):
        request = request or {}
        statuses = request.get("statuses") or ["planned", "deferred", "paused"]
        normalized_statuses = []
        for status in statuses:
            normalized = self._normalize_autonomous_chain_decision(str(status))
            if normalized is None or normalized == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported review status: {status}")
            normalized_statuses.append(normalized)

        drive_input = await self._resolve_runtime_drive_input_request(request)
        requested_task_family = self._normalize_runtime_task_family(
            request.get("execution_kind")
            or request.get("task_family")
            or drive_input.get("execution_kind")
            or drive_input.get("task_family")
        )
        requested_governance_task_type = self._normalize_runtime_task_type(requested_task_family)
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
        candidate_tasks.sort(key=self._task_sort_key)

        supervisor_review_actions = await self._review_task_governance_with_supervisor(
            candidate_tasks,
            drive_input=drive_input,
        )
        reserved_schedule_tokens = self._build_schedule_conflict_index(
            exclude_task_ids={task.task_id for task in candidate_tasks}
        )

        reviewed = []
        reviewed_statuses = []
        for task in candidate_tasks:
            task_drive_input = drive_input
            task_family = self._task_runtime_family(task)
            if drive_input.get("task_family") != task_family:
                task_execution_kind = self._task_execution_kind(task)
                task_request = dict(request)
                task_drive_input = await self._resolve_runtime_drive_input_request(
                    task_request,
                    default_task_family=task_family,
                    default_execution_kind=task_execution_kind,
                )
            target_status, default_reason = self._build_autonomous_chain_auto_decision(
                task=task,
                drive_input=task_drive_input,
                autonomous_chain_gate_active=getattr(
                    getattr(self, "_service_runtime", None), "autonomous_chain_gate_active", False
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
                    task = self._autonomous_chain_store.update_priority(
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
                    preserve_agent_pull_approval = (
                        target_status == "approved"
                        and suggested_status in {"deferred", "paused"}
                        and self._is_agent_pull_task(task)
                    )
                    if preserve_agent_pull_approval:
                        decision_context["supervisor_followup_suggestion"] = {
                            "action": suggested_status,
                            "reason": str(review_action.get("reason") or "").strip(),
                            "preserved_status": target_status,
                        }
                    else:
                        target_status = suggested_status
                    lm_reason = str(review_action.get("reason") or "").strip()
                    if lm_reason:
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
            schedule_token = self._task_schedule_token(task)
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
                if self._task_requires_execution_request(task):
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
                        updated = self._update_task_status(
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
            updated = self._update_task_status(
                task.task_id,
                status=target_status,
                decision_id=decision_id,
                actor=str(request.get("actor", "supervisor")),
                reason=str(request.get("reason") or default_reason),
                context=decision_context,
                execution_request=execution_request,
                event_type="review",
            )
            reviewed.append(updated)
            reviewed_statuses.append(updated.status)
            updated_schedule_token = self._task_schedule_token(updated)
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
            task = self._autonomous_chain_store.create_task(
                title=proposal.title,
                summary=proposal.summary,
                trace_id=str(submission.metadata.get("trace_id") or submission.conclusion_id or uuid.uuid4()),
                task_type=self._request_task_type(proposal_payload, metadata=proposal_metadata),
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

        # ── Mark running BEFORE any await to prevent duplicate handoff ──
        self._update_task_status(
            task.task_id,
            status="running",
            actor="supervisor",
            reason="自主交接已开始",
            event_type="execution_handoff_started",
        )
        self._autonomous_chain_store.update_metadata(
            task.task_id,
            metadata={
                "executed_at": datetime.now(timezone.utc).isoformat(),
            },
            execution_request=execution_request,
        )

        payload = execution_request.model_dump(mode="json")
        result = await self._execution_facade.execute_autonomous_chain_request(payload)

        # ── Failure recovery: restore approved state so the task can be ──
        # retried on the next cycle.  Only explicit success statuses close the
        # task; empty or unknown statuses mean the executor did not confirm
        # completion.
        result_status = result.get("status") if isinstance(result, dict) else None
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
                "upgrade_awaiting_user_consent",
                "learn_only_completed",
                "autonomous_chain_execution_executed",
                "autonomous_chain_execution_recorded",
            }
        )
        is_failure = (
            normalized_result_status in _ERROR_STATUSES
            or normalized_result_status not in _SUCCESS_STATUSES
        )
        if is_failure:
            failure_count = int(task_metadata.get("execution_failure_count") or 0) + 1
            task_governance_type = self._task_governance_type(task)
            # memory_maintenance tasks are handled by the supervisor's internal
            # memory service (baseline §3.4). API-A pull paths only see
            # autonomous-executor tasks, so retry keeps the task
            # approved for the supervisor handoff lane instead of pushing it
            # through the API-A supervisor_task lane poll.
            if task_governance_type == "memory_maintenance":
                if failure_count < max_retries:
                    self._update_task_status(
                        task.task_id,
                        status="approved",
                        actor="supervisor_memory_service",
                        reason=(
                            f"记忆维护自主交接失败 "
                            f"({failure_count}/{max_retries})；已恢复为待执行，"
                            f"等待监督者下一轮重新交接。"
                            f"executor_status={str(result_status)[:60]}"
                        ),
                        event_type="execution_handoff_failed",
                    )
                else:
                    self._update_task_status(
                        task.task_id,
                        status="failed",
                        actor="supervisor_memory_service",
                        reason=(
                            f"记忆维护自主交接在 {max_retries} 次重试后仍失败。"
                            f"executor_status={str(result_status)[:60]}"
                        ),
                        event_type="execution_handoff_failed",
                    )
                self._autonomous_chain_store.update_metadata(
                    task.task_id,
                    metadata={
                        "execution_failed": True,
                        "execution_failure_count": failure_count,
                        "execution_result": result,
                    },
                )
                return result
            if failure_count < max_retries:
                # Allow retry — set back to approved so it can be re-handed off.
                self._update_task_status(
                    task.task_id,
                    status="approved",
                    actor="supervisor",
                    reason=f"自主交接重试 {failure_count}/{max_retries}",
                    event_type="execution_handoff_retry",
                )
                self._autonomous_chain_store.update_metadata(
                    task.task_id,
                    metadata={
                        "execution_failed": True,
                        "execution_failure_count": failure_count,
                        "execution_result": result,
                    },
                )
            else:
                # Permanent failure — keep the failed lineage so it is not retried.
                self._autonomous_chain_store.update_metadata(
                    task.task_id,
                    metadata={
                        "execution_failed": True,
                        "execution_failure_count": failure_count,
                        "execution_result": result,
                    },
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
        task_governance_type = self._task_governance_type(task)
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
        self._update_task_status(
            task.task_id,
            status="completed",
            actor=actor,
            reason=completion_reason,
            event_type="execution_handoff_completed",
        )
        self._autonomous_chain_store.update_metadata(
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
                        self._update_task_status(
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

        # Pass 1: hand off tasks that were *just* approved in this review.
        handoff_considered_ids: set[str] = set()
        for task_payload in review_result.get("tasks", []):
            if task_payload.get("status") != "approved":
                continue

            task_payload_id = str(task_payload.get("task_id", "") or "")
            task = self._autonomous_chain_store.get_task(task_payload_id)
            if task is None:
                continue
            handoff_considered_ids.add(task.task_id)

            gov_type = self._task_governance_type(task)
            execution_kind = self._task_execution_kind(task)
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

        # Pass 2: hand off any previously-approved tasks that were never
        # handed off, PLUS tasks whose previous handoff failed and were
        # reset to approved for retry (execution_failed=True,
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

            execution_kind = self._task_execution_kind(task)
            if self._task_governance_type(task) == "self_learning" or execution_kind == "body_improvement":
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

        # ── Phase 1: Endogenous drive → form governance backlog projections ──
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

    def _calculate_learning_quality_score(self) -> float:
        completed_count = 0
        quality_sum = 0.0
        freshness_sum = 0.0
        now = datetime.now(timezone.utc)

        for task in self._autonomous_chain_store.list_writeback_history(status="completed"):
            if self._task_runtime_family(task) != "self_learning":
                continue
            completed_count += 1

            task_quality = float(task.metadata.get("quality_score") or 0.5)
            quality_sum += task_quality

            completed_at = task.metadata.get("completed_at")
            if completed_at:
                try:
                    t = datetime.fromisoformat(str(completed_at))
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    age_days = (now - t).days
                    freshness = max(0.0, 1.0 - age_days / 90.0)
                    freshness_sum += freshness
                except Exception:
                    freshness_sum += 0.5

        if completed_count == 0:
            return 0.0

        avg_quality = quality_sum / completed_count
        avg_freshness = freshness_sum / completed_count
        score = avg_quality * 60 + avg_freshness * 40
        return max(0.0, min(100.0, score))

    def _update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        reason: Optional[str] = None,
        actor: str = "supervisor",
        decision_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        execution_request: Optional[AutonomousChainExecutionRequest] = None,
        event_type: str = "status_update",
    ) -> AutonomousChainTask:
        task = self._autonomous_chain_store.update_status(
            task_id,
            status=status,
            decision_id=decision_id,
            actor=actor,
            reason=reason or f"Status updated to {status}",
            context=dict(context or {}),
            execution_request=execution_request,
        )
        self._record_endogenous_drive_outcome(task, event_type=event_type)
        return task

    def _calc_file_repeat_penalty(self, slot_id: str, changed_files: list[str]) -> float:
        penalty = 0.0
        registry = self._execution_facade.body_registry.load_registry()
        try:
            meta = registry.load_slot_meta(slot_id)
        except Exception:
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

    def _calc_learning_freshness(self, learning_refs: list[str]) -> float:
        if not learning_refs:
            return 0.0

        now = datetime.now(timezone.utc)
        total_freshness = 0.0

        for ref in learning_refs:
            try:
                age_days = int(ref.split("_")[-1]) if "_" in ref else 0
                freshness = max(0.0, 1.0 - age_days / 90.0)
                total_freshness += freshness
            except Exception:
                total_freshness += 0.5

        avg_freshness = total_freshness / len(learning_refs)
        return avg_freshness * 20.0

    def _matches_forbidden_pattern(self, file_path: str, patterns: list[str]) -> bool:
        import fnmatch

        path = str(file_path).strip().replace("\\", "/")
        for pattern in patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def _get_probe_score(self, slot_id: str, slot_meta) -> float:
        if slot_meta.last_probe_result:
            probe = slot_meta.last_probe_result
            checks_total = len(probe.get("checks", []))
            checks_passed = sum(1 for c in probe.get("checks", []) if c.get("passed"))
            if checks_total > 0:
                return (checks_passed / checks_total) * 20.0

        if slot_meta.materialized_from:
            try:
                registry = self._execution_facade.body_registry.load_registry()
                parent_meta = registry.load_slot_meta(slot_meta.materialized_from)
                if parent_meta.last_probe_result:
                    probe = parent_meta.last_probe_result
                    checks_total = len(probe.get("checks", []))
                    checks_passed = sum(1 for c in probe.get("checks", []) if c.get("passed"))
                    if checks_total > 0:
                        return (checks_passed / checks_total) * 15.0
            except Exception:
                pass

        return 10.0

    def _apply_cumulative_decay(self, slot_meta) -> None:
        if slot_meta.decay_applied_at is None:
            slot_meta.decay_applied_at = datetime.now(timezone.utc).isoformat()
            return

        try:
            last_decay = datetime.fromisoformat(slot_meta.decay_applied_at)
        except Exception:
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
                days_since_improvement = (now - last_improvement).days
            except Exception:
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
        learning_refs: list[str],
    ) -> float:
        learning_context = ""
        if learning_refs:
            try:
                learning_context = "学习成果引用: " + ", ".join(learning_refs)
            except Exception:
                learning_context = ""

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
        if hasattr(report, 'model_dump'):
            report_dict = report.model_dump()
        elif isinstance(report, dict):
            report_dict = report
        else:
            return {"score_delta": 0, "reject_reason": "invalid_report_type"}

        slot_id = report_dict.get("slot_id")
        if not slot_id:
            return {"score_delta": 0, "reject_reason": "missing_slot_id"}

        changed_files = report_dict.get("changed_files", [])
        commit_hash = report_dict.get("commit_hash")

        if not changed_files or not commit_hash:
            return {"score_delta": 0, "reject_reason": "empty_improvement"}

        registry = self._execution_facade.body_registry.load_registry()
        try:
            slot_meta = registry.load_slot_meta(slot_id)
        except Exception:
            return {"score_delta": 0, "reject_reason": "slot_not_found"}

        await self._apply_cumulative_decay(slot_meta)

        from systems.evolution_boundary import classify_agent_evolution_changes
        boundary = classify_agent_evolution_changes(changed_files)
        boundary_score = boundary.score

        file_penalty = self._calc_file_repeat_penalty(slot_id, changed_files)

        learning_refs = report_dict.get("learning_refs", [])
        learning_freshness = self._calc_learning_freshness(learning_refs)

        probe_score = self._get_probe_score(slot_id, slot_meta)

        diff_text = ""
        try:
            import subprocess
            worktree_path = slot_meta.worktree_path
            result = subprocess.run(
                ["git", "show", commit_hash, "--stat"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                diff_text = result.stdout
        except Exception:
            pass

        llm_score = await self._llm_review_diff(
            diff_text,
            report_dict.get("improvement_description", ""),
            learning_refs,
        )

        score_delta = (
            llm_score * 0.35
            + boundary_score * 0.20
            + learning_freshness * 0.15
            + (20.0 if learning_refs else 0.0) * 0.15
            + probe_score * 0.25
            - file_penalty
        )
        score_delta = max(-20.0, min(30.0, score_delta))

        if score_delta > 0 and slot_meta.health_score < 100:
            slot_meta.health_score = min(100.0, slot_meta.health_score + score_delta)
        elif score_delta < 0:
            slot_meta.health_score = max(0.0, slot_meta.health_score + score_delta)

        now = datetime.now(timezone.utc)
        slot_meta.health_history.append({
            "score_delta": score_delta,
            "reason": "body_improvement",
            "task_id": report_dict.get("task_id"),
            "commit_hash": commit_hash,
            "reviewed_at": now.isoformat(),
            "changed_files": changed_files,
        })
        slot_meta.improvement_count += 1
        slot_meta.last_improvement_at = now.isoformat()

        if score_delta > 0:
            slot_meta.previous_healthy_commit = commit_hash

        self._execution_facade.body_registry.save_slot_meta(slot_meta)

        active_slot = self._execution_facade.body_registry.get_active_slot()
        active_health = active_slot.health_score if active_slot else 0.0

        if slot_meta.health_score >= active_health + 15:
            await self._emit_switch_suggestion_event(slot_id)
        elif slot_meta.health_score > active_health:
            await self._emit_switch_suggestion_event(slot_id)

        return {
            "score_delta": score_delta,
            "health_score": slot_meta.health_score,
            "improvement_count": slot_meta.improvement_count,
        }

    async def _emit_switch_suggestion_event(self, slot_id: str):
        try:
            await self._governor.evaluate({
                "event_type": "switch_suggestion",
                "slot_id": slot_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            logger.warning("Failed to emit switch_suggestion event for slot %s", slot_id)






