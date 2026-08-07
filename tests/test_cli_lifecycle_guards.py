from types import SimpleNamespace

from VoidCube_cli.cli_lifecycle_guards import (
    CliLifecycleGuardPorts,
    CliLifecycleGuardRuntime,
)


def _runtime(calls, *, sighup=True, stdin_ok=True):
    loop = SimpleNamespace(
        handlers=[],
        set_exception_handler=lambda handler: loop.handlers.append(handler),
        default_exception_handler=lambda context: calls.append(("default", context)),
    )
    return CliLifecycleGuardRuntime(
        CliLifecycleGuardPorts(
            install_signal=lambda name, handler: calls.append(("signal", name, handler)),
            sigint="INT",
            sigint_ignore="IGNORE",
            sigterm="TERM",
            sighup="HUP" if sighup else None,
            get_running_loop=lambda: loop,
            new_event_loop=lambda: loop,
            set_event_loop=lambda value: calls.append(("set-loop", value)),
            fstat_stdin=lambda: (_ for _ in ()).throw(OSError("stdin bad"))
            if not stdin_ok
            else None,
            report_stdin_unavailable=lambda: calls.append("report-stdin"),
            cleanup_after_stdin_failure=lambda: calls.append("cleanup"),
            print_exit_summary=lambda: calls.append("summary"),
            log_signal=lambda signum: calls.append(("log-signal", signum)),
        ),
    ), loop


def test_signal_installation_leaves_terminal_interrupt_unhandled():
    calls = []
    runtime, _loop = _runtime(calls)
    runtime.install_signal_handlers()

    assert [call[:2] for call in calls] == [
        ("signal", "INT"),
        ("signal", "TERM"),
        ("signal", "HUP"),
    ]
    assert calls[0][2] == "IGNORE"
    handler = calls[1][2]
    try:
        handler(15, object())
    except SystemExit as error:
        assert error.code == 143
    else:
        raise AssertionError("termination signal must stop the application")
    assert ("log-signal", 15) in calls


def test_asyncio_handler_suppresses_known_cleanup_errors_and_delegates_other_errors():
    calls = []
    runtime, loop = _runtime(calls)
    runtime.install_asyncio_exception_handler()
    handler = loop.handlers[0]

    handler(loop, {"exception": RuntimeError("Event loop is closed")})
    handler(loop, {"exception": KeyError("0 is not registered")})
    handler(loop, {"exception": RuntimeError("unexpected")})

    assert len(calls) == 1
    assert calls[0][0] == "default"
    assert isinstance(calls[0][1]["exception"], RuntimeError)
    assert str(calls[0][1]["exception"]) == "unexpected"


def test_stdin_preflight_cleans_up_and_reports_failure():
    calls = []
    runtime, _loop = _runtime(calls, stdin_ok=False)
    assert runtime.validate_stdin() is False
    assert calls == ["report-stdin", "cleanup", "summary"]

    assert runtime.is_unusable_stdin_error(KeyError("0 is not registered"))
    assert runtime.is_unusable_stdin_error(OSError("Bad file descriptor"))
    assert not runtime.is_unusable_stdin_error(OSError("permission denied"))
