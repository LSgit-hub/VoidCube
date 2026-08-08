"""Prompt-toolkit application composition for the CLI adapter."""

from __future__ import annotations

from collections.abc import Mapping

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style

from VoidCube_cli.style import (
    ACCENT,
    BACKGROUND,
    BORDER,
    DANGER,
    GOOD,
    INFO,
    MUTED,
    PRIMARY,
    SECONDARY,
    SURFACE,
    TEXT,
    WARN,
)


TUI_STYLE: Mapping[str, str] = {
    "": f"bg:{BACKGROUND} {TEXT}",
    "input-area": f"bg:{BACKGROUND} {TEXT}",
    "placeholder": f"bg:{BACKGROUND} {MUTED} italic",
    "prompt": f"bg:{BACKGROUND} {PRIMARY} bold",
    "prompt-working": f"bg:{BACKGROUND} {ACCENT} italic",
    "hint": f"bg:{BACKGROUND} {MUTED} italic",
    "status-bar": f"bg:{SURFACE} {MUTED}",
    "status-bar-strong": f"bg:{SURFACE} {PRIMARY} bold",
    "status-bar-dim": f"bg:{SURFACE} {MUTED}",
    "status-bar-good": f"bg:{SURFACE} {GOOD} bold",
    "status-bar-warn": f"bg:{SURFACE} {WARN} bold",
    "status-bar-bad": f"bg:{SURFACE} {ACCENT} bold",
    "status-bar-critical": f"bg:{SURFACE} {DANGER} bold",
    "input-rule": BORDER,
    "image-badge": f"{PRIMARY} bold",
    "completion-menu": f"bg:{SURFACE} {TEXT}",
    "completion-menu.completion": f"bg:{SURFACE} {TEXT}",
    "completion-menu.completion.current": f"bg:{PRIMARY} {BACKGROUND} bold",
    "completion-menu.meta.completion": f"bg:{SURFACE} {MUTED}",
    "completion-menu.meta.completion.current": f"bg:{PRIMARY} {BACKGROUND}",
    "auto-panel-border": BORDER,
    "auto-panel-title": f"{PRIMARY} bold",
    "auto-panel-text": TEXT,
    "auto-panel-dim": MUTED,
    "auto-panel-info": INFO,
    "auto-panel-good": f"{GOOD} bold",
    "auto-panel-warn": f"{WARN} bold",
    "auto-panel-bad": f"{DANGER} bold",
    "clarify-border": BORDER,
    "clarify-title": f"{PRIMARY} bold",
    "clarify-question": f"{TEXT} bold",
    "clarify-choice": MUTED,
    "clarify-selected": f"{PRIMARY} bold",
    "clarify-active-other": f"{SECONDARY} italic",
    "clarify-countdown": SECONDARY,
    "sudo-prompt": f"{DANGER} bold",
    "sudo-border": BORDER,
    "sudo-title": f"{DANGER} bold",
    "sudo-text": TEXT,
    "approval-border": BORDER,
    "approval-title": f"{ACCENT} bold",
    "approval-desc": f"{TEXT} bold",
    "approval-cmd": f"{MUTED} italic",
    "approval-choice": MUTED,
    "approval-selected": f"{PRIMARY} bold",
    "voice-prompt": PRIMARY,
    "voice-recording": f"{DANGER} bold",
    "voice-processing": f"{ACCENT} italic",
    "voice-status": f"bg:{SURFACE} {PRIMARY}",
    "voice-status-recording": f"bg:{SURFACE} {DANGER} bold",
}


def create_tui_application(
    *,
    layout: Layout,
    key_bindings: KeyBindings,
    cursor: object | None,
) -> Application:
    """Create a terminal application with the stable CLI presentation style."""
    options: dict[str, object] = {}
    if cursor is not None:
        options["cursor"] = cursor
    application = Application(
        layout=layout,
        key_bindings=key_bindings,
        style=Style.from_dict(dict(TUI_STYLE)),
        full_screen=False,
        mouse_support=False,
        **options,
    )
    return application


def install_resize_reflow_cleanup(application: Application) -> None:
    """Clear terminal rows made stale when a narrower terminal reflows output."""
    original_on_resize = application._on_resize

    def clear_reflowed_rows() -> None:
        renderer = application.renderer
        try:
            old_size = renderer._last_size
            new_size = renderer.output.get_size()
            if old_size and new_size.columns < old_size.columns and new_size.columns > 0:
                reflow_factor = (old_size.columns + new_size.columns - 1) // new_size.columns
                last_height = renderer._last_screen.height if renderer._last_screen else 0
                extra_rows = last_height * (reflow_factor - 1)
                if extra_rows > 0:
                    renderer._cursor_pos = Point(
                        x=renderer._cursor_pos.x,
                        y=renderer._cursor_pos.y + extra_rows,
                    )
        except Exception:
            pass
        original_on_resize()

    application._on_resize = clear_reflowed_rows
