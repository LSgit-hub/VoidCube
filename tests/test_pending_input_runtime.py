from __future__ import annotations

from voidcube.interfaces.cli.pending_input_runtime import (
    PendingInputExecutionPorts,
    PendingInputRuntime,
)
from voidcube.interfaces.cli.turn.scheduler import TurnCompletion


def _runtime(calls, completions=None, continuations=None, stops=None):
    completions = completions if completions is not None else []
    continuations = continuations if continuations is not None else []
    stops = stops if stops is not None else []
    return PendingInputRuntime(
        PendingInputExecutionPorts(
            should_emit_scrollback=lambda: False,
            process_command=lambda command: calls.append(("command", command)) or True,
            set_should_exit=lambda value: calls.append(("exit", value)),
            reset_turn_state=lambda: calls.append("reset"),
            submit_turn=lambda payload, app, on_finished: (
                calls.append(("submit", payload, app)),
                completions.append(on_finished),
                True,
            )[-1],
            invalidate_app=lambda app: calls.append(("invalidate", app)),
            exit_app=lambda app: calls.append(("app-exit", app)),
            voice_restart_ready=lambda: False,
            restart_voice_recording=lambda: calls.append("voice"),
            enqueue_pending_input=lambda value: calls.append(("enqueue", value)),
            render_markup=lambda value: calls.append(("markup", value)),
            continue_session_goal=lambda: continuations.append("continue"),
            stop_session_goal=lambda reason: stops.append(reason),
            emit=lambda value: calls.append(("emit", value)),
        )
    )


def test_pending_input_runtime_owns_turn_lifecycle_through_ports():
    calls = []
    completions = []

    assert _runtime(calls, completions).execute("hello") is True

    assert calls == [
        ("invalidate", None),
        ("submit", ("hello", None), None),
    ]

    completions[0](TurnCompletion.SUCCEEDED)
    completions[0](TurnCompletion.SUCCEEDED)
    assert calls == [
        ("invalidate", None),
        ("submit", ("hello", None), None),
        "reset",
        ("invalidate", None),
    ]


def test_pending_input_runtime_does_not_continue_goal_after_failed_or_cancelled_turn():
    calls = []
    completions = []
    continuations = []
    stops = []
    runtime = _runtime(calls, completions, continuations, stops)

    assert runtime.execute("hello") is True
    completions[0](TurnCompletion.FAILED)

    assert continuations == []
    assert stops == [
        "Turn execution failed; resolve the error before resuming the goal."
    ]
    assert "reset" in calls

    calls.clear()
    completions.clear()
    assert runtime.execute("again") is True
    completions[0](TurnCompletion.CANCELLED)

    assert continuations == []
    assert stops == [
        "Turn execution failed; resolve the error before resuming the goal."
    ]


def test_pending_input_runtime_routes_slash_commands_without_starting_turn():
    calls = []

    assert _runtime(calls).execute("/status") is False

    assert calls == [("command", "/status")]
