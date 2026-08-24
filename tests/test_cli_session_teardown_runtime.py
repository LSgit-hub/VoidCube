from types import SimpleNamespace

from voidcube.interfaces.cli.session_teardown_runtime import (
    CliSessionTeardownPorts,
    CliSessionTeardownRuntime,
)


def _runtime(events, **overrides):
    values = {
        "repository": lambda: SimpleNamespace(),
        "session_id": lambda: "session",
        "agent_available": lambda: True,
        "agent_running": lambda: True,
        "model": lambda: "model",
        "platform": lambda: "cli",
        "end_session": lambda repository, session_id, reason: events.append(
            ("close", repository, session_id, reason)
        ),
        "invoke_session_end": lambda **kwargs: events.append(("hook", kwargs)),
        "log_debug": lambda message, error: events.append(("log", message, str(error))),
    }
    values.update(overrides)
    return CliSessionTeardownRuntime(CliSessionTeardownPorts(**values))


def test_session_teardown_closes_repository_and_finishes_interrupted_hook():
    events = []
    runtime = _runtime(events)

    runtime.close_session()
    runtime.finish_interrupted_session()

    assert events[0][0] == "close"
    assert events[0][2:] == ("session", "cli_close")
    assert events[1] == (
        "hook",
        {
            "session_id": "session",
            "completed": False,
            "interrupted": True,
            "model": "model",
            "platform": "cli",
        },
    )


def test_session_teardown_swallows_close_and_hook_failures():
    events = []

    def fail_close(*_args):
        raise RuntimeError("close failed")

    def fail_hook(**_kwargs):
        raise RuntimeError("hook failed")

    runtime = _runtime(
        events,
        end_session=fail_close,
        invoke_session_end=fail_hook,
    )

    runtime.close_session()
    runtime.finish_interrupted_session()

    assert events == [("log", "Could not close session in DB: %s", "close failed")]
