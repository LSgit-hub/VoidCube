"""Bind CLI-owned callbacks to the generic interactive TUI factory."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.formatted_text import AnyFormattedText

from ..lifecycle.registration import (
    CliInteractiveRegistrations,
)
from .indicator_assembly import CliTuiIndicatorPorts
from .composition_runtime import TuiCompositionPorts
from .indicator_widgets import IndicatorWidgetPorts
from .input_widgets import InputWidgetPorts
from .modal_navigation import ModalNavigationPorts
from .modal_widgets import ModalWidgetPorts
from .paste_runtime import PasteRuntimePorts
from .runtime_factory import (
    TuiRuntimeFactory,
    TuiRuntimeFactoryPorts,
)


@dataclass(frozen=True, slots=True)
class CliTuiPastePorts:
    should_attach_clipboard_image: Callable[[str], bool]
    attach_clipboard_image: Callable[[], bool]
    paste_directory: Path
    timestamp: Callable[[], str]
    invalidate: Callable[[object], None]


@dataclass(frozen=True, slots=True)
class CliTuiModalNavigationPorts:
    clarify_state: Callable[[], object | None]
    clarify_freetext_active: Callable[[], bool]
    approval_state: Callable[[], object | None]
    model_picker_state: Callable[[], object | None]
    invalidate: Callable[[], None]


@dataclass(frozen=True, slots=True)
class CliTuiInputPorts:
    history_path: str
    prompt_fragments: Callable[[], AnyFormattedText]
    prompt_text: Callable[[], str]
    command_available: Callable[[str], bool]
    command_running: Callable[[], bool]
    password_mask_active: Callable[[], bool]
    input_locked: Callable[[], bool] = lambda: False


@dataclass(frozen=True, slots=True)
class CliTuiModalPorts:
    clarify_state: Callable[[], object | None]
    clarify_freetext_active: Callable[[], bool]
    sudo_state: Callable[[], object | None]
    secret_state: Callable[[], object | None]
    approval_state: Callable[[], object | None]
    approval_fragments: Callable[[], AnyFormattedText]
    model_picker_state: Callable[[], object | None]


@dataclass(frozen=True, slots=True)
class CliTuiModalStatePorts:
    """CLI-owned modal state getters used by the TUI assembly."""

    clarify_state: Callable[[], object | None]
    clarify_freetext_active: Callable[[], bool]
    sudo_state: Callable[[], object | None]
    secret_state: Callable[[], object | None]
    approval_state: Callable[[], object | None]
    model_picker_state: Callable[[], object | None]


class CliTuiModalStateRuntime:
    """Build the modal callback projections and input-state policy."""

    def __init__(self, ports: CliTuiModalStatePorts) -> None:
        self.ports = ports

    def modal_navigation_ports(
        self,
        *,
        invalidate: Callable[[], None],
    ) -> CliTuiModalNavigationPorts:
        ports = self.ports
        return CliTuiModalNavigationPorts(
            clarify_state=ports.clarify_state,
            clarify_freetext_active=ports.clarify_freetext_active,
            approval_state=ports.approval_state,
            model_picker_state=ports.model_picker_state,
            invalidate=invalidate,
        )

    def modal_widget_ports(
        self,
        *,
        approval_fragments: Callable[[], AnyFormattedText],
    ) -> CliTuiModalPorts:
        ports = self.ports
        return CliTuiModalPorts(
            clarify_state=ports.clarify_state,
            clarify_freetext_active=ports.clarify_freetext_active,
            sudo_state=ports.sudo_state,
            secret_state=ports.secret_state,
            approval_state=ports.approval_state,
            approval_fragments=approval_fragments,
            model_picker_state=ports.model_picker_state,
        )

    def normal_input_active(self) -> bool:
        ports = self.ports
        return not any(
            (
                ports.clarify_state(),
                ports.approval_state(),
                ports.sudo_state(),
                ports.secret_state(),
                ports.model_picker_state(),
            )
        )

    def password_mask_active(self) -> bool:
        return bool(self.ports.sudo_state() or self.ports.secret_state())

    def input_locked(self) -> bool:
        """Lock the draft while a selection modal owns Enter and arrows."""
        return bool(
            self.ports.approval_state()
            or self.ports.model_picker_state()
            or (self.ports.clarify_state() and not self.ports.clarify_freetext_active())
        )


@dataclass(frozen=True, slots=True)
class CliTuiCompositionPorts:
    cursor: object | None
    store_application: Callable[[object], None]
    install_resize_cleanup: Callable[[object], None]
    input: object | None = None
    output: object | None = None


@dataclass(frozen=True, slots=True)
class CliTuiExtensionPorts:
    """Wrapper extension hooks kept outside the core TUI state ports."""

    register_extra_keybindings: Callable[..., None]
    composition: CliTuiCompositionPorts
    extra_widgets: Callable[[], list[object]]


@dataclass(frozen=True, slots=True)
class CliTuiHostAssemblyPorts:
    """CLI state projections and callbacks needed by the TUI composition root."""

    registrations: CliInteractiveRegistrations
    paste: CliTuiPastePorts
    modal_navigation: CliTuiModalNavigationPorts
    normal_input_active: Callable[[], bool]
    input: CliTuiInputPorts
    placeholder_text: Callable[[], str]
    modal: CliTuiModalPorts
    indicators: CliTuiIndicatorPorts
    extensions: CliTuiExtensionPorts


class CliTuiHostAssemblyRuntime:
    """Translate CLI-owned TUI callbacks into the generic factory contract."""

    def __init__(self, ports: CliTuiHostAssemblyPorts) -> None:
        self.ports = ports

    def build(self) -> object:
        ports = self.ports
        registrations = ports.registrations
        return TuiRuntimeFactory(
            TuiRuntimeFactoryPorts(
                enter=registrations.enter.handle,
                ctrl_z=registrations.suspend.handle,
                voice_key=registrations.voice_key,
                voice=registrations.voice.handle,
                paste=PasteRuntimePorts(
                    should_attach_clipboard_image=ports.paste.should_attach_clipboard_image,
                    attach_clipboard_image=ports.paste.attach_clipboard_image,
                    paste_directory=ports.paste.paste_directory,
                    timestamp=ports.paste.timestamp,
                    invalidate=ports.paste.invalidate,
                ),
                modal_navigation=ModalNavigationPorts(
                    clarify_state=ports.modal_navigation.clarify_state,
                    clarify_freetext_active=ports.modal_navigation.clarify_freetext_active,
                    approval_state=ports.modal_navigation.approval_state,
                    model_picker_state=ports.modal_navigation.model_picker_state,
                    invalidate=ports.modal_navigation.invalidate,
                ),
                normal_input_active=ports.normal_input_active,
                input=InputWidgetPorts(
                    history_path=ports.input.history_path,
                    prompt_fragments=ports.input.prompt_fragments,
                    prompt_text=ports.input.prompt_text,
                    command_available=ports.input.command_available,
                    command_running=ports.input.command_running,
                    password_mask_active=ports.input.password_mask_active,
                    input_locked=ports.input.input_locked,
                ),
                placeholder_text=ports.placeholder_text,
                modal=ModalWidgetPorts(
                    clarify_state=ports.modal.clarify_state,
                    clarify_freetext_active=ports.modal.clarify_freetext_active,
                    sudo_state=ports.modal.sudo_state,
                    secret_state=ports.modal.secret_state,
                    approval_state=ports.modal.approval_state,
                    approval_fragments=ports.modal.approval_fragments,
                    model_picker_state=ports.modal.model_picker_state,
                ),
                indicators=IndicatorWidgetPorts(
                    spinner_fragments=ports.indicators.spinner_fragments,
                    spinner_height=ports.indicators.spinner_height,
                    hint_fragments=ports.indicators.hint_fragments,
                    hint_height=ports.indicators.hint_height,
                    input_rule_height=ports.indicators.input_rule_height,
                    image_fragments=ports.indicators.image_fragments,
                    images_visible=ports.indicators.images_visible,
                    voice_fragments=ports.indicators.voice_fragments,
                    voice_visible=ports.indicators.voice_visible,
                    autonomous_fragments=ports.indicators.autonomous_fragments,
                    autonomous_visible=ports.indicators.autonomous_visible,
                    status_fragments=ports.indicators.status_fragments,
                    status_visible=ports.indicators.status_visible,
                ),
                register_extra_keybindings=ports.extensions.register_extra_keybindings,
                composition=TuiCompositionPorts(
                    cursor=ports.extensions.composition.cursor,
                    store_application=ports.extensions.composition.store_application,
                    install_resize_cleanup=ports.extensions.composition.install_resize_cleanup,
                    input=ports.extensions.composition.input,
                    output=ports.extensions.composition.output,
                ),
                extra_widgets=ports.extensions.extra_widgets,
            )
        ).build()
