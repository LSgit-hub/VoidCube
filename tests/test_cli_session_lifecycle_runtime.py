from datetime import datetime

from VoidCube_app.session_lifecycle import SessionLifecycleState
from VoidCube_cli.cli_session_lifecycle_runtime import (
    CliSessionLifecyclePorts,
    CliSessionLifecycleRuntime,
)


def test_session_lifecycle_runtime_applies_state_before_agent_activation():
    events = []
    started_at = datetime(2026, 8, 3, 12, 0, 0)
    runtime = CliSessionLifecycleRuntime(
        CliSessionLifecyclePorts(
            set_session_id=lambda value: events.append(("id", value)),
            set_session_start=lambda value: events.append(("start", value)),
            set_conversation_history=lambda value: events.append(("history", value)),
            set_pending_title=lambda value: events.append(("title", value)),
            set_resumed=lambda value: events.append(("resumed", value)),
            clear_hydration=lambda: events.append(("clear",)),
            activate_agent_session=lambda session_id, session_start: events.append(
                ("activate", session_id, session_start)
            ),
        )
    )
    state = SessionLifecycleState(
        session_id="target",
        session_start=started_at,
        conversation_history=({"role": "user", "content": "hello"},),
        resumed=True,
        pending_title="Work",
    )

    runtime.apply(state)

    assert [event[0] for event in events] == [
        "id",
        "start",
        "history",
        "title",
        "resumed",
        "clear",
        "activate",
    ]
    assert events[-1] == ("activate", "target", started_at)
