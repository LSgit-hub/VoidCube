"""Prompt-toolkit application composition for the CLI adapter."""

from __future__ import annotations

from collections.abc import Mapping

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style


TUI_STYLE: Mapping[str, str] = {
    "input-area": "bg:#1a1a2e #E8E8E8",
    "placeholder": "bg:#1a1a2e #6B7280 italic",
    "prompt": "bg:#1a1a2e #E8E8E8 bold",
    "prompt-working": "bg:#1a1a2e #58A6FF italic",
    "hint": "bg:#1a1a2e #6B7280 italic",
    "status-bar": "bg:#1a1a2e #9CA3AF",
    "status-bar-strong": "bg:#1a1a2e #1E40AF bold",
    "status-bar-dim": "bg:#1a1a2e #6B7280",
    "status-bar-good": "bg:#1a1a2e #34D399 bold",
    "status-bar-warn": "bg:#1a1a2e #FBBF24 bold",
    "status-bar-bad": "bg:#1a1a2e #FB923C bold",
    "status-bar-critical": "bg:#1a1a2e #F87171 bold",
    "input-rule": "#30363D",
    "image-badge": "#58A6FF bold",
    "completion-menu": "bg:#1a1a2e #E8E8E8",
    "completion-menu.completion": "bg:#1a1a2e #E8E8E8",
    "completion-menu.completion.current": "bg:#1E40AF #E8E8E8",
    "completion-menu.meta.completion": "bg:#1a1a2e #6B7280",
    "completion-menu.meta.completion.current": "bg:#1E40AF #58A6FF",
    "auto-panel-border": "#30363D",
    "auto-panel-title": "#58A6FF bold",
    "auto-panel-text": "#E8E8E8",
    "auto-panel-dim": "#9CA3AF",
    "auto-panel-info": "#58A6FF",
    "auto-panel-good": "#34D399 bold",
    "auto-panel-warn": "#FBBF24 bold",
    "auto-panel-bad": "#F87171 bold",
    "clarify-border": "#30363D",
    "clarify-title": "#58A6FF bold",
    "clarify-question": "#E8E8E8 bold",
    "clarify-choice": "#9CA3AF",
    "clarify-selected": "#58A6FF bold",
    "clarify-active-other": "#58A6FF italic",
    "clarify-countdown": "#58A6FF",
    "sudo-prompt": "#F87171 bold",
    "sudo-border": "#30363D",
    "sudo-title": "#F87171 bold",
    "sudo-text": "#E8E8E8",
    "approval-border": "#30363D",
    "approval-title": "#FB923C bold",
    "approval-desc": "#E8E8E8 bold",
    "approval-cmd": "#9CA3AF italic",
    "approval-choice": "#9CA3AF",
    "approval-selected": "#58A6FF bold",
    "voice-prompt": "#58A6FF",
    "voice-recording": "#F87171 bold",
    "voice-processing": "#FB923C italic",
    "voice-status": "bg:#1a1a2e #58A6FF",
    "voice-status-recording": "bg:#1a1a2e #F87171 bold",
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
    return Application(
        layout=layout,
        key_bindings=key_bindings,
        style=Style.from_dict(dict(TUI_STYLE)),
        full_screen=False,
        mouse_support=False,
        **options,
    )


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
