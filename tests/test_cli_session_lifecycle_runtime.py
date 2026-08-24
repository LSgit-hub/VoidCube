from datetime import datetime

from voidcube.application.sessions import SessionLifecycleState
from voidcube.interfaces.cli.session_lifecycle import (
    CliSessionLifecyclePorts,
    CliSessionLifecycleRuntime,
)


def test_session_lifecycle_runtime_applies_state_before_agent_activation():
    events = []
    started_at = datetime(2026, 8, 3, 12, 0, 0)
    runtime = CliSessionLifecycleRuntime(
        CliSessionLifecyclePorts(
            apply_shared_state=lambda value: events.append(("shared", value)),
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

    assert [event[0] for event in events] == ["shared", "activate"]
    assert events[-1] == ("activate", "target", started_at)
