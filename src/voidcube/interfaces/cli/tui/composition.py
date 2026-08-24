"""Compose the interactive CLI TUI from host-owned state and callbacks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..lifecycle.registration import (
    CliInteractiveRegistrations,
)
from .host_assembly import (
    CliTuiCompositionPorts,
    CliTuiExtensionPorts,
    CliTuiHostAssemblyPorts,
    CliTuiHostAssemblyRuntime,
    CliTuiInputPorts,
    CliTuiModalNavigationPorts,
    CliTuiModalPorts,
    CliTuiModalStatePorts,
    CliTuiModalStateRuntime,
    CliTuiPastePorts,
)
from .indicator_assembly import (
    CliTuiIndicatorAssemblyPorts,
    CliTuiIndicatorAssemblyRuntime,
)
from .image_indicator import CliTuiImageIndicatorPorts


@dataclass(frozen=True, slots=True)
class CliInteractiveTuiStatePorts:
    """Modal state projections owned by the CLI host."""

    clarify_state: Callable[[], object | None]
    clarify_freetext_active: Callable[[], bool]
    sudo_state: Callable[[], object | None]
    secret_state: Callable[[], object | None]
    approval_state: Callable[[], object | None]
    model_picker_state: Callable[[], object | None]
    # Run a modal-state mutation under the host's modal-state lock.
    update_selection: Callable[[Callable[[], None]], None]


@dataclass(frozen=True, slots=True)
class CliInteractiveTuiPorts:
    """Host callbacks required to compose the interactive TUI."""

    registrations: CliInteractiveRegistrations
    prompt_runtime: Any
    layout_metrics: Any
    state: CliInteractiveTuiStatePorts
    attached_images: Callable[[], list[object]]
    image_counter: Callable[[], int]
    format_image_badges: Callable[[list[object]], Any]
    voice_fragments: Callable[[], Any]
    voice_visible: Callable[[], bool]
    autonomous_fragments: Callable[[], Any]
    autonomous_visible: Callable[[], bool]
    status_fragments: Callable[[], Any]
    status_visible: Callable[[], bool]
    should_attach_clipboard_image: Callable[[str], bool]
    attach_clipboard_image: Callable[[], bool]
    paste_directory: Path
    timestamp: Callable[[], str]
    invalidate_event: Callable[[object], None]
    invalidate: Callable[[], None]
    history_path: str
    command_available: Callable[[str], bool]
    command_running: Callable[[], bool]
    approval_fragments: Callable[[], Any]
    register_extra_keybindings: Callable[..., None]
    cursor: object | None
    store_application: Callable[[object], None]
    install_resize_cleanup: Callable[[object], None]
    extra_widgets: Callable[[], list[object]]


class CliInteractiveTuiAssemblyRuntime:
    """Build the prompt_toolkit application without owning CLI state."""

    def __init__(self, ports: CliInteractiveTuiPorts) -> None:
        self.ports = ports

    def build(self) -> object:
        ports = self.ports
        state = ports.state
        modal_state_runtime = CliTuiModalStateRuntime(
            CliTuiModalStatePorts(
                clarify_state=state.clarify_state,
                clarify_freetext_active=state.clarify_freetext_active,
                sudo_state=state.sudo_state,
                secret_state=state.secret_state,
                approval_state=state.approval_state,
                model_picker_state=state.model_picker_state,
                update_selection=state.update_selection,
            )
        )
        indicator_ports = CliTuiIndicatorAssemblyRuntime(
            CliTuiIndicatorAssemblyPorts(
                dynamic_text=ports.registrations.dynamic_text,
                layout_input_rule_height=ports.layout_metrics.input_rule_height,
                image=CliTuiImageIndicatorPorts(
                    attached_images=ports.attached_images,
                    image_counter=ports.image_counter,
                    format_badges=ports.format_image_badges,
                ),
                voice_fragments=ports.voice_fragments,
                voice_visible=ports.voice_visible,
                autonomous_fragments=ports.autonomous_fragments,
                autonomous_visible=ports.autonomous_visible,
                status_fragments=ports.status_fragments,
                status_visible=ports.status_visible,
            )
        ).build()

        return CliTuiHostAssemblyRuntime(
            CliTuiHostAssemblyPorts(
                registrations=ports.registrations,
                paste=CliTuiPastePorts(
                    should_attach_clipboard_image=ports.should_attach_clipboard_image,
                    attach_clipboard_image=ports.attach_clipboard_image,
                    paste_directory=ports.paste_directory,
                    timestamp=ports.timestamp,
                    invalidate=ports.invalidate_event,
                ),
                modal_navigation=modal_state_runtime.modal_navigation_ports(
                    invalidate=ports.invalidate,
                ),
                normal_input_active=modal_state_runtime.normal_input_active,
                input=CliTuiInputPorts(
                    history_path=ports.history_path,
                    prompt_fragments=ports.prompt_runtime.fragments,
                    prompt_text=ports.prompt_runtime.text,
                    command_available=ports.command_available,
                    command_running=ports.command_running,
                    password_mask_active=modal_state_runtime.password_mask_active,
                    input_locked=modal_state_runtime.input_locked,
                ),
                placeholder_text=ports.registrations.dynamic_text.placeholder,
                modal=modal_state_runtime.modal_widget_ports(
                    approval_fragments=ports.approval_fragments,
                ),
                indicators=indicator_ports,
                extensions=CliTuiExtensionPorts(
                    register_extra_keybindings=ports.register_extra_keybindings,
                    composition=CliTuiCompositionPorts(
                        cursor=ports.cursor,
                        store_application=ports.store_application,
                        install_resize_cleanup=ports.install_resize_cleanup,
                    ),
                    extra_widgets=ports.extra_widgets,
                ),
            )
        ).build()


__all__ = [
    "CliInteractiveTuiAssemblyRuntime",
    "CliInteractiveTuiPorts",
    "CliInteractiveTuiStatePorts",
]
