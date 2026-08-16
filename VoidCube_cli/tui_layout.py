"""Prompt-toolkit layout composition for the interactive CLI adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from prompt_toolkit.layout import Window


def build_tui_layout_children(
    *,
    sudo_widget: object,
    secret_widget: object,
    approval_widget: object,
    clarify_widget: object,
    model_picker_widget: object | None,
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
    include_modals: bool = True,
) -> list[object]:
    """Return the fixed root-widget order for the terminal application.

    This adapter owns presentation order only. The caller owns every widget's
    state and visibility condition, while wrappers may insert display-only
    widgets through ``extra_widgets``.
    """
    modal_widgets = (
        sudo_widget,
        secret_widget,
        approval_widget,
        clarify_widget,
        model_picker_widget,
    ) if include_modals else ()
    return [
        widget
        for widget in (
            Window(height=0),
            *modal_widgets,
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
