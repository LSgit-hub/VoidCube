"""Project subagent manager state into a compact display snapshot."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliSubagentObservabilityPorts:
    """Subagent managers supplied by the CLI host."""

    display_managers: Callable[[], Sequence[Any]]


class CliSubagentObservabilityRuntime:
    """Own subagent task filtering and compact display projection."""

    _TERMINAL_STATUSES = frozenset(
        {"completed", "failed", "interrupted", "cancelled"}
    )

    def __init__(self, ports: CliSubagentObservabilityPorts) -> None:
        self.ports = ports

    def snapshot(self) -> dict[str, Any]:
        result = self._empty_snapshot()
        managers = self.ports.display_managers()
        if not managers:
            return result

        foreground_tasks: list[Any] = []
        background_tasks: list[Any] = []
        try:
            for manager in managers:
                foreground_tasks.extend(
                    list(manager.list_tasks(include_background=False) or [])
                )
                background_tasks.extend(list(manager.list_background_tasks() or []))
        except Exception:
            return result

        active_foreground = [
            task for task in foreground_tasks if self._is_active(task)
        ]
        active_background = [
            task for task in background_tasks if self._is_active(task)
        ]
        if not active_foreground and not active_background:
            return result

        active_foreground.sort(key=lambda task: getattr(task, "task_index", 0))
        active_background.sort(key=lambda task: getattr(task, "task_index", 0))
        focus_task = active_foreground[0] if active_foreground else active_background[0]
        focus_tool = str(getattr(focus_task, "current_tool", "") or "").strip()
        preview_source = (
            focus_tool
            or str(getattr(focus_task, "current_tool_preview", "") or "").strip()
            or str(getattr(focus_task, "current_thinking", "") or "").strip()
            or str(getattr(focus_task, "goal_preview", "") or "").strip()
            or str(getattr(focus_task, "goal", "") or "").strip()
        )
        foreground_count = len(active_foreground)
        background_count = len(active_background)
        result.update(
            {
                "active": True,
                "foreground_count": foreground_count,
                "background_count": background_count,
                "total_count": foreground_count + background_count,
                "counts_label": (
                    f"{foreground_count}+{background_count}"
                    if background_count > 0
                    else str(foreground_count)
                ),
                "focus_task_id": str(getattr(focus_task, "task_id", "") or "").strip(),
                "focus_tool": focus_tool,
                "focus_preview": self._truncate(preview_source, 32),
                "compact_preview": self._truncate(preview_source, 18),
            }
        )
        return result

    @classmethod
    def _is_active(cls, task: Any) -> bool:
        status = getattr(task, "status", "")
        value = getattr(status, "value", status)
        return str(value or "").strip().lower() not in cls._TERMINAL_STATUSES

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        value = " ".join(str(text or "").strip().split())
        if len(value) <= limit:
            return value
        if limit <= 3:
            return value[:limit]
        return value[: limit - 3] + "..."

    @staticmethod
    def _empty_snapshot() -> dict[str, Any]:
        return {
            "active": False,
            "foreground_count": 0,
            "background_count": 0,
            "total_count": 0,
            "counts_label": "0",
            "focus_task_id": "",
            "focus_tool": "",
            "focus_preview": "",
            "compact_preview": "",
        }
