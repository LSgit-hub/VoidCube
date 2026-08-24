"""Compose the interactive terminal application's static widget tree."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer, HSplit, Layout
from prompt_toolkit.layout.containers import Container
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.dimension import Dimension

from ..terminal_text_layout import (
    completion_menu_max_height as _completion_menu_max_height,
    modal_panel_max_height as _modal_stack_max_height,
)
from .application import create_tui_application
from .layout import build_tui_layout_children


@dataclass(frozen=True, slots=True)
class TuiCompositionWidgets:
    """Already-built widgets supplied by the CLI host."""

    sudo_widget: object
    secret_widget: object
    approval_widget: object
    clarify_widget: object
    model_picker_widget: object | None
    spinner_widget: object | None
    spacer: object
    status_bar: object
    auto_execution_panel: object | None
    input_rule_top: object
    image_bar: object
    input_area: object
    input_rule_bot: object
    voice_status_bar: object
    modal_visible: Callable[[], bool] = lambda: False


@dataclass(frozen=True, slots=True)
class TuiCompositionPorts:
    """Application lifecycle operations owned by the CLI host."""

    cursor: object | None
    store_application: Callable[[object], None]
    install_resize_cleanup: Callable[[object], None]
    input: object | None = None
    output: object | None = None


def _completion_menu_height() -> Dimension:
    """Re-evaluated per frame by the layout engine; caps the menu to the terminal."""
    return Dimension(min=1, max=_completion_menu_max_height())


class TuiCompositionRuntime:
    """Build the application shell without reading CLI state."""

    def __init__(self, ports: TuiCompositionPorts) -> None:
        self.ports = ports

    def compose(
        self,
        *,
        key_bindings: KeyBindings,
        widgets: TuiCompositionWidgets,
        extra_widgets: Callable[[], list[object]],
    ) -> object:
        completions_menu = CompletionsMenu(max_height=_completion_menu_max_height(), scroll_offset=1)
        # The menu shares the terminal's vertical budget with the input area:
        # cap its height dynamically instead of a fixed 12 rows so a small
        # terminal keeps the input area and status bars visible. `Window.height`
        # accepts a callable that the layout engine re-evaluates per frame.
        completions_menu.content.height = _completion_menu_height
        children = build_tui_layout_children(
            spinner_widget=widgets.spinner_widget,
            spacer=widgets.spacer,
            extra_widgets=extra_widgets,
            status_bar=widgets.status_bar,
            auto_execution_panel=widgets.auto_execution_panel,
            input_rule_top=widgets.input_rule_top,
            image_bar=widgets.image_bar,
            input_area=widgets.input_area,
            input_rule_bot=widgets.input_rule_bot,
            voice_status_bar=widgets.voice_status_bar,
            completions_menu=completions_menu,
        )
        modal_stack = HSplit(
            [
                widget
                for widget in (
                    widgets.sudo_widget,
                    widgets.secret_widget,
                    widgets.approval_widget,
                    widgets.clarify_widget,
                    widgets.model_picker_widget,
                )
                if isinstance(widget, Container)
            ],
            height=Dimension(max=_modal_stack_max_height()),
        )
        modal_overlay = ConditionalContainer(
            modal_stack,
            filter=Condition(widgets.modal_visible),
        )
        application = create_tui_application(
            layout=Layout(
                FloatContainer(
                    content=HSplit(children),
                    floats=[
                        Float(
                            content=modal_overlay,
                            top=1,
                            left=2,
                            right=2,
                            bottom=1,
                            z_index=100,
                        )
                    ],
                    modal=True,
                )
            ),
            key_bindings=key_bindings,
            cursor=self.ports.cursor,
            input=self.ports.input,
            output=self.ports.output,
        )
        self.ports.store_application(application)
        self.ports.install_resize_cleanup(application)
        return application
