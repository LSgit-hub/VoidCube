from types import SimpleNamespace

import VoidCube_cli.cli_interactive_registration_runtime as registration_module
from VoidCube_cli.cli_interactive_registration_runtime import (
    CliInteractiveRegistrationPorts,
    CliInteractiveRegistrationRuntime,
)


def _ports(calls, *, voice_key="ctrl+b", load_error=None):
    class FakePreflight:
        def __init__(self, ports):
            calls.append(("preflight", ports))

        def prepare(self):
            calls.append("preflight-prepare")

    values = dict(
        register_plugin_cli=lambda: calls.append("plugin"),
        reset_command_lifecycle=lambda: calls.append("reset"),
        register_sudo_password_callback=lambda value: calls.append(("sudo-register", value)),
        register_approval_sink=lambda value: calls.append(("approval-register", value)),
        register_secret_capture_callback=lambda value: calls.append(("secret-register", value)),
        sudo_password_callback=lambda: None,
        approval_sink=lambda: None,
        secret_capture_callback=lambda: None,
        create_enter_runtime=lambda: calls.append("enter") or SimpleNamespace(),
        create_control_runtime=lambda: calls.append("control") or SimpleNamespace(),
        create_voice_runtime=lambda: calls.append("voice") or SimpleNamespace(),
        create_suspend_runtime=lambda: calls.append("suspend") or SimpleNamespace(),
        create_dynamic_text_runtime=lambda: calls.append("dynamic") or SimpleNamespace(),
        load_voice_record_key=lambda: (
            (_ for _ in ()).throw(load_error) if load_error is not None else voice_key
        ),
    )
    return FakePreflight, CliInteractiveRegistrationPorts(**values)


def test_registration_runtime_prepares_preflight_before_building_runtimes(monkeypatch):
    calls = []
    fake_preflight, ports = _ports(calls, voice_key="alt+v")
    monkeypatch.setattr(registration_module, "CliInteractivePreflightRuntime", fake_preflight)

    result = CliInteractiveRegistrationRuntime(ports).prepare()

    assert calls[0][0] == "preflight"
    assert calls[1:] == ["preflight-prepare", "enter", "control", "voice", "suspend", "dynamic"]
    assert result.voice_key == "a-v"


def test_registration_runtime_uses_safe_voice_key_fallback(monkeypatch):
    calls = []
    fake_preflight, ports = _ports(calls, load_error=RuntimeError("config unavailable"))
    monkeypatch.setattr(registration_module, "CliInteractivePreflightRuntime", fake_preflight)

    result = CliInteractiveRegistrationRuntime(ports).prepare()

    assert result.voice_key == "c-b"
