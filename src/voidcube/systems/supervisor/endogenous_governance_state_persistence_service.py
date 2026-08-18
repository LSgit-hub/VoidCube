"""Persistence lifecycle for endogenous governance state snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict

from .endogenous_state_repository import EndogenousStateRepository


class EndogenousGovernanceStatePersistenceService:
    """Own governance-event, cognition, and self-regulation snapshot I/O."""

    _ENDOGENOUS_GOVERNANCE_EVENT_LIMIT = 240

    def __init__(
        self,
        repository: EndogenousStateRepository,
        *,
        endogenous_drive_enabled: Callable[[], bool],
    ) -> None:
        self._repository = repository
        self._endogenous_drive_enabled = endogenous_drive_enabled

    def default_governance_events(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "events": [],
        }

    def default_cognition_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "state": {
                "status": "uninitialized",
                "enabled": bool(self._endogenous_drive_enabled()),
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

    def default_self_regulation(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "updated_at": None,
            "dynamic_candidate_throttle_boost": 0.0,
            "dynamic_observation_bias_boost": 0.0,
            "dynamic_truthfulness_bias_boost": 0.0,
            "dynamic_learning_expansion_suppression": 0.0,
            "last_reason": None,
        }

    def load_governance_events(self) -> Dict[str, Any]:
        raw = self._repository.read_object(
            self._repository.paths.governance_events
        )
        if raw is None:
            return self.default_governance_events()
        snapshot = self.default_governance_events()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["events"] = [
            dict(item)
            for item in list(raw.get("events") or [])
            if isinstance(item, dict)
        ]
        return self._trim_governance_events(snapshot)

    def load_cognition_state(self) -> Dict[str, Any]:
        raw = self._repository.read_object(
            self._repository.paths.cognition_state
        )
        if raw is None:
            return self.default_cognition_state()
        snapshot = self.default_cognition_state()
        snapshot["updated_at"] = raw.get("updated_at")
        snapshot["state"] = dict(raw.get("state") or {})
        return snapshot

    def load_self_regulation(self) -> Dict[str, Any]:
        raw = self._repository.read_object(
            self._repository.paths.self_regulation
        )
        if raw is None:
            return self.default_self_regulation()
        snapshot = self.default_self_regulation()
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
        return self._decay_self_regulation(snapshot)

    def _decay_self_regulation(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
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
        self.persist_self_regulation(decayed)
        return decayed

    def _trim_governance_events(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
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
            semantic_key = self.semantic_event_key(row)
            if semantic_key:
                if semantic_key in seen_unconsumed_event_keys:
                    continue
                seen_unconsumed_event_keys.add(semantic_key)
            events.append(row)
            if len(events) >= self._ENDOGENOUS_GOVERNANCE_EVENT_LIMIT:
                break
        trimmed["events"] = events
        return trimmed

    def semantic_event_key(
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

    def persist_governance_events(self, snapshot: Dict[str, Any]) -> None:
        payload = self._trim_governance_events(snapshot)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._repository.write_object(
            self._repository.paths.governance_events, payload
        )

    def persist_cognition_state(self, state: Dict[str, Any]) -> None:
        payload = self.default_cognition_state()
        payload["state"] = dict(state or {})
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._repository.write_object(
            self._repository.paths.cognition_state, payload
        )

    def persist_self_regulation(self, snapshot: Dict[str, Any]) -> None:
        payload = dict(snapshot or {})
        payload["version"] = 1
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._repository.write_object(
            self._repository.paths.self_regulation, payload
        )

__all__ = ["EndogenousGovernanceStatePersistenceService"]
