from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from .router import ParsedCliCommand, slow_command_status


@dataclass(frozen=True, slots=True)
class BuiltinCommandSpec:
    handler_name: str = ""
    handler_key: str = ""
    pass_original: bool = False
    busy: bool = False
    exits: bool = False


@dataclass(frozen=True, slots=True)
class BuiltinExecutionResult:
    handled: bool
    continue_running: bool = True


BUILTIN_COMMAND_SPECS = MappingProxyType(
    {
        "quit": BuiltinCommandSpec(exits=True),
        "help": BuiltinCommandSpec(handler_key="help"),
        "doctor": BuiltinCommandSpec(handler_key="doctor"),
        "api": BuiltinCommandSpec(handler_key="api"),
        "profile": BuiltinCommandSpec(handler_key="profile"),
        "tools": BuiltinCommandSpec(handler_key="tools"),
        "toolsets": BuiltinCommandSpec(handler_key="toolsets"),
        "config": BuiltinCommandSpec(handler_key="config"),
        "clear": BuiltinCommandSpec(handler_key="clear"),
        "history": BuiltinCommandSpec(handler_key="history"),
        "goal": BuiltinCommandSpec(handler_key="goal"),
        "find": BuiltinCommandSpec(handler_key="find"),
        "export": BuiltinCommandSpec(handler_key="export"),
        "title": BuiltinCommandSpec(handler_key="title"),
        "new": BuiltinCommandSpec(handler_key="new"),
        "resume": BuiltinCommandSpec(handler_key="resume"),
        "model": BuiltinCommandSpec(handler_key="model"),
        "provider": BuiltinCommandSpec(handler_key="provider"),
        "memory": BuiltinCommandSpec(handler_key="memory"),
        "personality": BuiltinCommandSpec(handler_key="personality"),
        "auto": BuiltinCommandSpec(handler_key="auto"),
        "auto-q": BuiltinCommandSpec(handler_key="auto-q"),
        "plan": BuiltinCommandSpec(handler_key="plan"),
        "retry": BuiltinCommandSpec(handler_key="retry"),
        "undo": BuiltinCommandSpec(handler_key="undo"),
        "branch": BuiltinCommandSpec(handler_key="branch"),
        "save": BuiltinCommandSpec(handler_key="save"),
        "skills": BuiltinCommandSpec(handler_key="skills", busy=True),
        "mcp": BuiltinCommandSpec(handler_key="mcp"),
        "status": BuiltinCommandSpec(handler_key="status"),
        "cancel": BuiltinCommandSpec(handler_key="cancel"),
        "tasks": BuiltinCommandSpec(handler_key="tasks"),
        "statusbar": BuiltinCommandSpec(handler_key="statusbar"),
        "verbose": BuiltinCommandSpec(handler_key="verbose"),
        "yolo": BuiltinCommandSpec("_toggle_yolo"),
        "reasoning": BuiltinCommandSpec(handler_key="reasoning"),
        "fast": BuiltinCommandSpec(handler_key="fast"),
        "compress": BuiltinCommandSpec(handler_key="compress"),
        "usage": BuiltinCommandSpec(handler_key="usage"),
        "debug": BuiltinCommandSpec(handler_key="debug"),
        "paste": BuiltinCommandSpec(handler_key="paste"),
        "image": BuiltinCommandSpec(handler_key="image"),
        "reload-mcp": BuiltinCommandSpec(handler_key="reload-mcp", busy=True),
        "browser": BuiltinCommandSpec(handler_key="browser"),
        "plugins": BuiltinCommandSpec(handler_key="plugins"),
        "rollback": BuiltinCommandSpec(handler_key="rollback"),
        "stop": BuiltinCommandSpec(handler_key="stop"),
        "background": BuiltinCommandSpec(handler_key="background"),
        "btw": BuiltinCommandSpec(handler_key="btw"),
        "queue": BuiltinCommandSpec(handler_key="queue"),
        "language": BuiltinCommandSpec(handler_key="language"),
        "voice": BuiltinCommandSpec(handler_key="voice"),
        "preset": BuiltinCommandSpec(handler_key="preset"),
    }
)


class CommandBusyLifecycle:
    """Own temporary CLI busy state, including nested and exceptional exits."""

    def __init__(self, host: Any) -> None:
        self._host = host

    @contextmanager
    def activate(self, status: str) -> Iterator[None]:
        previous_running = bool(getattr(self._host, "_command_running", False))
        previous_status = str(getattr(self._host, "_command_status", "") or "")
        self._set_state(True, status)
        try:
            print(f"⏳ {status}")
            yield
        finally:
            self._set_state(previous_running, previous_status)

    def reset(self) -> None:
        self._set_state(False, "")

    def _set_state(self, running: bool, status: str) -> None:
        self._host._command_running = running
        self._host._command_status = status
        self._host._invalidate(min_interval=0.0)


CommandHandler = Callable[[ParsedCliCommand], None]


class BuiltinCommandExecutor:
    """Execute registered built-in commands through one declarative table."""

    def __init__(
        self,
        host: Any,
        busy_lifecycle: CommandBusyLifecycle,
        command_handlers: Mapping[str, CommandHandler] | None = None,
    ) -> None:
        self._host = host
        self._busy_lifecycle = busy_lifecycle
        self._command_handlers = dict(command_handlers or {})

    def execute(self, request: ParsedCliCommand) -> BuiltinExecutionResult:
        spec = BUILTIN_COMMAND_SPECS.get(request.canonical)
        if spec is None:
            return BuiltinExecutionResult(handled=False)
        if spec.exits:
            return BuiltinExecutionResult(handled=True, continue_running=False)

        def invoke() -> None:
            if spec.handler_key:
                self._command_handlers[spec.handler_key](request)
            else:
                handler = getattr(self._host, spec.handler_name)
                if spec.pass_original:
                    handler(request.original)
                else:
                    handler()

        if spec.busy:
            with self._busy_lifecycle.activate(slow_command_status(request)):
                invoke()
        else:
            invoke()
        return BuiltinExecutionResult(handled=True)


def initialize_command_execution(
    host: Any,
    *,
    command_handlers: Mapping[str, CommandHandler] | None = None,
) -> None:
    """Install the command lifecycle and executor owned by a CLI host."""
    lifecycle = CommandBusyLifecycle(host)
    host._command_busy_lifecycle = lifecycle
    host._builtin_command_executor = BuiltinCommandExecutor(
        host,
        lifecycle,
        command_handlers,
    )
