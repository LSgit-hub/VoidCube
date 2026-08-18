"""Mutation service for endogenous strategy-memory history."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)


class EndogenousStrategyMemoryService:
    """Own normalized strategy-memory mutations without persistence side effects."""

    def ensure_normalized(self, history: Dict[str, Any]) -> Dict[str, Any]:
        strategy_memory = normalize_endogenous_strategy_memory(
            history.get("strategy_memory")
        )
        history["strategy_memory"] = strategy_memory
        return strategy_memory

    def agenda_topic_bucket(
        self,
        history: Dict[str, Any],
        topic: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = self.ensure_normalized(history)
        topic_name = str(topic or "").strip().lower() or "unknown"
        return strategy_memory.setdefault("agenda_topic_stats", {}).setdefault(
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

    def record_agenda(
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
        bucket = self.agenda_topic_bucket(history, topic)
        normalized_status = str(status or "active").strip().lower() or "active"
        bucket["seen"] = max(0, int(bucket.get("seen") or 0)) + 1
        if normalized_status == "active":
            bucket["active_cycles"] = max(0, int(bucket.get("active_cycles") or 0)) + 1
        elif normalized_status == "resolved":
            bucket["resolved"] = max(0, int(bucket.get("resolved") or 0)) + 1
            bucket["last_resolved_at"] = recorded_at
        elif normalized_status == "dragging":
            bucket["dragging"] = max(0, int(bucket.get("dragging") or 0)) + 1
        bucket["last_priority"] = round(self._clamp_ratio(priority), 4)
        bucket["last_confidence"] = round(self._clamp_ratio(confidence), 4)
        bucket["last_status"] = normalized_status
        bucket["last_seen_at"] = recorded_at
        bucket["last_context_key"] = (
            str(context_key or "").strip().lower() or bucket.get("last_context_key")
        )

    def observation_target_bucket(
        self,
        history: Dict[str, Any],
        target: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = self.ensure_normalized(history)
        target_name = str(target or "").strip().lower() or "unknown"
        return strategy_memory.setdefault("observation_target_stats", {}).setdefault(
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

    def record_observation(
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
        bucket = self.observation_target_bucket(history, target)
        normalized_status = str(status or "recommended").strip().lower() or "recommended"
        bucket["seen"] = max(0, int(bucket.get("seen") or 0)) + 1
        if normalized_status == "recommended":
            bucket["recommended"] = max(0, int(bucket.get("recommended") or 0)) + 1
        elif normalized_status == "resolved":
            bucket["resolved"] = max(0, int(bucket.get("resolved") or 0)) + 1
            bucket["last_resolved_at"] = recorded_at
        elif normalized_status == "stalled":
            bucket["stalled"] = max(0, int(bucket.get("stalled") or 0)) + 1
        bucket["last_priority"] = round(self._clamp_ratio(priority), 4)
        bucket["last_risk"] = round(self._clamp_ratio(risk), 4)
        bucket["last_status"] = normalized_status
        bucket["last_seen_at"] = recorded_at
        bucket["last_context_key"] = (
            str(context_key or "").strip().lower() or bucket.get("last_context_key")
        )

    def resolve_cleared_observation_targets(
        self,
        history: Dict[str, Any],
        *,
        active_targets: set[str],
        context_key: Optional[str],
        recorded_at: str,
    ) -> bool:
        strategy_memory = self.ensure_normalized(history)
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
            self.record_observation(
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

    def meta_governance_bucket(
        self,
        history: Dict[str, Any],
        mode: Optional[str],
    ) -> Dict[str, Any]:
        strategy_memory = self.ensure_normalized(history)
        mode_name = str(mode or "").strip().lower() or "unknown"
        return strategy_memory.setdefault("meta_governance_stats", {}).setdefault(
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

    def record_meta_governance(
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
        bucket = self.meta_governance_bucket(history, mode)
        normalized_status = str(status or "active").strip().lower() or "active"
        bucket["seen"] = max(0, int(bucket.get("seen") or 0)) + 1
        if normalized_status == "active":
            bucket["active_cycles"] = max(0, int(bucket.get("active_cycles") or 0)) + 1
        elif normalized_status == "resolved":
            bucket["resolved"] = max(0, int(bucket.get("resolved") or 0)) + 1
            bucket["last_resolved_at"] = recorded_at
        elif normalized_status == "stalled":
            bucket["stalled"] = max(0, int(bucket.get("stalled") or 0)) + 1
        bucket["last_priority"] = round(self._clamp_ratio(priority), 4)
        bucket["last_confidence"] = round(self._clamp_ratio(confidence), 4)
        bucket["last_status"] = normalized_status
        bucket["last_seen_at"] = recorded_at
        bucket["last_context_key"] = (
            str(context_key or "").strip().lower() or bucket.get("last_context_key")
        )

    def focus_bucket(
        self,
        history: Dict[str, Any],
        focus: Optional[str],
        context_key: Optional[str] = None,
    ) -> Dict[str, int]:
        strategy_memory = self.ensure_normalized(history)
        focus_name = str(focus or "").strip().lower() or "unknown"
        bucket = strategy_memory.setdefault("focus_stats", {}).setdefault(
            focus_name,
            {"judged": 0, "completed": 0, "failed": 0, "dragging": 0},
        )
        normalized_context = str(context_key or "").strip().lower()
        if not normalized_context:
            return bucket
        contextual_bucket = strategy_memory.setdefault(
            "contextual_focus_stats", {}
        ).setdefault(normalized_context, {})
        return contextual_bucket.setdefault(
            focus_name,
            {"judged": 0, "completed": 0, "failed": 0, "dragging": 0},
        )

    @staticmethod
    def _clamp_ratio(value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0


__all__ = ["EndogenousStrategyMemoryService"]
