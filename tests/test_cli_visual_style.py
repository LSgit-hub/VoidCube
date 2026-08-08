from __future__ import annotations

from VoidCube_cli.cli_ui import _accent_hex
from VoidCube_cli.style import (
    ACCENT,
    BACKGROUND,
    BORDER,
    BANNER_ACCENT,
    BANNER_BORDER,
    BANNER_DIM,
    BANNER_TEXT,
    BANNER_TITLE,
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
from VoidCube_cli.tui_application import TUI_STYLE


def test_theme_tokens_are_valid_and_compatibility_names_share_one_palette() -> None:
    tokens = (
        BACKGROUND,
        SURFACE,
        BORDER,
        TEXT,
        MUTED,
        PRIMARY,
        ACCENT,
        SECONDARY,
        INFO,
        GOOD,
        WARN,
        DANGER,
    )
    assert all(len(token) == 7 and token.startswith("#") for token in tokens)
    assert (BANNER_ACCENT, BANNER_TITLE) == (PRIMARY, PRIMARY)
    assert (BANNER_BORDER, BANNER_DIM, BANNER_TEXT) == (BORDER, MUTED, TEXT)
    assert _accent_hex() == ACCENT


def test_tui_style_uses_the_canonical_surface_and_focus_colors() -> None:
    assert TUI_STYLE["input-area"] == f"bg:{BACKGROUND} {TEXT}"
    assert TUI_STYLE["status-bar"] == f"bg:{SURFACE} {MUTED}"
    assert TUI_STYLE["input-rule"] == BORDER
    assert TUI_STYLE["prompt"] == f"bg:{BACKGROUND} {PRIMARY} bold"
    assert TUI_STYLE["completion-menu.completion.current"] == (
        f"bg:{PRIMARY} {BACKGROUND} bold"
    )
