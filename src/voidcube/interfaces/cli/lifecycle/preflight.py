"""Install interactive callbacks and perform non-fatal CLI security preflight."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliInteractivePreflightPorts:
    """Host callbacks needed before constructing the interactive TUI."""

    register_plugin_cli: Callable[[], None]
    reset_command_lifecycle: Callable[[], None]
    register_sudo_password_callback: Callable[[Any], Any]
    register_approval_sink: Callable[[Any], Any]
    register_secret_capture_callback: Callable[[Any], Any]
    sudo_password_callback: Any
    approval_sink: Any
    secret_capture_callback: Any


def ensure_tirith_security_scanner() -> None:
    """Best-effort security scanner installation for interactive commands."""
    try:
        from ....infrastructure.execution.tirith_security import ensure_installed

        ensure_installed(log_failures=False)
    except Exception:
        # Security scanning remains fail-open when the optional binary is absent.
        pass


class CliInteractivePreflightRuntime:
    """Preserve interactive callback and preflight ordering without host access."""

    def __init__(self, ports: CliInteractivePreflightPorts) -> None:
        self.ports = ports

    def prepare(self) -> None:
        ports = self.ports
        ports.register_plugin_cli()
        ports.reset_command_lifecycle()
        ports.register_sudo_password_callback(ports.sudo_password_callback)
        ports.register_approval_sink(ports.approval_sink)
        ports.register_secret_capture_callback(ports.secret_capture_callback)
        ensure_tirith_security_scanner()
