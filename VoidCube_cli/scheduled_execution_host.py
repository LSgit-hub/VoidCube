"""Narrow execution owner for scheduled and companion-delegated tasks."""

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
        resolve_agent_route: Callable[[str, str], dict[str, Any]],
        create_agent: Callable[[dict[str, Any], str, dict[str, Any], bool], Any],
        completion_outcome: Callable[[dict[str, Any] | None], tuple[bool, str, str]],
        announce_start: Callable[[int, str, str, str], None],
        render_completion: Callable[[bool, str, str, int, str, str | None, str], None],
        invalidate: Callable[[], None],
    ) -> None:
        self._state = BackgroundTaskState()
        self._resolve_agent_route = resolve_agent_route
        self._runtime = BackgroundTaskRuntime(
            BackgroundTaskPorts(
                state=self._state,
                ensure_credentials=ensure_credentials,
                resolve_agent_route=lambda prompt: resolve_agent_route(prompt, ""),
                create_agent=create_agent,
                announce_start=announce_start,
                render_completion=render_completion,
                set_thinking=lambda _text: None,
                invalidate=invalidate,
                bell_on_complete=lambda: None,
                completion_outcome=completion_outcome,
            )
        )

    def start(self, prompt: str, **kwargs: Any) -> bool:
        worker_role = str(kwargs.pop("worker_role", "") or "").strip().lower()
        route = self._resolve_agent_route(prompt, worker_role)
        worker_label = str(route.get("worker_label") or "").strip()
        task_label = str(kwargs.get("task_label") or "")
        if worker_label and task_label.startswith("API-B 指令 · "):
            title = task_label.removeprefix("API-B 指令 · ")
            kwargs["task_label"] = f"API-B 指令 · {worker_label} · {title}"
        elif worker_label and task_label.startswith("媒体请求 · "):
            title = task_label.removeprefix("媒体请求 · ")
            kwargs["task_label"] = f"媒体请求 · {worker_label} · {title}"
        return self._runtime.start(prompt, route_override=route, **kwargs)

    def snapshot(self) -> ScheduledExecutionSnapshot:
        return ScheduledExecutionSnapshot(
            active_tasks=self._state.active_snapshots(),
        )


__all__ = ["ScheduledExecutionHost", "ScheduledExecutionSnapshot"]
