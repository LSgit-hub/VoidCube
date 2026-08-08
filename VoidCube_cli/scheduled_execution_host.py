"""Narrow execution owner for scheduled and companion-media tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from VoidCube_cli.background_task_runtime import (
    BackgroundTaskRuntime,
    BackgroundTaskSnapshot,
    BackgroundTaskState,
    BackgroundTaskPorts,
)


@dataclass(frozen=True, slots=True)
class ScheduledExecutionSnapshot:
    active_tasks: tuple[BackgroundTaskSnapshot, ...]


class ScheduledExecutionHost:
    """Own scheduled background workers without exposing CLI or TUI APIs."""

    def __init__(
        self,
        *,
        ensure_credentials: Callable[[], bool],
        resolve_agent_route: Callable[[str], dict[str, Any]],
        create_agent: Callable[[dict[str, Any], str, dict[str, Any], bool], Any],
        completion_outcome: Callable[[dict[str, Any] | None], tuple[bool, str, str]],
        invalidate: Callable[[], None],
    ) -> None:
        self._state = BackgroundTaskState()
        self._runtime = BackgroundTaskRuntime(
            BackgroundTaskPorts(
                state=self._state,
                ensure_credentials=ensure_credentials,
                resolve_agent_route=resolve_agent_route,
                create_agent=create_agent,
                announce_start=lambda *_args: None,
                render_completion=lambda *_args: None,
                set_thinking=lambda _text: None,
                invalidate=invalidate,
                bell_on_complete=lambda: None,
                completion_outcome=completion_outcome,
            )
        )

    def start(self, prompt: str, **kwargs: Any) -> bool:
        return self._runtime.start(prompt, **kwargs)

    def snapshot(self) -> ScheduledExecutionSnapshot:
        return ScheduledExecutionSnapshot(
            active_tasks=self._state.active_snapshots(),
        )


__all__ = ["ScheduledExecutionHost", "ScheduledExecutionSnapshot"]
