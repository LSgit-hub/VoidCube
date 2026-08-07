from __future__ import annotations

from VoidCube_cli.pending_input_runtime import (
    PendingInputExecutionPorts,
    PendingInputRuntime,
)


def _runtime(calls):
    return PendingInputRuntime(
        PendingInputExecutionPorts(
            should_emit_scrollback=lambda: False,
            process_command=lambda command: calls.append(("command", command)) or True,
            set_should_exit=lambda value: calls.append(("exit", value)),
            reset_turn_state=lambda: calls.append("reset"),
            submit_turn=lambda payload, app: calls.append(("submit", payload, app)) or True,
            invalidate_app=lambda app: calls.append(("invalidate", app)),
            exit_app=lambda app: calls.append(("app-exit", app)),
            voice_restart_ready=lambda: False,
            restart_voice_recording=lambda: calls.append("voice"),
            enqueue_pending_input=lambda value: calls.append(("enqueue", value)),
            render_markup=lambda value: calls.append(("markup", value)),
            emit=lambda value: calls.append(("emit", value)),
        )
    )


def test_pending_input_runtime_owns_turn_lifecycle_through_ports():
    calls = []

    assert _runtime(calls).execute("hello") is True

    assert calls == [
        ("invalidate", None),
        ("submit", ("hello", None), None),
        "reset",
        ("invalidate", None),
    ]


def test_pending_input_runtime_routes_slash_commands_without_starting_turn():
    calls = []

    assert _runtime(calls).execute("/status") is False

    assert calls == [("command", "/status")]
