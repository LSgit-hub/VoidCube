"""Render dynamic placeholder, hint, and spinner text for the terminal UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TuiDynamicTextPorts:
    """Read-only state and presentation callbacks supplied by the CLI host."""

    voice_recording: Callable[[], bool]
    voice_processing: Callable[[], bool]
    sudo_active: Callable[[], bool]
    secret_active: Callable[[], bool]
    approval_active: Callable[[], bool]
    clarify_freetext: Callable[[], bool]
    clarify_active: Callable[[], bool]
    command_running: Callable[[], bool]
    command_spinner_frame: Callable[[], str]
    command_status: Callable[[], str]
    agent_running: Callable[[], bool]
    voice_mode: Callable[[], bool]
    spinner_text: Callable[[], str]
    tool_start_time: Callable[[], float]
    now: Callable[[], float]
    agent_spacer_height: Callable[[], int]
    spinner_height: Callable[[], int]
    sudo_deadline: Callable[[], float]
    secret_deadline: Callable[[], float]
    approval_deadline: Callable[[], float]
    clarify_deadline: Callable[[], float]


class TuiDynamicTextRuntime:
    """Project current host state into prompt-toolkit text callbacks."""

    def __init__(self, ports: TuiDynamicTextPorts) -> None:
        self.ports = ports

    def placeholder(self) -> str:
        if self.ports.voice_recording():
            return "录音中……按 Ctrl+B 停止"
        if self.ports.voice_processing():
            return "转写中……"
        if self.ports.sudo_active():
            return "请输入密码（隐藏输入），按 Enter 跳过"
        if self.ports.secret_active():
            return "请输入密钥（隐藏输入），按 Enter 跳过"
        if self.ports.approval_active():
            return ""
        if self.ports.clarify_freetext():
            return "请在此输入答案并按 Enter 提交"
        if self.ports.clarify_active():
            return ""
        if self.ports.command_running():
            frame = self.ports.command_spinner_frame()
            status = self.ports.command_status() or "正在处理命令……"
            return f"{frame} {status}"
        if self.ports.agent_running():
            return "智能体运行中……使用 /cancel 取消本轮"
        if self.ports.voice_mode():
            return "输入文字，或按 Ctrl+B 录音"
        return ""

    def hint_fragments(self) -> list[tuple[str, str]]:
        if self.ports.sudo_active():
            return self._countdown_hint(
                "  密码已隐藏 · 按 Enter 跳过", self.ports.sudo_deadline()
            )
        if self.ports.secret_active():
            return self._countdown_hint(
                "  密钥已隐藏 · 按 Enter 跳过", self.ports.secret_deadline()
            )
        if self.ports.approval_active():
            return self._countdown_hint(
                "  ↑/↓ 选择，Enter 确认", self.ports.approval_deadline()
            )
        if self.ports.clarify_active():
            deadline = self.ports.clarify_deadline()
            countdown = f"  ({max(0, int(deadline - self.ports.now()))}s)" if deadline else ""
            if self.ports.clarify_freetext():
                return [
                    ("class:hint", "  输入答案并按 Enter 提交"),
                    ("class:clarify-countdown", countdown),
                ]
            return [
                ("class:hint", "  ↑/↓ 选择，Enter 确认"),
                ("class:clarify-countdown", countdown),
            ]
        if self.ports.command_running():
            frame = self.ports.command_spinner_frame()
            return [
                (
                    "class:hint",
                    f"  {frame} 命令执行中 · 暂时无法输入",
                )
            ]
        return []

    def hint_height(self) -> int:
        if (
            self.ports.sudo_active()
            or self.ports.secret_active()
            or self.ports.approval_active()
            or self.ports.clarify_active()
            or self.ports.command_running()
        ):
            return 1
        return self.ports.agent_spacer_height()

    def spinner_fragments(self) -> list[tuple[str, str]]:
        text = self.ports.spinner_text()
        if not text:
            return []
        started_at = self.ports.tool_start_time()
        if started_at <= 0:
            return [("class:hint", f"  {text}")]
        elapsed = max(0.0, self.ports.now() - started_at)
        if elapsed >= 60:
            elapsed_label = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
        else:
            elapsed_label = f"{elapsed:.1f}s"
        return [("class:hint", f"  {text}  ({elapsed_label})")]

    def spinner_widget_height(self) -> int:
        return self.ports.spinner_height()

    def _countdown_hint(self, text: str, deadline: float) -> list[tuple[str, str]]:
        remaining = max(0, int(deadline - self.ports.now()))
        return [
            ("class:hint", text),
            ("class:clarify-countdown", f"  ({remaining}s)"),
        ]
