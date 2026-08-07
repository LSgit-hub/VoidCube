from types import SimpleNamespace

import VoidCube_cli.cli_interactive_lifecycle_runtime as lifecycle_module
from VoidCube_cli.cli_interactive_lifecycle_runtime import (
    CliInteractiveLifecyclePorts,
    CliInteractiveLifecycleRuntime,
)


def test_lifecycle_runtime_connects_loop_and_application_owners(monkeypatch):
    calls = []
    captured = {}

    class FakeRunRuntime:
        def __init__(self, ports):
            captured["run"] = ports

        def start(self):
            calls.append("start-loop")

    class FakeApplicationRuntime:
        def __init__(self, ports):
            captured["application"] = ports

        def run(self):
            calls.append("run-application")

    class FakeIdleRuntime:
        def __init__(self, ports):
            captured["idle"] = ports

        def run_once(self):
            calls.append("idle")

    monkeypatch.setattr(lifecycle_module, "CliRunRuntime", FakeRunRuntime)
    monkeypatch.setattr(lifecycle_module, "CliIdleMaintenanceRuntime", FakeIdleRuntime)
    monkeypatch.setattr(
        lifecycle_module,
        "CliApplicationRuntime",
        FakeApplicationRuntime,
    )

    idle = SimpleNamespace()
    guards = SimpleNamespace(
        install_signal_handlers=lambda: calls.append("signals"),
        validate_stdin=lambda: True,
        install_asyncio_exception_handler=lambda: calls.append("asyncio"),
        is_unusable_stdin_error=lambda _error: False,
    )
    application = SimpleNamespace(
        run=lambda **kwargs: calls.append(("application-run", kwargs))
    )

    runtime = CliInteractiveLifecycleRuntime(
        CliInteractiveLifecyclePorts(
            application=application,
            idle_maintenance=idle,
            lifecycle_guards=guards,
            stop_requested=lambda: False,
            presence_refresh_needed=lambda: False,
            refresh_presence=lambda: None,
            command_running=lambda: False,
            invalidate=lambda _interval: None,
            poll_scheduled_workflow=lambda: None,
            get_pending_input=lambda _timeout: None,
            empty_input=Exception,
            execute_input=lambda _value: None,
            report_input_error=lambda _error: None,
            sleep=lambda _seconds: None,
            monotonic_time=lambda: 0.0,
            thread_factory=object,
            register_exit_cleanup=lambda _callback: None,
            cleanup=lambda: None,
            stdout_context=lambda: None,
            report_unusable_stdin=lambda _error: None,
            request_stop=lambda: None,
            teardown=lambda: None,
        )
    )

    runtime.run()

    assert calls == ["start-loop", "run-application"]
    captured["run"].perform_idle_maintenance()
    assert calls[-1] == "idle"
    assert captured["idle"] is idle
    assert captured["run"].application_ready() is True
    captured["application"].run_application()
    assert calls[-1] == ("application-run", {"handle_sigint": False})
    assert captured["application"].install_signal_handlers is guards.install_signal_handlers
