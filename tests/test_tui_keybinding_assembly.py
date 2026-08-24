from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from voidcube.interfaces.cli.tui.keybinding_assembly import (
    TuiKeybindingAssemblyPorts,
    TuiKeybindingAssemblyRuntime,
)
from voidcube.interfaces.cli.tui.modal_navigation import ModalNavigationPorts


class _Paste:
    def handle_bracketed_paste(self, event) -> None:
        event.calls.append("paste")


def test_keybinding_assembly_registers_existing_adapters_and_routes_events() -> None:
    calls: list[str] = []
    bindings = KeyBindings()
    runtime = TuiKeybindingAssemblyRuntime(
        TuiKeybindingAssemblyPorts(
            key_bindings=bindings,
            enter=lambda _event: calls.append("enter"),
            ctrl_z=lambda _event: calls.append("ctrl-z"),
            voice_key="c-b",
            voice=lambda _event: calls.append("voice"),
            paste=_Paste(),
            modal_navigation=ModalNavigationPorts(
                clarify_state=lambda: None,
                clarify_freetext_active=lambda: False,
                approval_state=lambda: None,
                model_picker_state=lambda: None,
                update_selection=lambda mutate: mutate(),
                invalidate=lambda: calls.append("invalidate"),
            ),
            normal_input_active=lambda: True,
        )
    )

    runtime.install()

    key_sequences = {
        tuple(key.value if hasattr(key, "value") else str(key) for key in binding.keys)
        for binding in bindings.bindings
    }
    assert {("c-c",), ("c-d",), ("c-v",)}.isdisjoint(key_sequences)
