"""Build the interactive TUI runtime graph from host-owned ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.key_binding import KeyBindings

from VoidCube_cli.tui_composition_runtime import (
    TuiCompositionPorts,
    TuiCompositionRuntime,
    TuiCompositionWidgets,
)
from VoidCube_cli.tui_indicator_widgets import IndicatorWidgetPorts
from VoidCube_cli.tui_input_widgets import InputWidgetPorts
from VoidCube_cli.tui_keybinding_assembly import (
    TuiKeybindingAssemblyPorts,
    TuiKeybindingAssemblyRuntime,
)
from VoidCube_cli.tui_modal_navigation import ModalNavigationPorts
from VoidCube_cli.tui_modal_widgets import ModalWidgetPorts
from VoidCube_cli.tui_paste_runtime import PasteRuntimePorts, TuiPasteRuntime
from VoidCube_cli.tui_widget_graph_runtime import (
    TuiWidgetGraphPorts,
    TuiWidgetGraphRuntime,
)


@dataclass(frozen=True, slots=True)
class TuiRuntimeFactoryPorts:
    """Host callbacks and runtime adapters needed for TUI assembly."""

    enter: Callable[[object], None]
    ctrl_z: Callable[[object], None]
    voice_key: str
    voice: Callable[[object], None]
    paste: PasteRuntimePorts
    modal_navigation: ModalNavigationPorts
    normal_input_active: Callable[[], bool]
    input: InputWidgetPorts
    placeholder_text: Callable[[], str]
    modal: ModalWidgetPorts
    indicators: IndicatorWidgetPorts
    register_extra_keybindings: Callable[..., None]
    composition: TuiCompositionPorts
    extra_widgets: Callable[[], list[object]]


class TuiRuntimeFactory:
    """Create and connect the interactive TUI runtimes without CLI state access."""

    def __init__(self, ports: TuiRuntimeFactoryPorts) -> None:
        self.ports = ports

    def build(self) -> object:
        ports = self.ports
        key_bindings = KeyBindings()
        paste_runtime = TuiPasteRuntime(ports.paste)

        TuiKeybindingAssemblyRuntime(
            TuiKeybindingAssemblyPorts(
                key_bindings=key_bindings,
                enter=ports.enter,
                ctrl_z=ports.ctrl_z,
                voice_key=ports.voice_key,
                voice=ports.voice,
                paste=paste_runtime,
                modal_navigation=ports.modal_navigation,
                normal_input_active=ports.normal_input_active,
            )
        ).install()

        widget_graph = TuiWidgetGraphRuntime(
            TuiWidgetGraphPorts(
                input=ports.input,
                placeholder_text=ports.placeholder_text,
                on_text_changed=paste_runtime.handle_text_changed,
                modal=ports.modal,
                indicators=ports.indicators,
            )
        ).build()
        ports.register_extra_keybindings(
            key_bindings,
            input_area=widget_graph.input_area,
        )

        modal_widgets = widget_graph.modal_widgets
        indicator_widgets = widget_graph.indicator_widgets
        return TuiCompositionRuntime(ports.composition).compose(
            key_bindings=key_bindings,
            widgets=TuiCompositionWidgets(
                sudo_widget=modal_widgets.sudo,
                secret_widget=modal_widgets.secret,
                approval_widget=modal_widgets.approval,
                clarify_widget=modal_widgets.clarify,
                model_picker_widget=modal_widgets.model_picker,
                spinner_widget=indicator_widgets.spinner,
                spacer=indicator_widgets.spacer,
                status_bar=indicator_widgets.status_bar,
                auto_execution_panel=indicator_widgets.autonomous_execution_panel,
                input_rule_top=indicator_widgets.input_rule_top,
                image_bar=indicator_widgets.image_bar,
                input_area=widget_graph.input_area,
                input_rule_bot=indicator_widgets.input_rule_bottom,
                voice_status_bar=indicator_widgets.voice_status_bar,
            ),
            extra_widgets=ports.extra_widgets,
        )
