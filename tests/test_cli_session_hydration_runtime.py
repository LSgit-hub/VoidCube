from VoidCube_app.session_lifecycle import SessionHydration, SessionHydrationStatus
from VoidCube_cli.cli_session_hydration_runtime import (
    CliSessionHydrationPorts,
    CliSessionHydrationRuntime,
)


def test_session_hydration_runtime_loads_once_and_projects_ready_history():
    calls = []
    state = {"hydration": None, "history": []}
    hydration = SessionHydration(
        session_id="session",
        status=SessionHydrationStatus.READY,
        conversation_history=({"role": "user", "content": "hello"},),
    )
    runtime = CliSessionHydrationRuntime(
        CliSessionHydrationPorts(
            cached_hydration=lambda: state["hydration"],
            set_hydration=lambda value: state.__setitem__("hydration", value),
            repository=lambda: "db",
            session_id=lambda: "session",
            set_conversation_history=lambda value: state.__setitem__("history", value),
            hydrate=lambda **kwargs: calls.append(kwargs) or hydration,
        )
    )

    first, first_loaded = runtime.load()
    second, second_loaded = runtime.load()

    assert first is hydration
    assert second is hydration
    assert first_loaded is True
    assert second_loaded is False
    assert calls == [{"repository": "db", "session_id": "session"}]
    assert state["history"] == [{"role": "user", "content": "hello"}]


def test_session_hydration_runtime_preserves_empty_result_without_history_projection():
    projected = []
    hydration = SessionHydration(
        session_id="session",
        status=SessionHydrationStatus.EMPTY,
    )
    runtime = CliSessionHydrationRuntime(
        CliSessionHydrationPorts(
            cached_hydration=lambda: None,
            set_hydration=lambda _value: None,
            repository=lambda: "db",
            session_id=lambda: "session",
            set_conversation_history=projected.append,
            hydrate=lambda **_: hydration,
        )
    )

    result, loaded_now = runtime.load()

    assert result is hydration
    assert loaded_now is True
    assert projected == []
