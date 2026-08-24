"""Prompt-toolkit layout composition for the interactive CLI adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from prompt_toolkit.layout import Window


def build_tui_layout_children(
    *,
    spinner_widget: object | None,
    spacer: object,
    extra_widgets: Callable[[], Sequence[object]],
    status_bar: object,
    auto_execution_panel: object | None,
    input_rule_top: object,
    image_bar: object,
    input_area: object,
    input_rule_bot: object,
    voice_status_bar: object,
    completions_menu: object,
) -> list[object]:
    """Return the fixed root-widget order for the terminal application.

    This adapter owns presentation order only. The caller owns every widget's
    state and visibility condition, while wrappers may insert display-only
    widgets through ``extra_widgets``. Modal overlays live in a separate stack
    managed by the caller, so they are intentionally not repeated here.
    """
    return [
        widget
        for widget in (
            Window(height=0),
            spinner_widget,
            spacer,
            *extra_widgets(),
            status_bar,
            input_rule_top,
            image_bar,
            input_area,
            input_rule_bot,
            voice_status_bar,
            auto_execution_panel,
            completions_menu,
        )
        if widget is not None
    ]
