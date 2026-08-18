"""Execute dynamic CLI command routes through explicit host ports."""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable, Mapping, Set
from dataclasses import dataclass
from typing import Any

from VoidCube_cli.command_router import (
    ParsedCliCommand,
    resolve_dynamic_command,
)


@dataclass(frozen=True, slots=True)
class CliDynamicCommandPorts:
    """Dynamic command sources and terminal operations supplied by the host."""

    custom_commands: Mapping[str, Mapping[str, Any]]
    plugin_names: Set[str]
    skill_commands: Mapping[str, Any]
    get_plugin_handler: Callable[[str], Callable[[str], Any] | None]
    build_skill_message: Callable[[str, str, str], str | None]
    session_id: Callable[[], str]
    enqueue_pending_input: Callable[[str], None]
    emit: Callable[[str], None]
    emit_markup: Callable[[str], None]


class CliDynamicCommandRuntime:
    """Resolve and execute non-built-in CLI commands without CLI state access."""

    def __init__(self, ports: CliDynamicCommandPorts) -> None:
        self.ports = ports

    def run(self, request: ParsedCliCommand) -> bool:
        ports = self.ports
        route = resolve_dynamic_command(
            request,
            custom_commands=ports.custom_commands,
            plugin_names=ports.plugin_names,
            skill_commands=ports.skill_commands,
        )

        if route.kind == "custom_exec":
            self._run_custom_exec(route.executable)
        elif route.kind == "custom_invalid":
            self._report_invalid_custom_command(request, route.custom_type)
        elif route.kind == "plugin":
            self._run_plugin(request)
        elif route.kind == "skill":
            self._run_skill(request)
        else:
            ports.emit(f"\033[1;31mUnknown command: {request.normalized}\033[0m")
            ports.emit("\033[2m\033[1;38;2;218;165;32mType /help for available commands\033[0m")
        return True

    def _run_custom_exec(self, executable: str) -> None:
        try:
            result = subprocess.run(
                shlex.split(executable),
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout.strip() or result.stderr.strip()
            self.ports.emit_markup(output or "[dim]Command returned no output[/]")
        except subprocess.TimeoutExpired:
            self.ports.emit_markup("[bold red]Custom command timed out (30s)[/]")
        except Exception as error:
            self.ports.emit_markup(f"[bold red]Custom command error: {error}[/]")

    def _report_invalid_custom_command(self, request: ParsedCliCommand, custom_type: str) -> None:
        if custom_type == "exec":
            self.ports.emit_markup(
                f"[bold red]Custom command '{request.base_token}' has no command defined[/]"
            )
        else:
            self.ports.emit_markup(
                f"[bold red]Custom command '{request.base_token}' has unsupported type "
                "(supported: 'exec')"
            )

    def _run_plugin(self, request: ParsedCliCommand) -> None:
        handler = self.ports.get_plugin_handler(request.name)
        if not handler:
            return
        try:
            result = handler(request.arguments)
            if result:
                self.ports.emit(str(result))
        except Exception as error:
            self.ports.emit(f"\033[1;31mPlugin command error: {error}\033[0m")

    def _run_skill(self, request: ParsedCliCommand) -> None:
        message = self.ports.build_skill_message(
            request.base_token,
            request.arguments,
            self.ports.session_id(),
        )
        if message:
            skill_name = self.ports.skill_commands[request.base_token]["name"]
            self.ports.emit(f"\n🔧 Loading skill: {skill_name}")
            self.ports.enqueue_pending_input(message)
        else:
            self.ports.emit_markup(
                f"[bold red]Failed to load skill for {request.base_token}[/]"
            )
