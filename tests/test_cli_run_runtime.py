from __future__ import annotations

import voidcube.interfaces.cli.lifecycle.run as runtime_module
from voidcube.interfaces.cli.lifecycle.run import CliRunRuntime, CliRunRuntimePorts


def test_cli_run_runtime_starts_all_long_lived_loops(monkeypatch):
    calls = []

    monkeypatch.setattr(
        runtime_module,
        "start_tui_refresh_loop",
        lambda **kwargs: calls.append(("refresh", kwargs)),
    )
    monkeypatch.setattr(
        runtime_module,
        "start_scheduled_task_polling",
        lambda **kwargs: calls.append(("scheduled", kwargs)),
    )
    monkeypatch.setattr(
        runtime_module,
        "start_input_process_loop",
        lambda **kwargs: calls.append(("input", kwargs)),
    )

    runtime = CliRunRuntime(
        CliRunRuntimePorts(
            stop_requested=lambda: False,
            application_ready=lambda: True,
            refresh_status=lambda: None,
            presence_refresh_needed=lambda: False,
            refresh_presence=lambda: None,
            command_running=lambda: False,
            invalidate=lambda _interval: None,
            poll_scheduled_workflow=lambda: None,
            perform_idle_maintenance=lambda: None,
            get_pending_input=lambda _timeout: None,
            empty_input=TimeoutError,
            execute_input=lambda _value: None,
            report_input_error=lambda _error: None,
            sleep=lambda _seconds: None,
            monotonic_time=lambda: 0.0,
            thread_factory=lambda **_kwargs: None,
        )
    )

    runtime.start()

    assert [name for name, _kwargs in calls] == ["refresh", "scheduled", "input"]
    assert calls[0][1]["stop_requested"]() is False
    assert calls[1][1]["poll_workflow"] is not None
