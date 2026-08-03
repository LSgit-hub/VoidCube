"""Assemble the interactive TUI widget graph from explicit widget ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.widgets import TextArea

from VoidCube_cli.tui_indicator_widgets import (
    IndicatorWidgetPorts,
    IndicatorWidgets,
    build_indicator_widgets,
)
from VoidCube_cli.tui_input_widgets import (
    InputWidgetPorts,
    build_input_area,
    install_placeholder_processor,
)
from VoidCube_cli.tui_modal_widgets import (
    ModalWidgetPorts,
    ModalWidgets,
    build_modal_widgets,
)


@dataclass(frozen=True, slots=True)
class TuiWidgetGraphPorts:
    """Existing widget factory ports and buffer lifecycle callbacks."""

    input: InputWidgetPorts
    placeholder_text: Callable[[], str]
    on_text_changed: Callable[[object], None]
    modal: ModalWidgetPorts
    indicators: IndicatorWidgetPorts


@dataclass(frozen=True, slots=True)
class TuiWidgetGraph:
    """Widgets produced for the static TUI composition runtime."""

    input_area: TextArea
    modal_widgets: ModalWidgets
    indicator_widgets: IndicatorWidgets


class TuiWidgetGraphRuntime:
    """Build the input, modal and indicator widget groups without CLI state."""

    def __init__(self, ports: TuiWidgetGraphPorts) -> None:
        self.ports = ports

    def build(self) -> TuiWidgetGraph:
        ports = self.ports
        input_area = build_input_area(ports=ports.input)
        input_area.buffer.on_text_changed += ports.on_text_changed
        install_placeholder_processor(
            input_area,
            placeholder_text=ports.placeholder_text,
        )
        return TuiWidgetGraph(
            input_area=input_area,
            modal_widgets=build_modal_widgets(ports=ports.modal),
            indicator_widgets=build_indicator_widgets(ports=ports.indicators),
        )
