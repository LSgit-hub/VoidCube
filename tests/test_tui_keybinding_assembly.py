from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings

from VoidCube_cli.tui_keybinding_assembly import (
    TuiKeybindingAssemblyPorts,
    TuiKeybindingAssemblyRuntime,
)
from VoidCube_cli.tui_modal_navigation import ModalNavigationPorts


class _Paste:
    def handle_bracketed_paste(self, event) -> None:
        event.calls.append("paste")

    def handle_image_paste(self, event) -> None:
        event.calls.append("image")


def test_keybinding_assembly_registers_existing_adapters_and_routes_events() -> None:
    calls: list[str] = []
    bindings = KeyBindings()
    runtime = TuiKeybindingAssemblyRuntime(
        TuiKeybindingAssemblyPorts(
            key_bindings=bindings,
            enter=lambda _event: calls.append("enter"),
            ctrl_c=lambda _event: calls.append("ctrl-c"),
            ctrl_d=lambda _event: calls.append("ctrl-d"),
            ctrl_z=lambda _event: calls.append("ctrl-z"),
            voice_key="c-b",
            voice=lambda _event: calls.append("voice"),
            paste=_Paste(),
            modal_navigation=ModalNavigationPorts(
                clarify_state=lambda: None,
                clarify_freetext_active=lambda: False,
                approval_state=lambda: None,
                model_picker_state=lambda: None,
                invalidate=lambda: calls.append("invalidate"),
            ),
            normal_input_active=lambda: True,
        )
    )

    runtime.install()

    assert len(bindings.bindings) == 19
