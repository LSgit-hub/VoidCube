"""Persistence boundary for endogenous drive history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from systems.supervisor.endogenous_state_repository import EndogenousStateRepository
from systems.supervisor.endogenous_strategy_memory import (
    normalize_endogenous_strategy_memory,
)


class EndogenousDriveHistoryPersistenceService:
    """Own the drive-history snapshot lifecycle without strategy decisions."""

    def __init__(
        self,
        repository: EndogenousStateRepository,
        *,
        history_limit: int = 240,
    ) -> None:
        self._repository = repository
        self._history_limit = max(1, int(history_limit))

    def default_snapshot(self) -> Dict[str, Any]:
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

    def load(self) -> Dict[str, Any]:
        raw = self._repository.read_object(self._repository.paths.drive_history)
        if raw is None:
            return self.default_snapshot()

        snapshot = self.default_snapshot()
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
        return self.trim(snapshot)

    def trim(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        trimmed = dict(snapshot or {})
        trimmed["version"] = 1
        trimmed["judgements"] = list(trimmed.get("judgements") or [])[
            : self._history_limit
        ]
        trimmed["outcomes"] = list(trimmed.get("outcomes") or [])[
            : self._history_limit
        ]
        trimmed["strategy_memory"] = normalize_endogenous_strategy_memory(
            trimmed.get("strategy_memory")
        )
        return trimmed

    def persist(self, snapshot: Dict[str, Any]) -> None:
        payload = self.trim(snapshot)
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._repository.write_object(
            self._repository.paths.drive_history,
            payload,
        )


__all__ = ["EndogenousDriveHistoryPersistenceService"]
