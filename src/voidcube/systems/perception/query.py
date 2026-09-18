"""Read-only query facade for Agent/Supervisor integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .context import build_perception_context
from .runtime import PerceptionRuntime
from .timeline_store import TimelineStore


class PerceptionQueryService:
    """Expose current scene and text-only timeline context without model calls."""

    def __init__(self, runtime: PerceptionRuntime, *, store: TimelineStore | None = None) -> None:
        self.runtime = runtime
        self.store = store or runtime.store

    def current_scene(self) -> dict[str, Any]:
        return self.runtime.loop.buffer.scene_state().as_dict()

    def timeline(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        return [segment.as_dict() for segment in self.store.query(start_at=start_at, end_at=end_at, limit=limit)]

    def context(
        self,
        *,
        level: str,
        user_query: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 24,
    ) -> dict[str, Any]:
        segments = []
        if start_at is not None and end_at is not None and self.store is not None:
            segments = self.store.query(start_at=start_at, end_at=end_at, limit=limit)
        return build_perception_context(
            level=level,
            user_query=user_query,
            scene=self.runtime.loop.buffer.scene_state(),
            segments=segments,
            max_segments=limit,
        )


__all__ = ["PerceptionQueryService"]
