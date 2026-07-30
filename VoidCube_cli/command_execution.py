from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from VoidCube_cli.command_router import ParsedCliCommand, slow_command_status


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
        "help": BuiltinCommandSpec("show_help"),
        "doctor": BuiltinCommandSpec("_handle_doctor_command"),
        "api": BuiltinCommandSpec("_handle_api_command"),
        "profile": BuiltinCommandSpec(handler_key="profile"),
        "tools": BuiltinCommandSpec("_handle_tools_command", pass_original=True),
        "toolsets": BuiltinCommandSpec("show_toolsets"),
        "config": BuiltinCommandSpec("show_config"),
        "clear": BuiltinCommandSpec(handler_key="clear"),
        "history": BuiltinCommandSpec(handler_key="history"),
        "title": BuiltinCommandSpec(handler_key="title"),
        "new": BuiltinCommandSpec(handler_key="new"),
        "resume": BuiltinCommandSpec(handler_key="resume"),
        "model": BuiltinCommandSpec("_handle_model_switch", pass_original=True),
        "provider": BuiltinCommandSpec("_handle_provider_command", pass_original=True),
        "memory": BuiltinCommandSpec("_handle_memory_switch", pass_original=True),
        "personality": BuiltinCommandSpec(
            "_handle_personality_command",
            pass_original=True,
        ),
        "auto": BuiltinCommandSpec("_handle_auto_command", pass_original=True),
        "auto-q": BuiltinCommandSpec("_handle_auto_q_command"),
        "plan": BuiltinCommandSpec("_handle_plan_command", pass_original=True),
        "retry": BuiltinCommandSpec(handler_key="retry"),
        "undo": BuiltinCommandSpec(handler_key="undo"),
        "branch": BuiltinCommandSpec(handler_key="branch"),
        "save": BuiltinCommandSpec(handler_key="save"),
        "skills": BuiltinCommandSpec(
            "_handle_skills_command",
            pass_original=True,
            busy=True,
        ),
        "mcp": BuiltinCommandSpec("_handle_mcp_command", pass_original=True),
        "status": BuiltinCommandSpec("_show_session_status"),
        "tasks": BuiltinCommandSpec("_handle_tasks_command", pass_original=True),
        "statusbar": BuiltinCommandSpec(handler_key="statusbar"),
        "verbose": BuiltinCommandSpec("_toggle_verbose"),
        "yolo": BuiltinCommandSpec("_toggle_yolo"),
        "reasoning": BuiltinCommandSpec(
            "_handle_reasoning_command",
            pass_original=True,
        ),
        "fast": BuiltinCommandSpec("_handle_fast_command", pass_original=True),
        "compress": BuiltinCommandSpec("_manual_compress", pass_original=True),
        "usage": BuiltinCommandSpec("_show_usage"),
        "debug": BuiltinCommandSpec("_handle_debug_command"),
        "paste": BuiltinCommandSpec(handler_key="paste"),
        "image": BuiltinCommandSpec(handler_key="image"),
        "reload-mcp": BuiltinCommandSpec("_reload_mcp", busy=True),
        "browser": BuiltinCommandSpec("_handle_browser_command", pass_original=True),
        "plugins": BuiltinCommandSpec(handler_key="plugins"),
        "rollback": BuiltinCommandSpec(handler_key="rollback"),
        "stop": BuiltinCommandSpec(handler_key="stop"),
        "background": BuiltinCommandSpec(
            "_handle_background_command",
            pass_original=True,
        ),
        "btw": BuiltinCommandSpec("_handle_btw_command", pass_original=True),
        "queue": BuiltinCommandSpec(handler_key="queue"),
        "language": BuiltinCommandSpec(
            "_handle_language_command",
            pass_original=True,
        ),
        "voice": BuiltinCommandSpec("_handle_voice_command", pass_original=True),
        "preset": BuiltinCommandSpec("_handle_preset_command", pass_original=True),
        "connect": BuiltinCommandSpec("_handle_connect_command", pass_original=True),
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
