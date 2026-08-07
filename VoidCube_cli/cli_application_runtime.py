"""Own the prompt-toolkit application wait loop and interactive shutdown edge."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CliApplicationPorts:
    """Process and TUI operations supplied by the interactive CLI host."""

    register_exit_cleanup: Callable[[Callable[[], None]], Any]
    cleanup: Callable[[], None]
    install_signal_handlers: Callable[[], None]
    validate_stdin: Callable[[], bool]
    install_asyncio_exception_handler: Callable[[], None]
    stdout_context: Callable[[], AbstractContextManager[Any]]
    run_application: Callable[[], None]
    is_unusable_stdin_error: Callable[[BaseException], bool]
    report_unusable_stdin: Callable[[BaseException], None]
    request_stop: Callable[[], None]
    teardown: Callable[[], None]


class CliApplicationRuntime:
    """Run the interactive application while preserving its shutdown contract."""

    def __init__(self, ports: CliApplicationPorts) -> None:
        self.ports = ports

    def run(self) -> None:
        ports = self.ports
        ports.register_exit_cleanup(ports.cleanup)
        ports.install_signal_handlers()

        if not ports.validate_stdin():
            return

        try:
            with ports.stdout_context():
                ports.install_asyncio_exception_handler()
                ports.run_application()
        except (EOFError, BrokenPipeError):
            pass
        except (KeyError, OSError) as error:
            if ports.is_unusable_stdin_error(error):
                ports.report_unusable_stdin(error)
            else:
                raise
        finally:
            ports.request_stop()
            ports.teardown()
