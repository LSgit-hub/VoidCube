"""Protect the interactive CLI lifecycle at process and terminal boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CliLifecycleGuardPorts:
    """Process, event-loop and stdin operations supplied by the host."""

    install_signal: Callable[[object, Callable[[int, object], None]], None]
    sigterm: object
    sighup: object | None
    get_running_loop: Callable[[], object]
    new_event_loop: Callable[[], object]
    set_event_loop: Callable[[object], None]
    fstat_stdin: Callable[[], None]
    report_stdin_unavailable: Callable[[], None]
    cleanup_after_stdin_failure: Callable[[], None]
    print_exit_summary: Callable[[], None]
    log_signal: Callable[[int], None]


class CliLifecycleGuardRuntime:
    """Own lifecycle guard policy without accessing the CLI host."""

    def __init__(self, ports: CliLifecycleGuardPorts) -> None:
        self.ports = ports

    def install_signal_handlers(self) -> None:
        def signal_handler(signum: int, _frame: object) -> None:
            self.ports.log_signal(signum)
            raise KeyboardInterrupt()

        try:
            self.ports.install_signal(self.ports.sigterm, signal_handler)
            if self.ports.sighup is not None:
                self.ports.install_signal(self.ports.sighup, signal_handler)
        except Exception:
            # Signal registration is unavailable in embedded/restricted hosts.
            pass

    def install_asyncio_exception_handler(self) -> None:
        try:
            try:
                loop = self.ports.get_running_loop()
            except RuntimeError:
                loop = self.ports.new_event_loop()
                self.ports.set_event_loop(loop)
            loop.set_exception_handler(self.asyncio_exception_handler)
        except Exception:
            pass

    def asyncio_exception_handler(
        self,
        loop: object,
        context: Mapping[str, object],
    ) -> None:
        exception = context.get("exception")
        if self.should_suppress_asyncio_exception(exception):
            return
        loop.default_exception_handler(context)

    @staticmethod
    def should_suppress_asyncio_exception(exception: object) -> bool:
        if isinstance(exception, RuntimeError) and "Event loop is closed" in str(exception):
            return True
        return isinstance(exception, KeyError) and "is not registered" in str(exception)

    def validate_stdin(self) -> bool:
        try:
            self.ports.fstat_stdin()
        except OSError:
            self.ports.report_stdin_unavailable()
            self.ports.cleanup_after_stdin_failure()
            self.ports.print_exit_summary()
            return False
        return True

    @staticmethod
    def is_unusable_stdin_error(error: BaseException) -> bool:
        message = str(error)
        return "is not registered" in message or "Bad file descriptor" in message
