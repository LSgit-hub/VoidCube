"""Prepare interactive CLI registrations through explicit host callbacks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .preflight import (
    CliInteractivePreflightPorts,
    CliInteractivePreflightRuntime,
)


@dataclass(frozen=True, slots=True)
class CliInteractiveRegistrationPorts:
    """Preflight operations and runtime factories supplied by the CLI host."""

    register_plugin_cli: Callable[[], None]
    reset_command_lifecycle: Callable[[], None]
    register_sudo_password_callback: Callable[[object], None]
    register_approval_sink: Callable[[object], None]
    register_secret_capture_callback: Callable[[object], None]
    sudo_password_callback: Callable[..., object]
    approval_sink: Callable[..., object]
    secret_capture_callback: Callable[..., object]
    create_enter_runtime: Callable[[], object]
    create_voice_runtime: Callable[[], object]
    create_suspend_runtime: Callable[[], object]
    create_dynamic_text_runtime: Callable[[], object]
    load_voice_record_key: Callable[[], str]


@dataclass(frozen=True, slots=True)
class CliInteractiveRegistrations:
    """Registrations consumed by the TUI and interactive lifecycle host."""

    enter: object
    voice: object
    suspend: object
    dynamic_text: object
    voice_key: str


class CliInteractiveRegistrationRuntime:
    """Apply interactive preflight and create the per-run TUI adapters."""

    def __init__(self, ports: CliInteractiveRegistrationPorts) -> None:
        self.ports = ports

    def prepare(self) -> CliInteractiveRegistrations:
        ports = self.ports
        CliInteractivePreflightRuntime(
            CliInteractivePreflightPorts(
                register_plugin_cli=ports.register_plugin_cli,
                reset_command_lifecycle=ports.reset_command_lifecycle,
                register_sudo_password_callback=ports.register_sudo_password_callback,
                register_approval_sink=ports.register_approval_sink,
                register_secret_capture_callback=ports.register_secret_capture_callback,
                sudo_password_callback=ports.sudo_password_callback,
                approval_sink=ports.approval_sink,
                secret_capture_callback=ports.secret_capture_callback,
            )
        ).prepare()
        return CliInteractiveRegistrations(
            enter=ports.create_enter_runtime(),
            voice=ports.create_voice_runtime(),
            suspend=ports.create_suspend_runtime(),
            dynamic_text=ports.create_dynamic_text_runtime(),
            voice_key=self._resolve_voice_key(ports.load_voice_record_key),
        )

    @staticmethod
    def _resolve_voice_key(load_key: Callable[[], str]) -> str:
        try:
            raw_key = load_key() or "ctrl+b"
            return raw_key.lower().replace("ctrl+", "c-").replace("alt+", "a-")
        except Exception:
            return "c-b"
