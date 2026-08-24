from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import voidcube.interfaces.cli.tui.composition as assembly_module
import pytest
from voidcube.interfaces.cli.tui.composition import (
    CliInteractiveTuiAssemblyRuntime,
    CliInteractiveTuiPorts,
    CliInteractiveTuiStatePorts,
)
from voidcube.interfaces.cli.lifecycle.registration import CliInteractiveRegistrations


def _ports() -> CliInteractiveTuiPorts:
    registrations = CliInteractiveRegistrations(
        enter=SimpleNamespace(handle="enter"),
        voice=SimpleNamespace(handle="voice"),
        suspend=SimpleNamespace(handle="suspend"),
        dynamic_text=SimpleNamespace(placeholder=lambda: "placeholder"),
        voice_key="c-b",
    )
    prompt_runtime = SimpleNamespace(
        fragments=lambda: [],
        text=lambda: "> ",
    )
    layout_metrics = SimpleNamespace(
        input_rule_height=lambda: 1,
        extended_panels_visible=lambda: True,
        status_bar_visible=lambda: True,
    )
    state = CliInteractiveTuiStatePorts(
        clarify_state=lambda: None,
        clarify_freetext_active=lambda: False,
        sudo_state=lambda: None,
        secret_state=lambda: None,
        approval_state=lambda: None,
        model_picker_state=lambda: None,
        update_selection=lambda mutate: mutate(),
    )
    return CliInteractiveTuiPorts(
        registrations=registrations,
        prompt_runtime=prompt_runtime,
        layout_metrics=layout_metrics,
        state=state,
        attached_images=lambda: [],
        image_counter=lambda: 0,
        format_image_badges=lambda _images: [],
        voice_fragments=lambda: [],
        voice_visible=lambda: False,
        autonomous_fragments=lambda: [],
        autonomous_visible=lambda: False,
        status_fragments=lambda: [],
        status_visible=lambda: False,
        should_attach_clipboard_image=lambda _text: False,
        attach_clipboard_image=lambda: False,
        paste_directory=Path("pastes"),
        timestamp=lambda: "000000",
        invalidate_event=lambda _event: None,
        invalidate=lambda: None,
        history_path="history",
        command_available=lambda _command: True,
        command_running=lambda: False,
        approval_fragments=lambda: [],
        register_extra_keybindings=lambda *_args, **_kwargs: None,
        cursor=None,
        store_application=lambda _application: None,
        install_resize_cleanup=lambda _application: None,
        extra_widgets=lambda: [],
    )


def test_tui_assembly_maps_host_ports_to_factory(monkeypatch):
    captured: dict[str, object] = {}

    class FakeIndicators:
        def __init__(self, ports):
            captured["indicator_ports"] = ports

        def build(self):
            return "indicators"

    class FakeHostAssembly:
        def __init__(self, ports):
            captured["host_ports"] = ports

        def build(self):
            return "application"

    monkeypatch.setattr(assembly_module, "CliTuiIndicatorAssemblyRuntime", FakeIndicators)
    monkeypatch.setattr(assembly_module, "CliTuiHostAssemblyRuntime", FakeHostAssembly)

    ports = _ports()
    assert CliInteractiveTuiAssemblyRuntime(ports).build() == "application"

    host_ports = captured["host_ports"]
    assert host_ports.registrations is ports.registrations
    assert host_ports.input.history_path == "history"
    assert host_ports.input.prompt_text() == "> "
    assert host_ports.extensions.composition.cursor is None
    assert host_ports.indicators == "indicators"
    assert host_ports.modal_navigation.model_picker_state() is None


def test_tui_assembly_projects_modal_input_policy():
    base = _ports()
    ports = replace(
        base,
        state=CliInteractiveTuiStatePorts(
            clarify_state=base.state.clarify_state,
            clarify_freetext_active=base.state.clarify_freetext_active,
            sudo_state=base.state.sudo_state,
            secret_state=base.state.secret_state,
            approval_state=base.state.approval_state,
            model_picker_state=lambda: {"stage": "provider"},
            update_selection=lambda mutate: mutate(),
        ),
    )
    captured: dict[str, object] = {}

    class FakeHostAssembly:
        def __init__(self, host_ports):
            captured["host_ports"] = host_ports

        def build(self):
            return object()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(assembly_module, "CliTuiHostAssemblyRuntime", FakeHostAssembly)
    monkeypatch.setattr(
        assembly_module,
        "CliTuiIndicatorAssemblyRuntime",
        lambda _ports: SimpleNamespace(build=lambda: object()),
    )
    try:
        CliInteractiveTuiAssemblyRuntime(ports).build()
    finally:
        monkeypatch.undo()

    host_ports = captured["host_ports"]
    assert host_ports.normal_input_active() is False
    assert host_ports.input.input_locked() is True
