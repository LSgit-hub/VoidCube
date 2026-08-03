from pathlib import Path
from types import SimpleNamespace

import VoidCube_cli.cli_tui_host_assembly_runtime as assembly_module
from VoidCube_cli.cli_interactive_registration_runtime import CliInteractiveRegistrations
from VoidCube_cli.cli_tui_host_assembly_runtime import (
    CliTuiCompositionPorts,
    CliTuiHostAssemblyPorts,
    CliTuiHostAssemblyRuntime,
    CliTuiIndicatorPorts,
    CliTuiInputPorts,
    CliTuiModalNavigationPorts,
    CliTuiModalPorts,
    CliTuiPastePorts,
)


def test_host_assembly_maps_cli_registrations_and_widget_ports(monkeypatch):
    captured = {}

    class FakeFactory:
        def __init__(self, ports):
            captured["ports"] = ports

        def build(self):
            return "application"

    monkeypatch.setattr(assembly_module, "TuiRuntimeFactory", FakeFactory)
    registrations = CliInteractiveRegistrations(
        enter=SimpleNamespace(handle="enter"),
        control=SimpleNamespace(handle_ctrl_c="ctrl-c", handle_ctrl_d="ctrl-d"),
        voice=SimpleNamespace(handle="voice"),
        suspend=SimpleNamespace(handle="suspend"),
        dynamic_text=SimpleNamespace(),
        voice_key="c-b",
    )
    callback = lambda: None
    assembly = CliTuiHostAssemblyRuntime(
        CliTuiHostAssemblyPorts(
            registrations=registrations,
            paste=CliTuiPastePorts(
                should_attach_clipboard_image=lambda _text: False,
                attach_clipboard_image=lambda: False,
                paste_directory=Path("pastes"),
                timestamp=lambda: "000000",
                invalidate=lambda _event: None,
            ),
            modal_navigation=CliTuiModalNavigationPorts(
                clarify_state=callback,
                clarify_freetext_active=lambda: False,
                approval_state=callback,
                model_picker_state=callback,
                invalidate=callback,
            ),
            normal_input_active=lambda: True,
            input=CliTuiInputPorts(
                history_path="history",
                prompt_fragments=lambda: [],
                prompt_text=lambda: "> ",
                command_available=lambda _command: True,
                command_running=lambda: False,
                password_mask_active=lambda: False,
            ),
            placeholder_text=lambda: "",
            modal=CliTuiModalPorts(
                clarify_state=callback,
                clarify_freetext_active=lambda: False,
                sudo_state=callback,
                secret_state=callback,
                approval_state=callback,
                approval_fragments=lambda: [],
                model_picker_state=callback,
            ),
            indicators=CliTuiIndicatorPorts(
                spinner_fragments=lambda: [],
                spinner_height=lambda: 1,
                hint_fragments=lambda: [],
                hint_height=lambda: 1,
                input_rule_height=lambda _position: 1,
                image_fragments=lambda: [],
                images_visible=lambda: False,
                voice_fragments=lambda: [],
                voice_visible=lambda: False,
                autonomous_fragments=lambda: [],
                autonomous_visible=lambda: False,
                status_fragments=lambda: [],
                status_visible=lambda: False,
            ),
            register_extra_keybindings=lambda *_args, **_kwargs: None,
            composition=CliTuiCompositionPorts(
                cursor=None,
                store_application=lambda _application: None,
                install_resize_cleanup=lambda _application: None,
            ),
            extra_widgets=lambda: [],
        )
    )

    assert assembly.build() == "application"
    factory_ports = captured["ports"]
    assert factory_ports.enter == "enter"
    assert factory_ports.ctrl_c == "ctrl-c"
    assert factory_ports.voice_key == "c-b"
    assert factory_ports.input.history_path == "history"
    assert factory_ports.composition.cursor is None
