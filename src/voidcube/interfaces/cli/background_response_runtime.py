"""Render completion output for an isolated background CLI task."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich import box as rich_box
from rich.panel import Panel


@dataclass(frozen=True, slots=True)
class CliBackgroundResponsePorts:
    """Terminal presentation operations supplied by the CLI host."""

    invalidate: Callable[[], None]
    sleep: Callable[[float], None]
    emit_blank_line: Callable[[], None]
    emit: Callable[[str], None]
    create_console: Callable[[], Any]
    rich_text_from_ansi: Callable[[str], Any]


class CliBackgroundResponseRuntime:
    """Own background completion presentation without CLI state access."""

    def __init__(self, ports: CliBackgroundResponsePorts) -> None:
        self.ports = ports

    def render(
        self,
        success: bool,
        response: str,
        error: str,
        task_num: int,
        task_label: str,
        response_title: str | None,
        prompt: str,
    ) -> None:
        self.ports.invalidate()
        self.ports.sleep(0.05)
        self.ports.emit_blank_line()
        console = self.ports.create_console()
        console.print("[#34D399]" + "~" * 40 + "[/]")
        display_task_label = {
            "Background task": "后台任务",
            "Job": "任务",
        }.get(task_label, task_label)
        if success:
            self.ports.emit(f"  ✅ {display_task_label} #{task_num} 已完成")
        else:
            self.ports.emit(f"  ❌ {display_task_label} #{task_num} 失败：{error}")
        preview = prompt[:60] + ("..." if len(prompt) > 60 else "")
        self.ports.emit(f'  提示词："{preview}"')
        console.print("[#34D399]" + "~" * 40 + "[/]")
        if response:
            color = "#CD7F32"
            label = "> Voidcube"
            title = response_title or f"{label}（后台任务 #{task_num}）"
            console.print(
                Panel(
                    self.ports.rich_text_from_ansi(response),
                    title=f"[{color} bold]{title}[/]",
                    title_align="left",
                    border_style=color,
                    style="#FFF8DC",
                    box=rich_box.HORIZONTALS,
                    padding=(1, 2),
                )
            )
        else:
            self.ports.emit("  （未生成响应）")
