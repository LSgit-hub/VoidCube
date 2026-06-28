from __future__ import annotations

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
from systems.self_learning import SupervisorConclusionSubmission
from systems.supervisor.endogenous_drive import CORE_VALUES
from systems.supervisor.task_queue import (
    SelfEvolutionExecutionRequest,
    SelfEvolutionGitLineage,
    SelfEvolutionTask,
)

logger = logging.getLogger("supervisor")


# ──────────────────────────────────────────────────────────────────────
# Supervisor scene taxonomy (baseline §3.4 / §3.6 / §13.2)
# ──────────────────────────────────────────────────────────────────────
# The supervisor (API-B) is the governance identity of Mem.  It only
# MANAGES the task list and runs endogenous drive — it never executes
# learning or body-upgrade code.  Therefore the supervisor's `scene`
# field is restricted to the values below.  The Agent (API-A) is the
# only component that may surface "learning" / "execution" scenes.
#
#   idle         - at rest
#   planning     - deciding / approving / denying a task (managing list)
#   memory       - actively touching long-term memory (Mem internal)
#   drive        - endogenous drive: cognitive evaluation / governance output
#   dispatch     - sending an execution request to the body executor
#   maintenance  - memory-maintenance sweep (long-term memory hygiene)
#   body_switch  - judging a body switch request
#
# Forbidden scenes for the supervisor (API-A territory):
#   "learning"   - the Agent is doing learning work
#   "execution"  - the Agent or executor is doing work
# ──────────────────────────────────────────────────────────────────────
SUPERVISOR_LEGAL_SCENES: frozenset[str] = frozenset(
    {"idle", "planning", "memory", "drive", "dispatch", "maintenance"}
)



class PlanningRuntimeMixin:
    """Supervisor planning, idle-window, and self-evolution orchestration."""
    _ENDOGENOUS_DRIVE_HISTORY_LIMIT = 240
    _ENDOGENOUS_GOVERNANCE_EVENT_LIMIT = 240

    _LM_QUEUE_ACTION_TO_STATUS: Dict[str, str] = {
        "approve": "approved",
        "approved": "approved",
        "defer": "deferred",
        "deferred": "deferred",
        "cancel": "cancelled",
        "cancelled": "cancelled",
        "pause": "paused",
        "paused": "paused",
    }
    _LM_QUEUE_SHADOW_ACTIONS: frozenset[str] = frozenset(
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
                    "summary": "No LM proposal cognition has been formed yet.",
                    "lm_reasoning_state": {},
                    "task_type_priors": {
                        "top_priority_task_type": None,
                        "top_priority_score": 0.0,
                        "priors": [],
                    },
                    "proposal_drift_memory": {
                        "available": False,
                        "average_score": 0.0,
                        "drift_state": "unknown",
                        "quality_counts": {},
                        "summary": "No proposal drift memory is available yet.",
                    },
                    "recent_reference_alignment": {
                        "available": False,
                        "average_alignment_score": 0.0,
                        "weak_or_partial_count": 0,
                        "summary": "No recent reference alignment is available yet.",
                    },
                    "recent_cognitive_alignment": {
                        "available": False,
                        "average_score": 0.0,
                        "quality_counts": {},
                        "dominant_top_priority_task_type": None,
                        "summary": "No recent cognitive alignment feedback is available yet.",
                        "common_reasons": [],
                        "entries": [],
                    },
                    "current_candidates": {
                        "count": 0,
                        "lm_generated_count": 0,
                        "task_type_counts": {},
                        "quality_counts": {},
                        "average_cognitive_alignment_score": 0.0,
                        "average_reference_alignment_score": 0.0,
                        "entries": [],
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
                    "entries": [],
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
        trimmed["events"] = list(trimmed.get("events") or [])[: self._ENDOGENOUS_GOVERNANCE_EVENT_LIMIT]
        return trimmed

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
        readiness_score = self._clamp_endogenous_ratio(
            evidence_basis.get("self_iteration_readiness_score") or 0.0
        )
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
        if reference_alignment_score < reference_alignment_min_score:
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
        if readiness_score < readiness_min_score:
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
            int(policy.get("auto_truthfulness_correction_signal_threshold") or 3),
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
            or dominant_constraint in {"queue_blockage", "historical_underdelivery"}
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

    def _build_endogenous_cognition_state(
        self,
        *,
        deliberation: Dict[str, Any],
        governance_channels: Dict[str, Any],
        governance_event_stream: Dict[str, Any],
        self_regulation: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
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
            candidate_items=candidate_items,
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
        proposal_cognition = self._build_endogenous_proposal_cognition(
            history_snapshot=history_snapshot,
            candidate_items=candidate_items,
            deliberation=deliberation,
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
            "governance": {
                "posture": drive_posture,
                "preferred_focus": adaptive_policy.get("preferred_focus"),
                "dominant_constraint": reflection.get("dominant_constraint"),
                "candidate_count": len(candidate_items),
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

    def _build_endogenous_proposal_cognition(
        self,
        *,
        history_snapshot: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
        deliberation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        lm_reasoning_state: Dict[str, Any] = {}
        engine = getattr(self, "_endogenous_drive_engine", None)
        if engine is not None and hasattr(engine, "get_latest_lm_task_generation_context"):
            try:
                lm_reasoning_state = dict(
                    engine.get_latest_lm_task_generation_context() or {}
                )
            except Exception:
                lm_reasoning_state = {}

        task_type_priors = dict(lm_reasoning_state.get("task_type_priors") or {})
        proposal_drift_memory = dict(lm_reasoning_state.get("proposal_drift_memory") or {})
        recent_reference_alignment = dict(
            lm_reasoning_state.get("recent_reference_alignment") or {}
        )
        recent_cognitive_alignment = self._build_recent_cognitive_alignment_summary(
            history_snapshot=history_snapshot,
        )
        current_candidates = self._build_current_candidate_cognition_summary(
            candidate_items=candidate_items,
        )

        top_priority_task_type = str(task_type_priors.get("top_priority_task_type") or "").strip()
        top_priority_score = round(
            self._clamp_endogenous_ratio(task_type_priors.get("top_priority_score") or 0.0),
            4,
        )
        priors = [
            {
                "task_type": str(item.get("task_type") or "").strip(),
                "score": round(
                    self._clamp_endogenous_ratio(item.get("score") or 0.0),
                    4,
                ),
                "reasons": [
                    str(reason).strip()
                    for reason in list(item.get("reasons") or [])[:3]
                    if str(reason).strip()
                ],
            }
            for item in list(task_type_priors.get("priors") or [])[:5]
            if isinstance(item, dict) and str(item.get("task_type") or "").strip()
        ]

        drift_state = str(proposal_drift_memory.get("drift_state") or "").strip() or "unknown"
        summary = (
            f"LM proposal cognition favors {top_priority_task_type or 'unknown'} "
            f"tasks ({top_priority_score:.2f}); drift state is {drift_state}; "
            f"current candidate count={int(current_candidates.get('count') or 0)}."
        )
        if recent_cognitive_alignment.get("available"):
            summary += (
                f" Recent cognitive alignment average="
                f"{float(recent_cognitive_alignment.get('average_score') or 0.0):.2f}."
            )

        return {
            "summary": summary,
            "lm_reasoning_state": lm_reasoning_state,
            "cognitive_control_policy": self._current_cognitive_control_policy(),
            "active_cognitive_posture_profile": self._current_active_cognitive_posture_profile(
                lm_reasoning_state=lm_reasoning_state,
                history_snapshot=history_snapshot,
                deliberation=deliberation,
            ),
            "task_type_priors": {
                "top_priority_task_type": top_priority_task_type or None,
                "top_priority_score": top_priority_score,
                "priors": priors,
            },
            "proposal_drift_memory": {
                "available": bool(proposal_drift_memory.get("available")),
                "average_score": round(
                    self._clamp_endogenous_ratio(
                        proposal_drift_memory.get("average_score") or 0.0
                    ),
                    4,
                ),
                "drift_state": drift_state,
                "quality_counts": dict(proposal_drift_memory.get("quality_counts") or {}),
                "common_posture_alignment": [
                    str(item).strip()
                    for item in list(proposal_drift_memory.get("common_posture_alignment") or [])[:4]
                    if str(item).strip()
                ],
                "common_priority_basis": [
                    str(item).strip()
                    for item in list(proposal_drift_memory.get("common_priority_basis") or [])[:4]
                    if str(item).strip()
                ],
                "posture_alignment_health": str(
                    proposal_drift_memory.get("posture_alignment_health") or ""
                ).strip(),
                "priority_basis_health": str(
                    proposal_drift_memory.get("priority_basis_health") or ""
                ).strip(),
                "summary": str(proposal_drift_memory.get("summary") or "").strip(),
            },
            "recent_reference_alignment": {
                "available": bool(recent_reference_alignment.get("available")),
                "average_alignment_score": round(
                    self._clamp_endogenous_ratio(
                        recent_reference_alignment.get("average_alignment_score") or 0.0
                    ),
                    4,
                ),
                "weak_or_partial_count": max(
                    0,
                    int(recent_reference_alignment.get("weak_or_partial_count") or 0),
                ),
                "summary": str(recent_reference_alignment.get("summary") or "").strip(),
            },
            "recent_cognitive_alignment": recent_cognitive_alignment,
            "current_candidates": current_candidates,
        }

    def _current_cognitive_control_policy(self) -> Dict[str, Any]:
        runtime_config = getattr(self.config, "service_runtime", None)
        charter_model = getattr(runtime_config, "endogenous_drive_cognition_charter", None)
        policy_model = getattr(charter_model, "cognitive_control_policy", None)
        if hasattr(policy_model, "model_dump"):
            return policy_model.model_dump(mode="json")
        return dict(policy_model or {})

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
        entries: list[Dict[str, Any]] = []
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
            top_priority_task_type = str(
                cognitive_alignment.get("top_priority_task_type") or ""
            ).strip().lower()
            if top_priority_task_type:
                top_priority_counts[top_priority_task_type] = (
                    top_priority_counts.get(top_priority_task_type, 0) + 1
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
            entries.append(
                {
                    "title": str(outcome.get("title") or "").strip(),
                    "quality": quality,
                    "score": round(
                        self._clamp_endogenous_ratio(
                            cognitive_alignment.get("score") or 0.0
                        ),
                        4,
                    ),
                    "top_priority_task_type": top_priority_task_type or None,
                    "reasons": reasons,
                    "llm_posture_alignment": normalized_posture_alignment,
                    "llm_priority_basis": normalized_priority_basis,
                    "event_type": str(outcome.get("event_type") or "").strip(),
                }
            )
            if len(entries) >= 4:
                break

        if not entries:
            return {
                "available": False,
                "average_score": 0.0,
                "quality_counts": {},
                "dominant_top_priority_task_type": None,
                "summary": "No recent cognitive alignment feedback is available yet.",
                "common_reasons": [],
                "common_posture_alignment": [],
                "common_priority_basis": [],
                "missing_posture_alignment_count": 0,
                "missing_priority_basis_count": 0,
                "entries": [],
            }

        average_score = sum(float(item["score"]) for item in entries) / len(entries)
        dominant_top_priority_task_type = None
        if top_priority_counts:
            dominant_top_priority_task_type = max(
                top_priority_counts.items(),
                key=lambda item: (item[1], item[0]),
            )[0]
        common_reasons = [
            reason
            for reason, _count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:5]
        ]
        common_posture_alignment = [
            item
            for item, _count in sorted(
                posture_alignment_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        common_priority_basis = [
            item
            for item, _count in sorted(
                priority_basis_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )[:4]
        ]
        summary = (
            f"Recent cognitive alignment average={average_score:.2f}; "
            f"dominant quality="
            f"{max(quality_counts.items(), key=lambda item: (item[1], item[0]))[0]}."
        )
        return {
            "available": True,
            "average_score": round(self._clamp_endogenous_ratio(average_score), 4),
            "quality_counts": quality_counts,
            "dominant_top_priority_task_type": dominant_top_priority_task_type,
            "summary": summary,
            "common_reasons": common_reasons,
            "common_posture_alignment": common_posture_alignment,
            "common_priority_basis": common_priority_basis,
            "missing_posture_alignment_count": missing_posture_alignment_count,
            "missing_priority_basis_count": missing_priority_basis_count,
            "entries": entries,
        }

    def _build_current_candidate_cognition_summary(
        self,
        *,
        candidate_items: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        entries: list[Dict[str, Any]] = []
        task_type_counts: Dict[str, int] = {}
        quality_counts: Dict[str, int] = {"strong": 0, "partial": 0, "weak": 0}
        cognitive_scores: list[float] = []
        reference_scores: list[float] = []
        lm_generated_count = 0

        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            metadata = dict(item.get("metadata") or {})
            evidence = dict(item.get("evidence") or {})
            llm_generated = bool(metadata.get("llm_task_generated") or evidence.get("llm_generated"))
            if llm_generated:
                lm_generated_count += 1
            task_type = str(
                metadata.get("llm_task_type")
                or evidence.get("llm_task_type")
                or ""
            ).strip().lower()
            if task_type:
                task_type_counts[task_type] = task_type_counts.get(task_type, 0) + 1
            cognitive_alignment = metadata.get("cognitive_alignment")
            if not isinstance(cognitive_alignment, dict):
                cognitive_alignment = evidence.get("cognitive_alignment")
            reference_alignment = metadata.get("reference_alignment")
            if not isinstance(reference_alignment, dict):
                reference_alignment = evidence.get("reference_alignment")

            quality = None
            score = None
            if isinstance(cognitive_alignment, dict) and cognitive_alignment:
                quality = str(cognitive_alignment.get("quality") or "partial").strip().lower()
                if quality not in quality_counts:
                    quality = "partial"
                quality_counts[quality] += 1
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

            entries.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "stable_key": str(item.get("stable_key") or "").strip() or None,
                    "llm_generated": llm_generated,
                    "task_type": task_type or None,
                    "cognitive_alignment_quality": quality,
                    "cognitive_alignment_score": score,
                    "reference_alignment_score": reference_score,
                }
            )

        average_cognitive_alignment_score = (
            sum(cognitive_scores) / len(cognitive_scores) if cognitive_scores else 0.0
        )
        average_reference_alignment_score = (
            sum(reference_scores) / len(reference_scores) if reference_scores else 0.0
        )
        return {
            "count": len(entries),
            "lm_generated_count": lm_generated_count,
            "task_type_counts": task_type_counts,
            "quality_counts": {
                key: value for key, value in quality_counts.items() if value > 0
            },
            "average_cognitive_alignment_score": round(
                self._clamp_endogenous_ratio(average_cognitive_alignment_score),
                4,
            ),
            "average_reference_alignment_score": round(
                self._clamp_endogenous_ratio(average_reference_alignment_score),
                4,
            ),
            "entries": entries[:8],
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
                + (0.08 if dominant_constraint in {"weak_learning_yield", "historical_underdelivery", "queue_blockage"} else 0.0)
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
                + (0.06 if current_focus == "queue_hygiene" else 0.0)
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
        entries = [
            {
                "mode": meta_mode["mode"],
                "confidence": meta_mode["confidence"],
                "stability": meta_mode["stability"],
                "drivers": list(meta_mode["drivers"]),
                "guardrails": list(meta_mode["guardrails"]),
                "preferred_focus": adaptive_policy.get("preferred_focus"),
                "corrective_mode": correction_mode.get("mode"),
                "observation_target": observation_program.get("highest_priority_target"),
                "agenda_topic": attention_agenda.get("entries", [{}])[0].get("topic") if attention_agenda.get("entries") else None,
                "uncertainty_domain": uncertainty_ledger.get("highest_risk_domain"),
                "context_key": context_key,
            }
        ]
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
            "entries": entries,
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
        candidate_items: list[Dict[str, Any]],
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

        candidate_kind_counts: Dict[str, int] = {}
        for item in candidate_items:
            if not isinstance(item, dict):
                continue
            metadata = dict(item.get("metadata") or {})
            score_breakdown = dict(metadata.get("score_breakdown") or {})
            candidate_kind = str(score_breakdown.get("candidate_kind") or "").strip().lower()
            if candidate_kind:
                candidate_kind_counts[candidate_kind] = candidate_kind_counts.get(candidate_kind, 0) + 1

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
            candidate_kind = str((matching_intent or {}).get("candidate_kind") or "").strip().lower()
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
                    "candidate_kind": candidate_kind or None,
                    "candidate_count": candidate_kind_counts.get(candidate_kind, 0),
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

        queue_pressure = float(reflection.get("queue_blockage_pressure") or 0.0)
        if queue_pressure >= 0.28 or str(world_model.get("queue_health") or "").strip() in {"busy", "strained"}:
            risk = self._clamp_endogenous_ratio(
                queue_pressure * 0.7
                + (0.2 if str(world_model.get("queue_health") or "").strip() == "strained" else 0.08)
            )
            entries.append(
                {
                    "ledger_id": "uncertainty:queue_blockage",
                    "domain": "governance_backlog",
                    "risk": round(risk, 4),
                    "confidence": round(
                        self._clamp_endogenous_ratio(
                            0.58 + float(adaptive_policy.get("queue_hygiene_bias") or 0.0) * 0.18
                        ),
                        4,
                    ),
                    "hypothesis": (
                        "Additional autonomous output may worsen queue drag before governance review debt is reduced."
                    ),
                    "why_uncertain": (
                        "Backlog pressure is visible, but the drive still needs to inspect whether the queue is blocked by stale work, review debt, or repeated low-yield candidates."
                    ),
                    "observation_target": "queue_blockage",
                    "recommended_probe": "inspect stale, deferred, and pending-review endogenous tasks",
                    "evidence": [
                        f"queue_blockage_state={reflection.get('queue_blockage_state')}",
                        f"active_queue_count={int(perception.get('active_queue_count') or 0)}",
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
                        "The drive can see mixed or weak learning signals, but it lacks enough evidence to know whether the problem is topic choice, queue drag, or low follow-through."
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
            or dominant_constraint in {"weak_learning_yield", "historical_underdelivery", "queue_blockage"}
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
        if entries_seed:
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
        task: Optional[SelfEvolutionTask] = None,
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
        if normalized == "completed":
            return "completed"
        if normalized in {"failed", "cancelled"}:
            return "failed"
        if normalized in {"approved", "deferred", "paused", "awaiting_review", "retry", "planned", "running"}:
            return "dragging"
        return None

    def _annotate_endogenous_drive_candidates(
        self,
        *,
        deliberation: Dict[str, Any],
        idle_window: Dict[str, Any],
        candidate_items: list[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        if not candidate_items:
            return []

        history = self._load_endogenous_drive_history()
        evaluation_id = str(uuid.uuid4())
        recorded_at = datetime.now(timezone.utc).isoformat()
        prepared: list[Dict[str, Any]] = []
        judgement_records: list[Dict[str, Any]] = []
        agenda_entries = self._build_endogenous_attention_agenda(
            deliberation=deliberation,
            governance_channels=self._governance_channels_from_deliberation(deliberation),
            candidate_items=candidate_items,
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
            judgement_id = str(uuid.uuid4())

            metadata["endogenous_evaluation_id"] = evaluation_id
            metadata["endogenous_judgement_id"] = judgement_id
            row["metadata"] = metadata

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
                    "idle_context": {
                        "user_mode": dict(deliberation.get("perception") or {}).get("user_mode"),
                        "system_posture": dict(deliberation.get("perception") or {}).get("system_posture"),
                        "active_sessions": dict(deliberation.get("perception") or {}).get("active_sessions"),
                        "correction_signals": dict(deliberation.get("perception") or {}).get("correction_signals"),
                        "queue_count": dict(deliberation.get("perception") or {}).get("active_queue_count"),
                        "governor_mode_active": bool(idle_window.get("governor_mode_active")),
                    },
                }
            )

        if judgement_records:
            history["judgements"] = judgement_records + list(history.get("judgements") or [])
            self._persist_endogenous_drive_history(history)
        return prepared

    def _record_endogenous_drive_outcome(
        self,
        task: SelfEvolutionTask,
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
        for existing in list(history.get("outcomes") or [])[:24]:
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

    def _normalize_runtime_task_family(self, value: Optional[str]) -> str:
        return str(
            normalize_runtime_task_family(value, default="general_self_evolution")
        )

    def _normalize_runtime_task_type(self, value: Optional[str]) -> str:
        return str(normalize_runtime_task_type(value, default="self_evolution"))

    def _task_runtime_family(self, task: SelfEvolutionTask) -> str:
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

    def _task_execution_kind(self, task: SelfEvolutionTask) -> Optional[str]:
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

    def _task_runtime_profile(self, task: SelfEvolutionTask) -> Dict[str, Any]:
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
        for key in ("governance_task_type", "task_family", "execution_kind"):
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

    def _idle_window_request_profile(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return derive_runtime_task_profile(
            governance_task_type=request.get("governance_task_type"),
            task_family=request.get("task_family"),
            execution_kind=request.get("execution_kind"),
            default_task_family="general_self_evolution",
        )

    def _task_governance_type(self, task: SelfEvolutionTask) -> str:
        return str(self._task_runtime_profile(task)["governance_task_type"])

    def _task_requires_execution_request(self, task: SelfEvolutionTask) -> bool:
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

    def _task_schedule_token(self, task: SelfEvolutionTask) -> Optional[str]:
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
            getattr(self.config.service_runtime, "self_evolution_review_interval", 300) or 300
        )
        return max(300, review_interval)

    def _next_execution_window_start(self, now: datetime) -> datetime:
        service_cfg = self.config.service_runtime
        start_hour = int(getattr(service_cfg, "execution_window_start_hour", 0) or 0)
        end_hour = int(getattr(service_cfg, "execution_window_end_hour", 24) or 24)

        candidate = now.replace(
            hour=start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        if start_hour <= now.hour < end_hour:
            return now
        if now < candidate:
            return candidate
        return candidate + timedelta(days=1)

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

    def _scheduled_for_within_window(self, when: datetime) -> datetime:
        service_cfg = self.config.service_runtime
        start_hour = int(getattr(service_cfg, "execution_window_start_hour", 0) or 0)
        end_hour = int(getattr(service_cfg, "execution_window_end_hour", 24) or 24)

        if start_hour <= when.hour < end_hour:
            return when
        normalized = self._next_execution_window_start(when)
        if normalized == when:
            return when
        return normalized.replace(
            hour=start_hour,
            minute=0,
            second=0,
            microsecond=0,
        )

    def _occupied_scheduled_for_tokens(self) -> set[str]:
        terminal = {"completed", "failed", "cancelled"}
        occupied: set[str] = set()
        for task in self._self_evolution_queue.list_tasks():
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

        cursor = self._scheduled_for_within_window(
            self._align_scheduled_for(self._next_execution_window_start(current))
        )
        while len(scheduled) < count:
            cursor = self._scheduled_for_within_window(self._align_scheduled_for(cursor))
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

        for index, item in enumerate(candidate_items):
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
                row["scheduled_for"] = existing_token
                row_metadata["scheduled_for"] = existing_token
                occupied.add(existing_token)
            else:
                missing_indexes.append(index)
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

    def _task_sort_key(self, task: SelfEvolutionTask) -> tuple[int, str, str]:
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
    ) -> Dict[str, SelfEvolutionTask]:
        terminal = {"completed", "failed", "cancelled"}
        excluded = exclude_task_ids or set()
        conflicts: Dict[str, SelfEvolutionTask] = {}
        for task in sorted(self._self_evolution_queue.list_tasks(), key=self._task_sort_key):
            if task.task_id in excluded:
                continue
            if str(task.status or "").strip().lower() in terminal:
                continue
            schedule_token = self._task_schedule_token(task)
            if not schedule_token:
                continue
            conflicts.setdefault(schedule_token, task)
        return conflicts

    def _task_activity_metadata(self, task: SelfEvolutionTask) -> Dict[str, Any]:
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

    def _build_self_evolution_activity_metadata(
        self,
        tasks: list[SelfEvolutionTask],
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

        payload = shell_meta.model_dump(mode="json")
        slot_id = str(payload.get("slot_id") or "").strip()
        if not slot_id:
            return payload

        from pathlib import Path

        repaired = False
        expected_root = registry.slot_root(slot_id)
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
        for task in self._self_evolution_queue.list_tasks(status="completed"):
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

    def _drive_queue_task_summaries(self, limit: int = 20) -> list[Dict[str, Any]]:
        rows: list[tuple[str, Dict[str, Any]]] = []
        for task in self._self_evolution_queue.list_tasks():
            rows.append(
                (
                    str(getattr(task, "updated_at", None) or getattr(task, "created_at", None) or ""),
                    {
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
                    },
                )
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in rows[: max(0, limit)]]

    def _planning_activity_kind_for_task(self, task_type: str) -> str:
        normalized = self._normalize_runtime_task_type(task_type)
        if normalized == "self_learning":
            return "self_learning"
        if normalized in {"self_evolution", "memory_maintenance"}:
            return "self_evolution_plan"
        return "self_evolution_plan"

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
                                detail=f"Gateway activity endpoint returned status {response.status}",
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
        raise HTTPException(status_code=503, detail="Gateway activity snapshot unavailable")

    def _parse_activity_timestamp(self, value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
            # Normalize to naive UTC for safe comparison with datetime.now()
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

    async def evaluate_idle_window(self, request: dict | None = None):
        request = request or {}
        snapshot = await self._fetch_gateway_activity_snapshot()

        now_override = request.get("now")
        if isinstance(now_override, str):
            try:
                now = datetime.fromisoformat(now_override)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid now override: {exc}")
        else:
            now = datetime.now()

        service_cfg = self.config.service_runtime
        user_idle_threshold = int(request.get("user_idle_seconds", 600))
        memory_idle_threshold = int(request.get("memory_idle_seconds", 600))
        workflow_idle_threshold = int(request.get("workflow_idle_seconds", 600))
        execution_window_start_hour = int(
            request.get(
                "execution_window_start_hour",
                getattr(service_cfg, "execution_window_start_hour", 0),
            )
        )
        execution_window_end_hour = int(
            request.get(
                "execution_window_end_hour",
                getattr(service_cfg, "execution_window_end_hour", 24),
            )
        )
        requested_task_profile = self._idle_window_request_profile(request)
        requested_governance_task_type = str(requested_task_profile["governance_task_type"])
        requested_task_family = str(requested_task_profile["task_family"])

        last_user_request_at = self._parse_activity_timestamp(snapshot.get("last_user_request_at"))
        last_agent_work_at = self._parse_activity_timestamp(snapshot.get("last_agent_work_at"))
        last_memory_task_at = self._parse_activity_timestamp(snapshot.get("last_memory_task_at"))
        last_self_learning_activity_at = self._parse_activity_timestamp(
            snapshot.get("last_self_learning_activity_at")
        )
        last_self_evolution_plan_at = self._parse_activity_timestamp(
            snapshot.get("last_self_evolution_plan_at")
        )
        last_self_evolution_execute_at = self._parse_activity_timestamp(
            snapshot.get("last_self_evolution_execute_at")
        )
        last_self_evolution_activity_at = self._parse_activity_timestamp(
            snapshot.get("last_self_evolution_activity_at")
        )

        user_idle_seconds = self._idle_seconds_since(last_user_request_at, now=now)
        agent_idle_seconds = self._idle_seconds_since(last_agent_work_at, now=now)
        memory_idle_seconds = self._idle_seconds_since(last_memory_task_at, now=now)
        self_learning_idle_seconds = self._idle_seconds_since(last_self_learning_activity_at, now=now)
        self_evolution_plan_idle_seconds = self._idle_seconds_since(
            last_self_evolution_plan_at,
            now=now,
        )
        self_evolution_execute_idle_seconds = self._idle_seconds_since(
            last_self_evolution_execute_at,
            now=now,
        )
        self_evolution_idle_seconds = self._idle_seconds_since(last_self_evolution_activity_at, now=now)

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
        # signals keep arriving.  This keeps truthfulness candidates from
        # being permanently produced by one old error long after the system
        # has self-corrected.  We use the user-idle window as a coarse proxy
        # for "how long has the system been calm" — when the user has been
        # idle for a long time, an old error should weigh less, since a
        # working session would have produced new activity.  This is a
        # best-effort heuristic (Gateway does not expose per-signal
        # timestamps) and matches the architectural baseline §4.2
        # "activity facts come from gateway" without requiring a new field.
        if user_idle_seconds is None:
            user_idle_hours = 24.0
        else:
            user_idle_hours = min(user_idle_seconds / 3600.0, 24.0)
        decay_factor = max(0.0, 1.0 - user_idle_hours / 4.0)
        correction_signals = int(round((error_count + uncertainty_count) * decay_factor))

        has_user_idle = user_idle_seconds is None or user_idle_seconds >= user_idle_threshold
        has_memory_idle = memory_idle_seconds is None or memory_idle_seconds >= memory_idle_threshold
        has_agent_idle = agent_idle_seconds is None or agent_idle_seconds >= workflow_idle_threshold
        has_self_learning_idle = (
            self_learning_idle_seconds is None
            or self_learning_idle_seconds >= workflow_idle_threshold
        )
        has_self_evolution_plan_idle = (
            self_evolution_plan_idle_seconds is None
            or self_evolution_plan_idle_seconds >= workflow_idle_threshold
        )
        has_self_evolution_execute_idle = (
            self_evolution_execute_idle_seconds is None
            or self_evolution_execute_idle_seconds >= workflow_idle_threshold
        )
        has_self_evolution_idle = (
            self_evolution_idle_seconds is None
            or self_evolution_idle_seconds >= workflow_idle_threshold
        )

        in_execution_window = execution_window_start_hour <= now.hour < execution_window_end_hour
        governance_task_type_decisions = {
            "user": {
                "eligible_for_planning": True,
                "eligible_for_execution": True,
            },
            "self_learning": {
                "eligible_for_planning": has_user_idle,
                "eligible_for_execution": (
                    has_user_idle
                    and has_agent_idle
                    and has_memory_idle
                    and has_self_learning_idle
                    and has_self_evolution_plan_idle
                ),
            },
            "memory_maintenance": {
                "eligible_for_planning": has_user_idle,
                "eligible_for_execution": (
                    in_execution_window
                    and has_user_idle
                    and has_agent_idle
                    and has_memory_idle
                ),
            },
            "self_evolution": {
                "eligible_for_planning": (
                    has_user_idle
                    and has_self_evolution_plan_idle
                ),
                "eligible_for_execution": (
                    in_execution_window
                    and has_user_idle
                    and has_agent_idle
                    and has_memory_idle
                    and has_self_evolution_plan_idle
                    and has_self_evolution_execute_idle
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

        governor_mode_active = bool(
            request.get("governor_mode_active")
            or getattr(getattr(self, "_service_runtime", None), "governor_mode_active", False)
        )
        if governor_mode_active:
            # In Governor Mode, self_learning and memory_maintenance planning
            # is no longer gated by idle_window — the user has explicitly
            # authorised governance.  Execution still follows its own gating.
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
                "agent": agent_idle_seconds,
                "memory": memory_idle_seconds,
                "self_learning": self_learning_idle_seconds,
                "self_evolution_plan": self_evolution_plan_idle_seconds,
                "self_evolution_execute": self_evolution_execute_idle_seconds,
                "self_evolution": self_evolution_idle_seconds,
            },
            "thresholds": {
                "user_idle_seconds": user_idle_threshold,
                "memory_idle_seconds": memory_idle_threshold,
                "workflow_idle_seconds": workflow_idle_threshold,
                "execution_window_start_hour": execution_window_start_hour,
                "execution_window_end_hour": execution_window_end_hour,
            },
            "checks": {
                "has_user_idle": has_user_idle,
                "has_memory_idle": has_memory_idle,
                "has_agent_idle": has_agent_idle,
                "has_self_learning_idle": has_self_learning_idle,
                "has_self_evolution_plan_idle": has_self_evolution_plan_idle,
                "has_self_evolution_execute_idle": has_self_evolution_execute_idle,
                "has_self_evolution_idle": has_self_evolution_idle,
                "in_execution_window": in_execution_window,
            },
            "governance_task_type_decisions": governance_task_type_decisions,
            "task_family_decisions": task_family_decisions,
            "governor_mode_active": governor_mode_active,
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
        for task in self._self_evolution_queue.list_tasks():
            if task.status in terminal:
                continue
            key = task.metadata.get("endogenous_drive_key")
            if isinstance(key, str) and key:
                keys.add(key)
        return keys

    async def evaluate_endogenous_drive(self, request: dict | None = None):
        request = request or {}
        idle_window_request = dict(request.get("idle_window") or {})
        idle_window_request.setdefault(
            "governor_mode_active",
            getattr(getattr(self, "_service_runtime", None), "governor_mode_active", False),
        )
        record_activity = bool(request.get("record_activity", True))
        idle_window = await self.evaluate_idle_window(idle_window_request)
        persisted_self_regulation = self._load_endogenous_self_regulation()
        idle_window["queued_tasks"] = self._drive_queue_task_summaries(limit=24)
        idle_window["endogenous_drive_policy"] = {
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
        idle_window["drive_history"] = self._history_for_endogenous_drive(
            self._load_endogenous_drive_history()
        )
        self_regulation = dict(persisted_self_regulation)
        for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        ):
            idle_window["endogenous_drive_policy"][key] = float(
                self_regulation.get(key) or 0.0
            )
        max_candidates = int(
            request.get(
                "max_candidates",
                self.config.service_runtime.endogenous_drive_max_candidates,
            )
        )
        deliberation = self._endogenous_drive_engine.build_deliberation_report(
            idle_window=idle_window,
        )
        candidates = self._endogenous_drive_engine.generate_candidates(
            idle_window=idle_window,
            existing_drive_keys=self._existing_endogenous_drive_keys(),
            max_candidates=max_candidates,
        )
        queue_items = self._apply_scheduled_for_to_candidate_items(
            [
                {
                    **candidate.to_queue_item(),
                    "stable_key": candidate.stable_key,
                    "value_tags": candidate.value_tags,
                    "utility": candidate.utility,
                }
                for candidate in candidates
            ],
        )
        queue_items = self._annotate_endogenous_drive_candidates(
            deliberation=deliberation.to_dict(),
            idle_window=idle_window,
            candidate_items=queue_items,
        )
        lm_reasoning_state: Dict[str, Any] = {}
        if hasattr(self._endogenous_drive_engine, "get_latest_lm_task_generation_context"):
            try:
                lm_reasoning_state = dict(
                    self._endogenous_drive_engine.get_latest_lm_task_generation_context() or {}
                )
            except Exception:
                lm_reasoning_state = {}
        cognitive_self_regulation = self._derive_cognitive_self_regulation(
            drive_history=idle_window["drive_history"],
            lm_reasoning_state=lm_reasoning_state,
            deliberation=deliberation.to_dict(),
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
            idle_window["endogenous_drive_policy"][key] = float(
                combined_self_regulation.get(key) or 0.0
            )

        if any(float(cognitive_self_regulation.get(key) or 0.0) > 0.0 for key in (
            "dynamic_candidate_throttle_boost",
            "dynamic_observation_bias_boost",
            "dynamic_truthfulness_bias_boost",
            "dynamic_learning_expansion_suppression",
        )):
            deliberation = self._endogenous_drive_engine.build_deliberation_report(
                idle_window=idle_window,
            )
            candidates = self._endogenous_drive_engine.generate_candidates(
                idle_window=idle_window,
                existing_drive_keys=self._existing_endogenous_drive_keys(),
                max_candidates=max_candidates,
            )
            queue_items = self._apply_scheduled_for_to_candidate_items(
                [
                    {
                        **candidate.to_queue_item(),
                        "stable_key": candidate.stable_key,
                        "value_tags": candidate.value_tags,
                        "utility": candidate.utility,
                    }
                    for candidate in candidates
                ],
            )
            queue_items = self._annotate_endogenous_drive_candidates(
                deliberation=deliberation.to_dict(),
                idle_window=idle_window,
                candidate_items=queue_items,
            )

        governance_channels = self._governance_channels_from_deliberation(
            deliberation.to_dict()
        )
        governance_event_stream = self._record_endogenous_governance_events(
            deliberation=deliberation.to_dict(),
            governance_channels=governance_channels,
            candidate_items=queue_items,
        )
        cognition_state = self._build_endogenous_cognition_state(
            deliberation=deliberation.to_dict(),
            governance_channels=governance_channels,
            governance_event_stream=governance_event_stream,
            self_regulation=combined_self_regulation,
            candidate_items=queue_items,
        )
        self._persist_endogenous_cognition_state(cognition_state)
        if record_activity:
            self._record_supervisor_ui_activity(
                "endogenous_drive_evaluated",
                scene="planning",
                summary=f"Endogenous drive completed a cognition evaluation over {len(candidates)} compatibility projection(s).",
                metadata={
                    "count": len(candidates),
                    "candidate_keys": [candidate.stable_key for candidate in candidates],
                    "candidates": [dict(item) for item in queue_items],
                    "deliberation": deliberation.to_dict(),
                    "cognition_state": cognition_state,
                },
            )
        return {
            "status": "evaluated",
            "enabled": self.config.service_runtime.endogenous_drive_enabled,
            "core_values": CORE_VALUES,
            "idle_window": idle_window,
            "deliberation": deliberation.to_dict(),
            "candidates": queue_items,
            "count": len(candidates),
            "drive_posture": self._drive_posture_signal_from_deliberation(
                deliberation.to_dict()
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
            snapshot["events"] = generated_events + list(snapshot.get("events") or [])
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
            "queue_hygiene_review",
            "memory_maintenance",
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
                        "Deferred before queue insertion because the endogenous drive "
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
                            "Deferred before queue insertion because observation posture "
                            f"limits endogenous queue growth to budget {candidate_budget}."
                        ),
                    }
                )

        return kept, deferred

    async def _run_endogenous_drive_cycle(self) -> Dict[str, Any]:
        if not self.config.service_runtime.endogenous_drive_enabled:
            return {"status": "disabled", "planned": 0, "tasks": []}

        evaluation = await self.evaluate_endogenous_drive({})
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
                summary="Endogenous drive found no new governance projections.",
                metadata={
                    "drive_posture": drive_posture,
                    "governance_channels": governance_channels,
                    "governance_event_stream": governance_event_stream,
                    "deferred_candidates": deferred_candidates,
                } if drive_posture else None,
            )
            return {
                "status": "idle",
                "planned": 0,
                "tasks": [],
                "idle_window": evaluation.get("idle_window"),
                "drive_posture": drive_posture,
                "governance_channels": governance_channels,
                "governance_event_stream": governance_event_stream,
                "deferred_candidates": deferred_candidates,
            }

        plan_result = await self.plan_self_evolution_task({"items": candidate_items})
        created_tasks = plan_result.get("tasks", [])
        if created_tasks:
            self._record_supervisor_ui_activity(
                "endogenous_drive_planned",
                scene="planning",
                summary=f"Endogenous drive queued {len(created_tasks)} governance projection(s).",
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
                "self_evolution_plan",
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
            "idle_window": evaluation.get("idle_window"),
            "drive_posture": drive_posture,
            "governance_channels": governance_channels,
            "governance_event_stream": governance_event_stream,
            "deferred_candidates": deferred_candidates,
        }

    def _normalize_self_evolution_decision(self, decision: Optional[str]) -> Optional[str]:
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

    def _build_self_evolution_auto_decision(
        self,
        *,
        task: SelfEvolutionTask,
        idle_window: Dict[str, Any],
        governor_mode_active: bool = False,
    ) -> tuple[str, str]:
        task_type = self._task_governance_type(task)
        task_family = self._task_runtime_family(task)
        if self._is_agent_pull_task(task):
            execution_kind = self._task_execution_kind(task)
            if execution_kind == "body_improvement":
                if self._has_pending_self_learning_prerequisite():
                    return (
                        "deferred",
                        "Body-improvement task deferred because there are still planned/approved/running self-learning tasks awaiting completion. Supervisor must let learning evidence settle before code-improvement execution is released.",
                    )
                return (
                    "approved",
                    "Agent-pull body-improvement task approved for API-A execution. AUTO baseline keeps this path directly pull -> execute -> write back.",
                )
            return (
                "approved",
                "Agent-pull self-learning task approved for API-A execution. AUTO baseline keeps this path directly pull -> execute -> write back.",
            )

        # In Governor Mode, self_learning and memory_maintenance are
        # approved without idle-window gating — the user has explicitly
        # authorised governance.  Other task families keep their
        # existing idle-window and execution-window requirements.
        if governor_mode_active:
            if task_type == "self_learning":
                return (
                    "approved",
                    "Governor Mode active: self-learning task approved without idle-window requirement. Learn-only constraints still apply.",
                )
            if task_type == "memory_maintenance":
                return (
                    "approved",
                    "Governor Mode active: memory-maintenance task approved without idle-window requirement.",
                )

        decision = (
            idle_window.get("task_family_decisions", {}).get(task_family)
            or idle_window.get("governance_task_type_decisions", {}).get(task_type)
            or idle_window["decisions"]
        )

        if decision["eligible_for_execution"]:
            if task_type == "self_learning":
                return (
                    "approved",
                    "Task approved for learn-only follow-up because the user path is idle and no conflicting workflow activity is active. Execution-window gating is not required for self-learning evidence work.",
                )
            if task_type == "memory_maintenance":
                return (
                    "approved",
                    "Task approved for memory maintenance because the system is inside the execution window and user, runtime, memory, and workflow idle requirements are satisfied.",
                )
            return (
                "approved",
                "Task approved for the next execution handoff because the system is inside the execution window and idle requirements are satisfied.",
            )
        if task_type == "self_learning":
            return (
                "deferred",
                "Task deferred because the user path or another internal workflow is still active. Self-learning follow-up stays queued until the system is idle enough for learn-only work.",
            )
        if task_type == "memory_maintenance":
            return (
                "deferred",
                "Task deferred because memory maintenance requires the execution window plus idle user, runtime, memory, and workflow facts.",
            )
        return (
            "deferred",
            "Task deferred because the execution window or idle requirements are not yet satisfied. The task remains queued for future review.",
        )

    def _has_pending_self_learning_prerequisite(self) -> bool:
        for task in self._self_evolution_queue.list_tasks():
            if self._task_governance_type(task) != "self_learning":
                continue
            if task.status not in {"planned", "approved", "running"}:
                continue
            return True
        return False

    def _is_agent_pull_task(self, task: SelfEvolutionTask) -> bool:
        execution_kind = self._task_execution_kind(task)
        return (
            self._task_governance_type(task) == "self_learning"
            or execution_kind == "body_improvement"
        )

    async def _fetch_gateway_active_cli_executor(self) -> Dict[str, Any]:
        import aiohttp

        execution_config = getattr(self.config, "execution", None)
        gateway_address = getattr(execution_config, "gateway_address", "http://127.0.0.1:6000")
        url = f"{gateway_address}/admin/body/status"
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    raise HTTPException(
                        status_code=503,
                        detail=f"Gateway active executor query failed with status {response.status}",
                    )
                payload = await response.json()
        if not isinstance(payload, dict):
            return {}
        active = payload.get("active_cli_executor")
        return dict(active or {}) if isinstance(active, dict) else {}

    async def _recover_orphaned_agent_pull_tasks(self) -> int:
        active_executor = {}
        active_session_id = ""
        try:
            active_executor = await self._fetch_gateway_active_cli_executor()
            active_session_id = str(active_executor.get("session_id") or "").strip()
        except Exception:
            active_executor = {}
            active_session_id = ""

        recovered = 0
        for task in self._self_evolution_queue.list_tasks(status="running"):
            if not self._is_agent_pull_task(task):
                continue
            metadata = dict(task.metadata or {})
            owner_session_id = str(metadata.get("owner_session_id") or "").strip()
            execution_source = str(metadata.get("execution_source") or "").strip().lower()
            if execution_source and execution_source != "cli_agent_pull":
                continue
            if owner_session_id and owner_session_id == active_session_id:
                continue
            self._update_task_status(
                task.task_id,
                status="approved",
                actor="supervisor",
                reason=(
                    "Recovered orphaned AUTO agent-pull task because its owning CLI session "
                    "is no longer the active executor."
                ),
                context={
                    "recovered": True,
                    "previous_owner_session_id": owner_session_id or None,
                    "active_cli_session_id": active_session_id or None,
                },
                event_type="recovery",
            )
            self._self_evolution_queue.update_metadata(
                task.task_id,
                metadata={
                    "recovered_from_orphaned_running": True,
                    "last_recovered_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            recovered += 1
        return recovered

    def _build_self_evolution_execution_request(
        self,
        task: SelfEvolutionTask,
        *,
        decision_id: str,
        actor: str,
        reason: str,
        decision_context: Dict[str, Any],
    ) -> Optional[SelfEvolutionExecutionRequest]:
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

        return SelfEvolutionExecutionRequest(
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
            git_lineage=SelfEvolutionGitLineage.model_validate(git_lineage),
            probe_report_ref=(
                execution.get("probe_report_ref")
                or task.evidence.get("probe_report_ref")
                or task.evidence.get("probe_report_path")
            ),
            idle_window_evidence=dict(decision_context.get("idle_window") or {}),
            governor_decision=governor_decision,
            rollback_plan=rollback_plan,
        )

    def _serialize_self_evolution_task(self, task: SelfEvolutionTask) -> Dict[str, Any]:
        payload = task.model_dump(mode="json")
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
        governance_preview: Dict[str, Any] = {}
        lm_review = latest_context.get("lm_queue_review")
        if isinstance(lm_review, dict):
            governance_preview["lm_queue_review"] = dict(lm_review)
        lm_shadow = latest_context.get("lm_queue_shadow")
        if isinstance(lm_shadow, dict):
            governance_preview["lm_queue_shadow"] = dict(lm_shadow)
        lm_priority = latest_context.get("lm_queue_priority")
        if isinstance(lm_priority, dict):
            governance_preview["lm_queue_priority"] = dict(lm_priority)
        if governance_preview:
            payload["governance_preview"] = governance_preview
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

    def _build_lm_queue_review_snapshot(
        self,
        tasks: list[SelfEvolutionTask],
        *,
        idle_window: Dict[str, Any],
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

    def _coerce_lm_queue_action(
        self,
        action: Any,
        *,
        current_status: str,
    ) -> Optional[str]:
        if not isinstance(action, str):
            return None
        normalized = self._LM_QUEUE_ACTION_TO_STATUS.get(action.strip().lower())
        if normalized is None:
            return None
        if current_status in {"completed", "failed", "cancelled"}:
            return None
        return normalized

    def _extract_lm_shadow_recommendation(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        action = str(item.get("action") or "").strip().lower()
        if action not in self._LM_QUEUE_SHADOW_ACTIONS:
            return None

        recommendation: Dict[str, Any] = {
            "action": action,
            "reason": str(item.get("reason") or "").strip()[:500],
        }
        if action == "merge":
            recommendation["merge_into"] = str(item.get("merge_into") or "").strip()[:200]
        return recommendation

    def _extract_lm_priority_recommendation(self, item: Dict[str, Any]) -> Optional[str]:
        action = str(item.get("action") or "").strip().lower()
        if action not in {"reprioritize", "reprioritise"}:
            return None
        priority = str(item.get("priority") or "").strip().lower()
        if priority not in {"low", "normal", "high"}:
            return None
        return priority

    async def _lm_review_task_queue(
        self,
        tasks: list[SelfEvolutionTask],
        *,
        idle_window: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        if not tasks:
            return {}

        try:
            from memai.model_config import resolve_mem_llm_client

            llm_client, _ = resolve_mem_llm_client(role="governance_reasoner")
            if llm_client is None:
                return {}
        except Exception:
            return {}

        queue_snapshot = self._build_lm_queue_review_snapshot(tasks, idle_window=idle_window)
        prompt = (
            "你是 VoidCube 的监督者队列治理层。你的职责不是产出新任务，"
            "而是治理当前任务列表。\n\n"
            "请基于当前 idle_window、任务列表快照和用户优先级，"
            "为每个任务给出一个结构化动作建议。你可以使用以下动作：\n"
            "- approve: 建议当前任务本轮放行\n"
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
            "5. body_improvement 只有在学习证据足够时才建议 approve\n\n"
            "6. 同一个 scheduled_for / preset_time 只能保留一个活跃任务；"
            "如果时间重叠，按先后顺序只保留一个，不能与定时队列内任务重复，其余建议 defer 或 cancel；"
            "该保留/顺延建议由监督者 LM 裁定\n\n"
            "输出 JSON 对象，格式为：\n"
            "{\n"
            '  "actions": [\n'
            '    {"task_id": "...", "action": "approve|defer|cancel|pause|retire|merge|reprioritize", "reason": "...", "merge_into": "...", "priority": "..."}\n'
            "  ]\n"
            "}\n\n"
            f"【idle_window】\n{json.dumps(idle_window, ensure_ascii=False, default=str)[:3000]}\n\n"
            f"【tasks】\n{json.dumps(queue_snapshot, ensure_ascii=False, default=str)[:5000]}"
        )

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    llm_client.complete_json,
                    system_prompt=(
                        "你是 VoidCube 的监督者身份。你管理任务列表生命周期，"
                        "但不能绕过确定性状态机。你的回答必须保守、结构化、可审计。"
                    ),
                    user_payload={"queue_review": prompt},
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
            shadow = self._extract_lm_shadow_recommendation(item)
            if shadow is not None:
                reviewed[task_id]["shadow"] = shadow
        return reviewed

    def _self_evolution_task_git_lineage(self, task: SelfEvolutionTask) -> Dict[str, Any]:
        execution = dict(task.metadata.get("execution_request") or {})
        return {
            **dict(task.evidence.get("git_lineage") or {}),
            **dict(execution.get("git_lineage") or {}),
        }

    async def list_self_evolution_tasks(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        execution_kind: Optional[str] = None,
    ):
        normalized_status = None
        if status is not None:
            normalized_status = self._normalize_self_evolution_decision(status)
            if normalized_status is None or normalized_status == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported task status filter: {status}")
        tasks = self._self_evolution_queue.list_tasks(status=normalized_status)
        if task_type:
            tasks = [t for t in tasks if self._task_governance_type(t) == str(task_type).strip()]
        if execution_kind:
            normalized_execution_kind = self._normalize_runtime_task_family(execution_kind)
            explicit_execution_kind = str(execution_kind).strip().lower()
            filtered_tasks = []
            for task in tasks:
                task_execution_kind = str(self._task_execution_kind(task) or "").strip().lower()
                serialized_execution_kind = str(
                    self._serialize_self_evolution_task(task).get("execution_kind") or ""
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
            "tasks": [self._serialize_self_evolution_task(task) for task in tasks],
            "count": len(tasks),
        }

    async def get_self_evolution_task(self, task_id: str):
        task = self._self_evolution_queue.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Self-evolution task not found: {task_id}")
        return self._serialize_self_evolution_task(task)

    async def clear_self_evolution_runtime(self, request: dict | None = None):
        del request
        tasks = list(self._self_evolution_queue.list_tasks())
        cleared_counts: Dict[str, int] = {}
        for task in tasks:
            status = str(task.status)
            cleared_counts[status] = cleared_counts.get(status, 0) + 1

        self._self_evolution_queue.clear_tasks()

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

    async def plan_self_evolution_task(self, request: dict | None = None):
        request = request or {}
        items = request.get("items")

        created = []
        if isinstance(items, list) and items:
            for item in items:
                title = str(item.get("title") or "").strip()
                if not title:
                    raise HTTPException(status_code=400, detail="Each task item must include a title.")
                request_metadata = self._request_task_metadata(item)
                task = self._self_evolution_queue.create_task(
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
                self._self_evolution_queue.create_task(
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
            "self_evolution_plan",
            metadata=self._build_self_evolution_activity_metadata(created, action="plan"),
        )
        if created:
            for task in created:
                self._record_endogenous_drive_outcome(task, event_type="planned")
            self._record_supervisor_ui_activity(
                "tasks_planned",
                scene="planning",
                summary=f"Supervisor queued {len(created)} task(s).",
                metadata=self._build_self_evolution_activity_metadata(created, action="plan"),
            )

        return {
            "status": "planned",
            "tasks": [self._serialize_self_evolution_task(task) for task in created],
            "count": len(created),
        }

    async def decide_self_evolution_task(self, task_id: str, request: dict | None = None):
        request = request or {}
        task = self._self_evolution_queue.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Self-evolution task not found: {task_id}")

        normalized = self._normalize_self_evolution_decision(request.get("decision"))
        decision_context: Dict[str, Any] = {}

        if normalized is None or normalized == "auto":
            idle_window_request = dict(request.get("idle_window") or {})
            idle_window_request.setdefault("task_family", self._task_runtime_family(task))
            task_execution_kind = self._task_execution_kind(task)
            if task_execution_kind is not None:
                idle_window_request.setdefault("execution_kind", task_execution_kind)
            idle_window = await self.evaluate_idle_window(idle_window_request)
            normalized, auto_reason = self._build_self_evolution_auto_decision(
                task=task,
                idle_window=idle_window,
                governor_mode_active=getattr(
                    getattr(self, "_service_runtime", None), "governor_mode_active", False
                ),
            )
            decision_context["idle_window"] = idle_window
            reason = str(request.get("reason") or auto_reason)
        else:
            reason = str(request.get("reason") or f"Task marked as {normalized} by supervisor decision.")
            request_context = request.get("context")
            if isinstance(request_context, dict) and request_context:
                decision_context.update(dict(request_context))

        if task.status == "cancelled":
            return {
                "status": "unchanged",
                "task": self._serialize_self_evolution_task(task),
                "reason": "Cancelled tasks are terminal and cannot be re-decided by the supervisor.",
            }

        actor = str(request.get("actor", "supervisor"))
        decision_id = str(request.get("decision_id") or uuid.uuid4())
        execution_request = None
        if normalized == "approved" and self._task_requires_execution_request(task):
            try:
                execution_request = self._build_self_evolution_execution_request(
                    task,
                    decision_id=decision_id,
                    actor=actor,
                    reason=reason,
                    decision_context=decision_context,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

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
        if isinstance(decision_metadata, dict) and decision_metadata:
            self._self_evolution_queue.update_metadata(task_id, metadata=decision_metadata)

        await self._touch_gateway_activity(
            self._planning_activity_kind_for_task(task.task_type),
            metadata=self._build_self_evolution_activity_metadata(
                [updated_task],
                action="decision",
                extra={"status": normalized},
            ),
        )
        self._record_supervisor_ui_activity(
            "task_decided",
            scene="planning",
            summary=f"Task '{updated_task.title}' was marked {normalized}.",
            metadata={
                **self._task_activity_metadata(updated_task),
                "status": normalized,
            },
        )

        return {
            "status": normalized,
            "task": self._serialize_self_evolution_task(updated_task),
        }

    async def review_self_evolution_tasks(self, request: dict | None = None):
        request = request or {}
        statuses = request.get("statuses") or ["planned", "deferred", "paused"]
        normalized_statuses = []
        for status in statuses:
            normalized = self._normalize_self_evolution_decision(str(status))
            if normalized is None or normalized == "auto":
                raise HTTPException(status_code=400, detail=f"Unsupported review status: {status}")
            normalized_statuses.append(normalized)

        idle_window_request = dict(request.get("idle_window") or {})
        idle_window = await self.evaluate_idle_window(idle_window_request)
        requested_task_family = self._normalize_runtime_task_family(
            idle_window_request.get("execution_kind") or idle_window_request.get("task_family")
        )
        requested_governance_task_type = self._normalize_runtime_task_type(requested_task_family)
        review_decision = (
            idle_window.get("task_family_decisions", {}).get(requested_task_family)
            or idle_window.get("governance_task_type_decisions", {}).get(
                requested_governance_task_type
            )
            or idle_window["decisions"]
        )
        default_review_status = (
            "approved" if review_decision["eligible_for_execution"] else "deferred"
        )

        candidate_tasks: list[SelfEvolutionTask] = []
        for task in self._self_evolution_queue.list_tasks():
            if task.status not in normalized_statuses or task.status == "cancelled":
                continue
            candidate_tasks.append(task)
        candidate_tasks.sort(key=self._task_sort_key)

        lm_queue_actions = await self._lm_review_task_queue(
            candidate_tasks,
            idle_window=idle_window,
        )
        reserved_schedule_tokens = self._build_schedule_conflict_index(
            exclude_task_ids={task.task_id for task in candidate_tasks}
        )

        reviewed = []
        reviewed_statuses = []
        for task in candidate_tasks:
            task_idle_window = idle_window
            task_family = self._task_runtime_family(task)
            if idle_window.get("task_family") != task_family:
                task_idle_window_request = dict(idle_window_request)
                task_idle_window_request["task_family"] = task_family
                task_execution_kind = self._task_execution_kind(task)
                task_idle_window_request.pop("execution_kind", None)
                if task_execution_kind is not None:
                    task_idle_window_request["execution_kind"] = task_execution_kind
                task_idle_window = await self.evaluate_idle_window(task_idle_window_request)
            target_status, default_reason = self._build_self_evolution_auto_decision(
                task=task,
                idle_window=task_idle_window,
                governor_mode_active=getattr(
                    getattr(self, "_service_runtime", None), "governor_mode_active", False
                ),
            )
            decision_context: Dict[str, Any] = {"idle_window": task_idle_window}
            lm_action = lm_queue_actions.get(task.task_id)
            reprioritized = False
            if lm_action:
                shadow_recommendation = lm_action.get("shadow")
                if isinstance(shadow_recommendation, dict):
                    decision_context["lm_queue_shadow"] = shadow_recommendation
                priority_recommendation = self._extract_lm_priority_recommendation(lm_action)
                if priority_recommendation is not None and priority_recommendation != str(task.priority):
                    task = self._self_evolution_queue.update_priority(
                        task.task_id,
                        priority=priority_recommendation,
                        actor=str(request.get("actor", "supervisor")),
                        reason=(
                            f"LM queue governance reprioritized task to {priority_recommendation}."
                        ),
                        context={
                            **decision_context,
                            "lm_queue_priority": {
                                "priority": priority_recommendation,
                                "reason": str(lm_action.get("reason") or "").strip(),
                            },
                        },
                    )
                    decision_context["lm_queue_priority"] = {
                        "priority": priority_recommendation,
                        "reason": str(lm_action.get("reason") or "").strip(),
                    }
                    reprioritized = True
                suggested_status = self._coerce_lm_queue_action(
                    lm_action.get("action"),
                    current_status=str(task.status),
                )
                if suggested_status is not None:
                    preserve_agent_pull_approval = (
                        target_status == "approved"
                        and suggested_status in {"deferred", "paused"}
                        and self._is_agent_pull_task(task)
                    )
                    if preserve_agent_pull_approval:
                        decision_context["lm_queue_shadow"] = {
                            "action": suggested_status,
                            "reason": str(lm_action.get("reason") or "").strip(),
                            "preserved_status": target_status,
                        }
                    else:
                        target_status = suggested_status
                    lm_reason = str(lm_action.get("reason") or "").strip()
                    if lm_reason:
                        default_reason = f"LM queue governance: {lm_reason}"
                    decision_context["lm_queue_review"] = {
                        "action": suggested_status,
                        "reason": lm_reason,
                    }
                elif isinstance(shadow_recommendation, dict):
                    default_reason = (
                        str(request.get("reason"))
                        or f"LM queue governance shadow recommendation recorded: "
                        f"{shadow_recommendation.get('action', 'review')}."
                    )
                elif reprioritized and not str(request.get("reason") or "").strip():
                    default_reason = (
                        f"LM queue governance reprioritized task to "
                        f"{decision_context['lm_queue_priority']['priority']}."
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
                        "Task deferred because this preset time is already occupied by "
                        f"'{occupied.title}'. Only one live task may keep the same scheduled_for."
                    )
            execution_request = None
            if target_status == "approved":
                if self._task_requires_execution_request(task):
                    decision_id = str(request.get("decision_id") or uuid.uuid4())
                    try:
                        execution_request = self._build_self_evolution_execution_request(
                            task,
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=str(request.get("reason") or default_reason),
                            decision_context={"idle_window": task_idle_window},
                        )
                    except ValueError:
                        updated = self._update_task_status(
                            task.task_id,
                            status="deferred",
                            decision_id=decision_id,
                            actor=str(request.get("actor", "supervisor")),
                            reason=(
                                "Task deferred because approved execution handoff lacks required "
                                "lineage, target, or rollback evidence."
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
            shadow_recommendation_count = 0
            shadow_action_counts: Dict[str, int] = {}
            priority_update_count = 0
            for task in reviewed:
                if not task.decision_history:
                    continue
                latest_context = dict(task.decision_history[-1].context or {})
                shadow = latest_context.get("lm_queue_shadow")
                if not isinstance(shadow, dict):
                    pass
                else:
                    shadow_recommendation_count += 1
                    action = str(shadow.get("action") or "unknown")
                    shadow_action_counts[action] = shadow_action_counts.get(action, 0) + 1
                if isinstance(latest_context.get("lm_queue_priority"), dict):
                    priority_update_count += 1
            self._record_supervisor_ui_activity(
                "tasks_reviewed",
                scene="planning",
                summary=(
                    f"Supervisor reviewed {len(reviewed)} task(s): {', '.join(unique_statuses)}."
                    + (
                        f" Shadow governance suggestions: {shadow_recommendation_count}."
                        if shadow_recommendation_count > 0
                        else ""
                    )
                    + (
                        f" Priority updates: {priority_update_count}."
                        if priority_update_count > 0
                        else ""
                    )
                ),
                metadata=self._build_self_evolution_activity_metadata(
                    reviewed,
                    action="review",
                    extra={
                        "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                        "lm_shadow_recommendations": shadow_recommendation_count,
                        "lm_shadow_action_counts": shadow_action_counts,
                        "lm_priority_updates": priority_update_count,
                    },
                ),
            )
            await self._touch_gateway_activity(
                "self_evolution_plan",
                metadata=self._build_self_evolution_activity_metadata(
                    reviewed,
                    action="review",
                    extra={
                        "status": unique_statuses[0] if len(unique_statuses) == 1 else "mixed",
                        "lm_shadow_recommendations": shadow_recommendation_count,
                        "lm_shadow_action_counts": shadow_action_counts,
                        "lm_priority_updates": priority_update_count,
                    },
                ),
            )
        else:
            unique_statuses = []

        return {
            "status": "reviewed",
            "decision": default_review_status,
            "reviewed_statuses": unique_statuses,
            "tasks": [self._serialize_self_evolution_task(task) for task in reviewed],
            "count": len(reviewed),
            "idle_window": idle_window,
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
            task = self._self_evolution_queue.create_task(
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
            created.append(self._serialize_self_evolution_task(task))

        if created:
            self._record_supervisor_ui_activity(
                "self_learning_submitted",
                scene="drive",
                summary=f"Self-learning submitted {len(created)} proposal task(s).",
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

    async def _dispatch_self_evolution_execution_request(
        self,
        task: SelfEvolutionTask,
        *,
        max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        execution_request = task.execution_request
        if execution_request is None:
            return None

        task_metadata = dict(task.metadata or {})
        if task.status == "running":
            return None

        # ── Mark running BEFORE any await to prevent double-dispatch ──
        self._update_task_status(
            task.task_id,
            status="running",
            actor="supervisor",
            reason="Execution request dispatched",
            event_type="dispatch",
        )
        self._self_evolution_queue.update_metadata(
            task.task_id,
            metadata={
                "executed_at": datetime.now(timezone.utc).isoformat(),
            },
            execution_request=execution_request,
        )

        payload = execution_request.model_dump(mode="json")
        result = await self._execution_facade.execute_self_evolution_request(payload)

        # ── Failure recovery: clear dispatched flag so the task can be ──
        # retried on the next cycle.  Only explicit error statuses trigger
        # retry; everything else (including None / unknown) is treated as
        # success to avoid false-positive retries on mock or partial results.
        result_status = result.get("status") if isinstance(result, dict) else None
        _ERROR_STATUSES = frozenset({"error", "failed", "timeout", "unreachable"})
        is_failure = isinstance(result_status, str) and result_status in _ERROR_STATUSES
        if is_failure:
            failure_count = int(task_metadata.get("execution_failure_count") or 0) + 1
            task_governance_type = self._task_governance_type(task)
            # memory_maintenance tasks are handled by the supervisor's internal
            # memory service (baseline §3.4). Agent pull paths only see
            # Agent-executable autonomous tasks, so on failure we route these
            # tasks back to deferred/failed rather than approved, keeping the
            # queue free of internal-only entries that would otherwise be
            # invisible to the Agent poll filter.
            if task_governance_type == "memory_maintenance":
                if failure_count < max_retries:
                    self._update_task_status(
                        task.task_id,
                        status="deferred",
                        actor="supervisor_memory_service",
                        reason=(
                            f"Memory-maintenance dispatch failed "
                            f"({failure_count}/{max_retries}); deferred so the "
                            f"supervisor's next cycle can re-dispatch. "
                            f"executor_status={str(result_status)[:60]}"
                        ),
                        event_type="dispatch_failure",
                    )
                else:
                    self._update_task_status(
                        task.task_id,
                        status="failed",
                        actor="supervisor_memory_service",
                        reason=(
                            f"Memory-maintenance dispatch permanently failed "
                            f"after {max_retries} retries. "
                            f"executor_status={str(result_status)[:60]}"
                        ),
                        event_type="dispatch_failure",
                    )
                self._self_evolution_queue.update_metadata(
                    task.task_id,
                    metadata={
                        "execution_failed": True,
                        "execution_failure_count": failure_count,
                        "execution_result": result,
                    },
                )
                return result
            if failure_count < max_retries:
                # Allow retry — set back to approved so it can be re-dispatched
                self._update_task_status(
                    task.task_id,
                    status="approved",
                    actor="supervisor",
                    reason=f"Execution retry {failure_count}/{max_retries}",
                    event_type="dispatch_retry",
                )
                self._self_evolution_queue.update_metadata(
                    task.task_id,
                    metadata={
                        "execution_failed": True,
                        "execution_failure_count": failure_count,
                        "execution_result": result,
                    },
                )
            else:
                # Permanent failure — keep dispatched so it's not retried
                self._self_evolution_queue.update_metadata(
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
        # internally by the memory service (not by Agent pull), so its
        # reason reflects the supervisor's internal completion. Body
        # upgrade / switch go through the executor body_lifecycle. Agent
        # pull handles Agent-executable autonomous tasks separately, so this
        # success path is the supervisor's own.
        task_governance_type = self._task_governance_type(task)
        if task_governance_type == "memory_maintenance":
            actor = "supervisor_memory_service"
            completion_reason = (
                "Memory-maintenance task completed by the supervisor's "
                "internal memory service (baseline §3.4 — Agent pull only "
                "sees Agent-executable autonomous tasks). "
                f"executor_status={str(result_status)[:60] if result_status else 'ok'}"
            )
        elif task_governance_type == "self_evolution":
            actor = "supervisor_executor"
            completion_reason = (
                "Self-evolution task completed by the supervisor's body "
                f"executor. executor_status={str(result_status)[:60] if result_status else 'ok'}"
            )
        else:
            actor = "supervisor"
            completion_reason = (
                f"Execution request completed: {str(result_status)[:100]}"
            )
        self._update_task_status(
            task.task_id,
            status="completed",
            actor=actor,
            reason=completion_reason,
            event_type="dispatch_completed",
        )
        self._self_evolution_queue.update_metadata(
            task.task_id,
            metadata={"execution_result": result},
        )
        await self._touch_gateway_activity(
            "self_evolution_execute",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
            },
        )
        self._record_supervisor_ui_activity(
            "execution_dispatched",
            scene="dispatch",
            summary=f"Execution request dispatched for '{task.title}'.",
            metadata={
                **self._task_activity_metadata(task),
                "decision_id": execution_request.decision_id,
                "source_actor": execution_request.source_actor,
                "result_status": result_status,
            },
        )
        return result

    async def _run_self_evolution_cycle(self) -> Dict[str, Any]:
        recovered_orphaned = await self._recover_orphaned_agent_pull_tasks()
        governance_consumption = self._consume_endogenous_governance_review_events()
        alignment_consumption = self._consume_endogenous_alignment_events()
        truthfulness_consumption = self._consume_endogenous_truthfulness_alerts()

        # ── Cleanup: auto-fail tasks stuck in "running" > 30 min ──
        stale_running = 0
        now = datetime.now(timezone.utc)
        for task in self._self_evolution_queue.list_tasks():
            if task.status != "running":
                continue
            started = task.metadata.get("executed_at")
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

        review_result = await self.review_self_evolution_tasks({})
        dispatched = []

        # Pass 1: dispatch tasks that were *just* approved in this review
        for task_payload in review_result.get("tasks", []):
            if task_payload.get("status") != "approved":
                continue

            task = self._self_evolution_queue.get_task(task_payload.get("task_id", ""))
            if task is None:
                continue

            gov_type = self._task_governance_type(task)
            execution_kind = self._task_execution_kind(task)
            if gov_type == "self_learning" or execution_kind == "body_improvement":
                # Agent-executable autonomous tasks are pulled by Agent via Gateway /v1/tasks API.
                continue

            if task.execution_request is None:
                continue

            result = await self._dispatch_self_evolution_execution_request(task)
            if result is not None:
                dispatched.append(
                    {
                        "task_id": task.task_id,
                        "status": result.get("status"),
                    }
                )

        # Pass 2: dispatch any previously-approved tasks that were never
        # dispatched, PLUS tasks whose previous dispatch failed and were
        # reset to approved for retry (execution_failed=True,
        # failure_count < max_retries).  Tasks in running state or
        # permanently failed are skipped here.
        dispatched_ids = {d["task_id"] for d in dispatched}
        for task in self._self_evolution_queue.list_tasks(status="approved"):
            if task.task_id in dispatched_ids:
                continue
            if task.status == "running":
                continue  # already running or permanently failed

            execution_kind = self._task_execution_kind(task)
            if self._task_governance_type(task) == "self_learning" or execution_kind == "body_improvement":
                # Agent-executable autonomous tasks are pulled by Agent via Gateway /v1/tasks API.
                # The supervisor only approves them; execution is Agent-initiated.
                continue

            if task.execution_request is None:
                continue

            result = await self._dispatch_self_evolution_execution_request(task)
            if result is not None:
                dispatched.append(
                    {"task_id": task.task_id, "status": result.get("status")}
                )

        return {
            "reviewed": review_result.get("count", 0),
            "dispatched": dispatched,
            "recovered_orphaned": recovered_orphaned,
            "governance_consumption": governance_consumption,
            "alignment_consumption": alignment_consumption,
            "truthfulness_consumption": truthfulness_consumption,
        }

    async def run_auto_cycle(self, request: dict | None = None) -> Dict[str, Any]:
        """Execute one full autonomous cycle: drive → plan → review → dispatch.

        This is the single-endpoint entry point for ``/auto``.  It runs the
        same pipeline as the periodic background loops, but triggered
        synchronously on demand so the user sees immediate results.

        Returns a summary of every phase so the CLI can report what happened.
        """
        request = request or {}
        focus = str(request.get("focus") or "").strip()
        phases: Dict[str, Any] = {}

        # ── Phase 1: Endogenous drive → generate & queue candidates ──
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

        # ── Phase 2: Self-evolution review → approve & dispatch ──
        try:
            cycle_result = await self._run_self_evolution_cycle()
            phases["review"] = {
                "reviewed": cycle_result.get("reviewed", 0),
                "dispatched": [
                    dict(item) if isinstance(item, dict) else {"task_id": str(item)}
                    for item in cycle_result.get("dispatched", [])
                ],
                "governance_consumption": dict(cycle_result.get("governance_consumption") or {}),
                "alignment_consumption": dict(cycle_result.get("alignment_consumption") or {}),
                "truthfulness_consumption": dict(cycle_result.get("truthfulness_consumption") or {}),
            }
            now = datetime.now(timezone.utc)
            self._service_runtime.last_review_at = now
            self._service_runtime.next_review_at = now + timedelta(
                seconds=self.config.service_runtime.self_evolution_review_interval
            )
        except Exception as exc:
            phases["review"] = {"status": "error", "error": str(exc)}

        # ── Phase 3: Record supervisor UI activity ──
        total_dispatched = len(phases.get("review", {}).get("dispatched", []))
        total_planned = phases.get("drive", {}).get("planned", 0)
        self._record_supervisor_ui_activity(
            "auto_cycle_completed",
            scene="dispatch" if total_dispatched > 0 else "planning",
            summary=(
                f"Auto cycle complete: {total_planned} planned, "
                f"{total_dispatched} dispatched for execution."
            ),
            metadata={
                "phases": {
                    k: {sk: sv for sk, sv in v.items() if sk != "task_ids"}
                    for k, v in phases.items()
                },
                "total_planned": total_planned,
                "total_dispatched": total_dispatched,
                "focus": focus or None,
            },
        )

        return {
            "status": "completed",
            "phases": phases,
            "summary": {
                "planned": total_planned,
                "dispatched": total_dispatched,
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

        for task in self._self_evolution_queue.list_tasks(status="completed"):
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
        execution_request: Optional[SelfEvolutionExecutionRequest] = None,
        event_type: str = "status_update",
    ) -> SelfEvolutionTask:
        task = self._self_evolution_queue.update_status(
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
