"""Execute one pending CLI input through explicit command and turn ports."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from VoidCube_cli.attachments import _detect_file_drop
from VoidCube_cli.cli_ui import _DIM, _RST, _accent_hex, _cprint
from VoidCube_cli.command_router import looks_like_slash_command
from VoidCube_cli.cli_idle_maintenance_runtime import drain_process_notifications


logger = logging.getLogger(__name__)
_PASTE_REF_RE = re.compile(r"\[Pasted text #\d+: \d+ lines \u2192 (.+?)\]")


@dataclass(frozen=True, slots=True)
class PendingInputExecutionPorts:
    """Host-owned state and side effects for one queued input execution."""

    should_emit_scrollback: Callable[[], bool]
    process_command: Callable[[str], bool]
    set_should_exit: Callable[[bool], None]
    reset_turn_state: Callable[[], None]
    submit_turn: Callable[[Any, Any], bool]
    invalidate_app: Callable[[Any | None], None]
    exit_app: Callable[[Any], None]
    voice_restart_ready: Callable[[], bool]
    restart_voice_recording: Callable[[], None]
    enqueue_pending_input: Callable[[Any], None]
    render_markup: Callable[[str], None]
    emit: Callable[[str], None] = _cprint


class PendingInputRuntime:
    """Own normalization and lifecycle around one queued CLI input."""

    def __init__(self, ports: PendingInputExecutionPorts) -> None:
        self.ports = ports

    def execute(self, user_input: Any, *, app: Any | None = None) -> bool:
        if not user_input:
            return False

        should_emit_scrollback = self.ports.should_emit_scrollback()
        submit_images: list[Any] = []
        if isinstance(user_input, tuple):
            user_input, submit_images = user_input

        file_drop = _detect_file_drop(user_input) if isinstance(user_input, str) else None
        if file_drop:
            drop_path = file_drop["path"]
            remainder = file_drop["remainder"]
            if file_drop["is_image"]:
                submit_images.append(drop_path)
                user_input = remainder or f"[User attached image: {drop_path.name}]"
                if should_emit_scrollback:
                    self.ports.emit(f"  📎 Auto-attached image: {drop_path.name}")
            else:
                if should_emit_scrollback:
                    self.ports.emit(f"  📄 Detected file: {drop_path.name}")
                user_input = f"[User attached file: {drop_path}]"
                if remainder:
                    user_input += f"\n{remainder}"

        if not file_drop and isinstance(user_input, str) and looks_like_slash_command(user_input):
            if should_emit_scrollback:
                self.ports.emit(f"\n>️  {user_input}")
            logger.info("CLI command executed: %s", user_input)
            if not self.ports.process_command(user_input):
                self.ports.set_should_exit(True)
                try:
                    self.ports.exit_app(app)
                except Exception:
                    pass
            return False

        user_input = self._expand_pasted_input(user_input, should_emit_scrollback)
        if submit_images and should_emit_scrollback:
            count = len(submit_images)
            self.ports.emit(
                f"  {_DIM}📎 {count} image{'s' if count > 1 else ''} attached{_RST}"
            )

        self.ports.invalidate_app(app)
        sanitized = str(user_input).encode("ascii", errors="replace").decode("ascii")
        logger.info(
            "User input received: %s (images: %d)",
            sanitized[:100] + "..." if len(sanitized) > 100 else sanitized,
            len(submit_images),
        )

        try:
            self.ports.submit_turn((user_input, submit_images or None), app)
        finally:
            self.ports.reset_turn_state()
            self.ports.invalidate_app(app)
            self._restart_continuous_voice_if_needed(app)
            self._enqueue_process_notifications()

        return True

    def _expand_pasted_input(self, user_input: Any, should_emit_scrollback: bool) -> Any:
        paste_refs = list(_PASTE_REF_RE.finditer(user_input)) if isinstance(user_input, str) else []
        if paste_refs:
            def expand_ref(match: re.Match[str]) -> str:
                path = Path(match.group(1))
                return path.read_text(encoding="utf-8") if path.exists() else match.group(0)

            expanded = _PASTE_REF_RE.sub(expand_ref, user_input)
            total_lines = expanded.count("\n") + 1
            paste_count = len(paste_refs)
            if should_emit_scrollback:
                user_bar = f"[#34D399]{'~' * 40}[/]"
                print()
                self.ports.render_markup(user_bar)
                split_parts = _PASTE_REF_RE.split(user_input)
                visible_user_text = " ".join(
                    split_parts[index].strip()
                    for index in range(0, len(split_parts), 2)
                    if split_parts[index].strip()
                )
                if visible_user_text:
                    self.ports.render_markup(
                        f"[bold {_accent_hex()}]●[/] [bold]{_escape(visible_user_text)}[/] "
                        f"[dim]({paste_count} pasted block{'s' if paste_count > 1 else ''}, "
                        f"{total_lines} lines total)[/]"
                    )
                else:
                    self.ports.render_markup(
                        f"[bold {_accent_hex()}]●[/] "
                        f"[bold]{_escape(f'[Pasted text: {total_lines} lines]')}[/]"
                    )
            return expanded

        if should_emit_scrollback:
            user_bar = f"[#34D399]{'~' * 40}[/]"
            if isinstance(user_input, str) and "\n" in user_input:
                first_line = user_input.split("\n")[0]
                line_count = user_input.count("\n") + 1
                print()
                self.ports.render_markup(user_bar)
                self.ports.render_markup(
                    f"[bold {_accent_hex()}]●[/] [bold]{_escape(first_line)}[/] "
                    f"[dim](+{line_count - 1} lines)[/]"
                )
            else:
                print()
                self.ports.render_markup(user_bar)
                self.ports.render_markup(
                    f"[bold {_accent_hex()}]●[/] [bold]{_escape(str(user_input))}[/]"
                )
        return user_input

    def _restart_continuous_voice_if_needed(self, app: Any | None) -> None:
        if not self.ports.voice_restart_ready():
            return

        def restart() -> None:
            try:
                self.ports.restart_voice_recording()
                self.ports.invalidate_app(app)
            except Exception as exc:
                self.ports.emit(f"{_DIM}Voice auto-restart failed: {exc}{_RST}")

        threading.Thread(target=restart, daemon=True).start()

    def _enqueue_process_notifications(self) -> None:
        drain_process_notifications(self.ports.enqueue_pending_input)


def _escape(text: str) -> str:
    from rich.markup import escape

    return escape(text)
