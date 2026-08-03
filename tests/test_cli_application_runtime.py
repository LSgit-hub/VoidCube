from __future__ import annotations

from contextlib import contextmanager

from VoidCube_cli.cli_application_runtime import CliApplicationPorts, CliApplicationRuntime


def _runtime(calls: list[str], *, stdin_ok: bool = True, application_error=None):
    @contextmanager
    def stdout_context():
        calls.append("stdout-enter")
        yield
        calls.append("stdout-exit")

    def run_application():
        calls.append("application")
        if application_error is not None:
            raise application_error

    return CliApplicationRuntime(
        CliApplicationPorts(
            register_exit_cleanup=lambda cleanup: calls.append("register") or cleanup,
            cleanup=lambda: calls.append("cleanup"),
            install_signal_handlers=lambda: calls.append("signals"),
            validate_stdin=lambda: stdin_ok,
            install_asyncio_exception_handler=lambda: calls.append("asyncio"),
            stdout_context=stdout_context,
            run_application=run_application,
            is_unusable_stdin_error=lambda error: "stdin" in str(error),
            report_unusable_stdin=lambda error: calls.append(f"report:{error}"),
            request_stop=lambda: calls.append("stop"),
            teardown=lambda: calls.append("teardown"),
        )
    )


def test_application_runtime_waits_and_tears_down_in_order() -> None:
    calls: list[str] = []

    _runtime(calls).run()

    assert calls == [
        "register",
        "signals",
        "stdout-enter",
        "asyncio",
        "application",
        "stdout-exit",
        "stop",
        "teardown",
    ]


def test_application_runtime_preserves_unusable_stdin_fallback() -> None:
    calls: list[str] = []

    _runtime(calls, application_error=OSError("stdin is not usable")).run()

    assert calls[-3:] == ["report:stdin is not usable", "stop", "teardown"]


def test_application_runtime_does_not_teardown_after_stdin_preflight_failure() -> None:
    calls: list[str] = []

    _runtime(calls, stdin_ok=False).run()

    assert calls == ["register", "signals"]
