"""Subagent task command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from VoidCube_cli.background_task_runtime import BackgroundTaskSnapshot
from VoidCube_cli.command_router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class TaskMoveResult:
    found: bool
    moved: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class TasksCommandPorts:
    has_display_managers: Callable[[], bool]
    render_subagent_tasks: Callable[[], str]
    background_tasks: Callable[[], Sequence[BackgroundTaskSnapshot]]
    now: Callable[[], float]
    move_to_background: Callable[[str], TaskMoveResult]
    bring_to_foreground: Callable[[str], TaskMoveResult]
    render_output: Callable[[str], None]
    emit: Callable[[str], None]
    invalidate: Callable[[], None]


def handle_tasks_command(request: ParsedCliCommand, *, ports: TasksCommandPorts) -> None:
    """Show subagent tasks or apply an explicit advanced-debug lane change."""
    parts = request.arguments.split()
    action = parts[0].lower() if parts else "show"
    task_ref = parts[1].strip() if len(parts) >= 2 else ""

    if action in {"show", "list"}:
        _render_tasks(ports)
        return

    if action not in {"bg", "background", "fg", "foreground"}:
        _render_usage(ports)
        return

    if not ports.has_display_managers():
        ports.emit("  No active subagent display is available right now.")
        return

    if not task_ref:
        ports.emit(
            "  API-A manages subagents automatically; specify a task only for advanced debug actions."
        )
        ports.emit("         /tasks bg <task-id|index>")
        ports.emit("         /tasks fg <task-id|index>")
        return

    background = action in {"bg", "background"}
    result = (ports.move_to_background if background else ports.bring_to_foreground)(task_ref)
    if not result.found:
        ports.emit(f"  Unknown subagent task: {task_ref}")
        return
    if result.error:
        direction = "send subagent task to background" if background else "bring subagent task to foreground"
        ports.emit(f"  Failed to {direction}: {result.error}")
        return
    if not result.moved:
        verb = "background" if background else "foreground"
        ports.emit(f"  Could not {verb} subagent task: {task_ref}")
        return
    ports.invalidate()


def _render_tasks(ports: TasksCommandPorts) -> None:
    if ports.has_display_managers():
        try:
            ports.render_output(ports.render_subagent_tasks())
        except Exception as exc:
            ports.emit(f"  Failed to render subagent tasks: {exc}")
        return

    tasks = ports.background_tasks()
    if not tasks:
        ports.render_output("No active subagent or background tasks.")
        return

    now = ports.now()
    lines = ["CLI Background Tasks", ""]
    for task in tasks:
        label = f"#{task.task_num}" if task.task_num else task.task_id
        preview = task.prompt_preview or task.task_id
        elapsed = max(0.0, now - task.started_at) if task.started_at else 0.0
        lines.append(f"  * {label} {preview}")
        lines.append(f"    id={task.task_id} thread={task.thread_name} elapsed={elapsed:.1f}s")
    ports.render_output("\n".join(lines))


def _render_usage(ports: TasksCommandPorts) -> None:
    ports.emit("  Usage: /tasks")
    ports.emit("  API-A manages subagents automatically; bg/fg are advanced debug actions.")
    ports.emit("         /tasks bg <task-id|index>")
    ports.emit("         /tasks fg <task-id|index>")
