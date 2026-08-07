from types import SimpleNamespace

import VoidCube_cli.cli_interactive_lifecycle_assembly_runtime as assembly_module
from VoidCube_cli.cli_interactive_lifecycle_assembly_runtime import (
    CliInteractiveLifecycleAssemblyPorts,
    CliInteractiveLifecycleAssemblyRuntime,
)


def test_lifecycle_assembly_maps_idle_and_forced_presence_refresh(monkeypatch):
    calls = []
    captured = {}

    class FakeLifecycle:
        def __init__(self, ports):
            captured["ports"] = ports

        def run(self):
            calls.append("run")

    monkeypatch.setattr(assembly_module, "CliInteractiveLifecycleRuntime", FakeLifecycle)
    lifecycle_guards = SimpleNamespace()

    runtime = CliInteractiveLifecycleAssemblyRuntime(
        CliInteractiveLifecycleAssemblyPorts(
            application="application",
            lifecycle_guards=lifecycle_guards,
            agent_running=lambda: False,
            check_config_changes=lambda: None,
            refresh_observation_surfaces=lambda refresh: calls.append(
                ("observe", refresh())
            ),
            refresh_gateway_presence=lambda force: calls.append(("presence", force)),
            autonomous_gate_active=lambda: False,
            start_autonomous_component=lambda: None,
            application_ready=lambda: True,
            invalidate=lambda _interval: None,
            enqueue_pending_input=lambda _value: None,
            stop_requested=lambda: False,
            presence_refresh_needed=lambda: False,
            command_running=lambda: False,
            poll_scheduled_workflow=lambda: None,
            get_pending_input=lambda _timeout: None,
            empty_input=TimeoutError,
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
    captured["ports"].refresh_presence()
    captured["ports"].idle_maintenance.refresh_observation_surfaces()

    assert calls == ["run", ("presence", True), ("presence", False), ("observe", None)]
    assert captured["ports"].application == "application"
    assert captured["ports"].lifecycle_guards is lifecycle_guards
