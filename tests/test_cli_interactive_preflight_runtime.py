from __future__ import annotations

import voidcube.interfaces.cli.lifecycle.preflight as runtime_module
from voidcube.interfaces.cli.lifecycle.preflight import (
    CliInteractivePreflightPorts,
    CliInteractivePreflightRuntime,
)


def test_interactive_preflight_preserves_registration_order(monkeypatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        runtime_module,
        "ensure_tirith_security_scanner",
        lambda: calls.append("security"),
    )

    CliInteractivePreflightRuntime(
        CliInteractivePreflightPorts(
            register_plugin_cli=lambda: calls.append("plugin"),
            reset_command_lifecycle=lambda: calls.append("busy-reset"),
            register_sudo_password_callback=lambda value: calls.append(("sudo", value)),
            register_approval_sink=lambda value: calls.append(("approval", value)),
            register_secret_capture_callback=lambda value: calls.append(("secret", value)),
            sudo_password_callback="sudo-callback",
            approval_sink="approval-sink",
            secret_capture_callback="secret-callback",
        )
    ).prepare()

    assert calls == [
        "plugin",
        "busy-reset",
        ("sudo", "sudo-callback"),
        ("approval", "approval-sink"),
        ("secret", "secret-callback"),
        "security",
    ]
