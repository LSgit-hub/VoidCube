"""Subagent task command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .....application.scheduling.background_task_runtime import BackgroundTaskSnapshot
from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class TaskMoveResult:
    found: bool
    moved: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class TasksCommandPorts:
    has_display_managers: Callable[[], bool]
    render_subagent_tasks: Callable[[], str]
    render_subagent_task: Callable[[str], str | None]
    render_subagent_task_log: Callable[[str], str | None]
    background_tasks: Callable[[], Sequence[BackgroundTaskSnapshot]]
    now: Callable[[], float]
    move_to_background: Callable[[str], TaskMoveResult]
    bring_to_foreground: Callable[[str], TaskMoveResult]
    render_output: Callable[[str], None]
    emit: Callable[[str], None]
    invalidate: Callable[[], None]


def handle_tasks_command(request: ParsedCliCommand, *, ports: TasksCommandPorts) -> None:
    """Show compact task state, one task's detail, or apply a lane change."""
    parts = request.arguments.split()
    action = parts[0].lower() if parts else "show"
    task_ref = parts[1].strip() if len(parts) >= 2 else ""

    if action in {"show", "list"}:
        if task_ref:
            _render_task_detail(task_ref, ports)
        else:
            _render_tasks(ports)
        return

    if action == "log":
        if task_ref:
            _render_task_log(task_ref, ports)
        else:
            ports.emit("  用法：/tasks <task> log")
        return

    if len(parts) >= 2 and parts[1].lower() == "log":
        _render_task_log(parts[0], ports)
        return

    if action not in {"bg", "background", "fg", "foreground"}:
        _render_task_detail(parts[0], ports)
        return

    if not ports.has_display_managers():
        ports.emit("  当前没有可用的子代理显示管理器。")
        return

    if not task_ref:
        ports.emit(
            "  API-A 会自动管理子代理；仅在调试操作时指定任务。"
        )
        ports.emit("         /tasks bg <task-id|index>")
        ports.emit("         /tasks fg <task-id|index>")
        return

    background = action in {"bg", "background"}
    result = (ports.move_to_background if background else ports.bring_to_foreground)(task_ref)
    if not result.found:
        ports.emit(f"  未知的子代理任务：{task_ref}")
        return
    if result.error:
        direction = "将子代理任务转入后台" if background else "将子代理任务调回前台"
        ports.emit(f"  {direction}失败：{result.error}")
        return
    if not result.moved:
        verb = "后台" if background else "前台"
        ports.emit(f"  无法将子代理任务切换到{verb}：{task_ref}")
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
        ports.render_output("当前没有运行中的子代理或后台任务。")
        return

    now = ports.now()
    lines = ["CLI 后台任务", ""]
    for task in tasks:
        label = f"#{task.task_num}" if task.task_num else task.task_id
        preview = task.prompt_preview or task.task_id
        elapsed = max(0.0, now - task.started_at) if task.started_at else 0.0
        lines.append(f"  * {label} {preview}")
        lines.append(f"    id={task.task_id} thread={task.thread_name} elapsed={elapsed:.1f}s")
    ports.render_output("\n".join(lines))


def _render_task_detail(task_ref: str, ports: TasksCommandPorts) -> None:
    if not ports.has_display_managers():
        ports.emit("  当前没有可查看的子代理任务。")
        return
    try:
        detail = ports.render_subagent_task(task_ref)
    except Exception as exc:
        ports.emit(f"  Failed to render subagent task: {exc}")
        return
    if detail is None:
        ports.emit(f"  未知的子代理任务：{task_ref}")
        return
    ports.render_output(detail)


def _render_task_log(task_ref: str, ports: TasksCommandPorts) -> None:
    if not ports.has_display_managers():
        ports.emit("  当前没有可查看的子代理任务。")
        return
    try:
        log = ports.render_subagent_task_log(task_ref)
    except Exception as exc:
        ports.emit(f"  Failed to render subagent task log: {exc}")
        return
    if log is None:
        ports.emit(f"  未知的子代理任务：{task_ref}")
        return
    ports.render_output(log)
