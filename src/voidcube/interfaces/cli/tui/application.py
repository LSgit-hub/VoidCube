"""Prompt-toolkit application composition for the CLI adapter."""

from __future__ import annotations

import logging
from collections.abc import Mapping

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style


logger = logging.getLogger(__name__)


TUI_STYLE: Mapping[str, str] = {
    # ── Main CLI (dark navy theme) ──────────────────────────────────────
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
    # ── Clarify / Approval / Voice (main CLI modal overlays) ────────────
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
    # ── Mini CLI / Autonomous Panel (cyan-teal theme, distinct from main) ─
    # Panel chrome — border styles carry the panel background so the
    # │ content │ row is perfectly flush with no colour gaps.
    "mc-panel-bg": "bg:#0d1f2d",                  # deep teal-dark fill
    "mc-border": "bg:#0d1f2d #0ea5a9",            # teal border on panel bg
    # Panel body
    "mc-body-text": "bg:#0d1f2d #e2e8f0",   # main text on dark teal bg
    # Status indicators
    "mc-status-active": "bg:#0d1f2d #2dd4bf bold",    # active/running (bright teal)
    "mc-status-idle": "bg:#0d1f2d #94a3b8",           # idle (gray)
    "mc-status-success": "bg:#0d1f2d #34d399 bold",   # success (green, shared)
    "mc-status-warn": "bg:#0d1f2d #f59e0b bold",      # warning (amber)
    "mc-status-error": "bg:#0d1f2d #ef4444 bold",      # error (red)
    "mc-status-info": "bg:#0d1f2d #22d3ee",            # info (cyan)
}


def create_tui_application(
    *,
    layout: Layout,
    key_bindings: KeyBindings,
    cursor: object | None,
    input: object | None = None,
    output: object | None = None,
) -> Application:
    """Create a terminal application with the stable CLI presentation style."""
    options: dict[str, object] = {}
    if cursor is not None:
        options["cursor"] = cursor
    if input is not None:
        options["input"] = input
    if output is not None:
        options["output"] = output
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
    """Adapt prompt-toolkit's renderer resize behavior for narrow terminals.

    The renderer fields used here are private prompt-toolkit implementation
    details. Keep the compatibility shim isolated and fail open if a future
    prompt-toolkit release changes them.
    """
    original_on_resize = getattr(application, "_on_resize", None)
    if not callable(original_on_resize):
        return

    def clear_reflowed_rows() -> None:
        try:
            renderer = application.renderer
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
        except (AttributeError, TypeError, ValueError):
            logger.debug("prompt-toolkit resize compatibility shim skipped", exc_info=True)
        original_on_resize()

    application._on_resize = clear_reflowed_rows
