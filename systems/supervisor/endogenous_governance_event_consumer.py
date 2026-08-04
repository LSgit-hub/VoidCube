"""Consume endogenous governance events and persist regulation changes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional


LoadSnapshot = Callable[[], Dict[str, Any]]
PersistSnapshot = Callable[[Dict[str, Any]], None]
MutateRegulation = Callable[[Dict[str, Any], Dict[str, Any]], None]
Now = Callable[[], datetime]


class EndogenousGovernanceEventConsumer:
    """Own event consumption and the resulting self-regulation write-back."""

    def __init__(
        self,
        *,
        load_events: LoadSnapshot,
        persist_events: PersistSnapshot,
        load_regulation: LoadSnapshot,
        persist_regulation: PersistSnapshot,
        now: Optional[Now] = None,
    ) -> None:
        self._load_events = load_events
        self._persist_events = persist_events
        self._load_regulation = load_regulation
        self._persist_regulation = persist_regulation
        self._now = now or (lambda: datetime.now(timezone.utc))

    def consume_governance_review_requests(self) -> Dict[str, Any]:
        return self._consume_events(
            event_type="governance_review_request",
            consumed_action="trigger_review_pass",
        )

    def consume_alignment_requests(self) -> Dict[str, Any]:
        return self._consume_regulation_events(
            event_type="autonomy_alignment_request",
            consumed_action="increase_self_regulation",
            mutate_regulation=self._apply_alignment_regulation,
        )

    def consume_truthfulness_alerts(self) -> Dict[str, Any]:
        return self._consume_regulation_events(
            event_type="truthfulness_alert",
            consumed_action="increase_truthfulness_correction",
            mutate_regulation=self._apply_truthfulness_regulation,
        )

    def _consume_events(
        self,
        *,
        event_type: str,
        consumed_action: str,
    ) -> Dict[str, Any]:
        snapshot = self._load_events()
        events = list(snapshot.get("events") or [])
        consumed: list[Dict[str, Any]] = []
        updated_events: list[Dict[str, Any]] = []

        for item in events:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if (
                str(row.get("event_type") or "").strip() == event_type
                and not row.get("consumed_at")
            ):
                row["consumed_at"] = self._now().isoformat()
                row["consumed_action"] = consumed_action
                consumed.append(self._consumed_payload(row))
            updated_events.append(row)

        if consumed:
            snapshot["events"] = updated_events
            self._persist_events(snapshot)
        return {
            "consumed": consumed,
            "count": len(consumed),
            "events": updated_events[:36],
        }

    def _consume_regulation_events(
        self,
        *,
        event_type: str,
        consumed_action: str,
        mutate_regulation: MutateRegulation,
    ) -> Dict[str, Any]:
        events_snapshot = self._load_events()
        regulation_snapshot = self._load_regulation()
        events = list(events_snapshot.get("events") or [])
        consumed: list[Dict[str, Any]] = []
        updated_events: list[Dict[str, Any]] = []
        applied = False

        for item in events:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if (
                str(row.get("event_type") or "").strip() == event_type
                and not row.get("consumed_at")
            ):
                row["consumed_at"] = self._now().isoformat()
                row["consumed_action"] = consumed_action
                mutate_regulation(regulation_snapshot, row)
                consumed.append(self._consumed_payload(row))
                applied = True
            updated_events.append(row)

        if consumed:
            events_snapshot["events"] = updated_events
            self._persist_events(events_snapshot)
        if applied:
            self._persist_regulation(regulation_snapshot)

        return {
            "consumed": consumed,
            "count": len(consumed),
            "regulation": dict(regulation_snapshot),
            "events": updated_events[:36],
        }

    @staticmethod
    def _apply_alignment_regulation(
        regulation: Dict[str, Any],
        row: Dict[str, Any],
    ) -> None:
        regulation["dynamic_candidate_throttle_boost"] = min(
            0.35,
            float(regulation.get("dynamic_candidate_throttle_boost") or 0.0) + 0.08,
        )
        regulation["dynamic_observation_bias_boost"] = min(
            0.30,
            float(regulation.get("dynamic_observation_bias_boost") or 0.0) + 0.06,
        )
        regulation["last_reason"] = row.get("message") or row.get("rationale")

    @staticmethod
    def _apply_truthfulness_regulation(
        regulation: Dict[str, Any],
        row: Dict[str, Any],
    ) -> None:
        regulation["dynamic_truthfulness_bias_boost"] = min(
            0.30,
            float(regulation.get("dynamic_truthfulness_bias_boost") or 0.0) + 0.08,
        )
        regulation["dynamic_learning_expansion_suppression"] = min(
            0.25,
            float(regulation.get("dynamic_learning_expansion_suppression") or 0.0) + 0.06,
        )
        regulation["last_reason"] = row.get("message") or row.get("rationale")

    @staticmethod
    def _consumed_payload(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "event_id": row.get("event_id"),
            "event_type": row.get("event_type"),
            "context_key": row.get("context_key"),
            "message": row.get("message"),
        }


__all__ = ["EndogenousGovernanceEventConsumer"]
