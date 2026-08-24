"""Install the interactive TUI keybinding adapters from explicit ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.keys import Keys

from .keybindings import (
    install_history_navigation_keybindings,
    install_text_editing_keybindings,
)
from .modal_navigation import (
    ModalNavigationPorts,
    install_modal_navigation_keybindings,
)


@dataclass(frozen=True, slots=True)
class TuiKeybindingAssemblyPorts:
    """Bindings and already-owned adapters supplied by the CLI host."""

    key_bindings: Any
    enter: Callable[[Any], None]
    ctrl_z: Callable[[Any], None]
    voice_key: str
    voice: Callable[[Any], None]
    paste: Any
    modal_navigation: ModalNavigationPorts
    normal_input_active: Callable[[], bool]


class TuiKeybindingAssemblyRuntime:
    """Register the interactive keybinding surface without owning CLI state."""

    def __init__(self, ports: TuiKeybindingAssemblyPorts) -> None:
        self.ports = ports

    def install(self) -> None:
        ports = self.ports
        key_bindings = ports.key_bindings

        @key_bindings.add("enter")
        def handle_enter(event: Any) -> None:
            ports.enter(event)

        install_text_editing_keybindings(
            key_bindings,
            normal_input_active=ports.normal_input_active,
        )
        install_modal_navigation_keybindings(
            key_bindings,
            ports=ports.modal_navigation,
        )
        install_history_navigation_keybindings(
            key_bindings,
            normal_input_active=ports.normal_input_active,
        )

        @key_bindings.add("c-z")
        def handle_ctrl_z(event: Any) -> None:
            ports.ctrl_z(event)

        @key_bindings.add(ports.voice_key)
        def handle_voice_record(event: Any) -> None:
            ports.voice(event)

        @key_bindings.add(Keys.BracketedPaste, eager=True)
        def handle_paste(event: Any) -> None:
            ports.paste.handle_bracketed_paste(event)
