from __future__ import annotations

import json
from types import SimpleNamespace

from voidcube.infrastructure.persistence.session_runtime import SessionDB
from voidcube.domain.contracts.events import GoalEvent, GoalEventKind
from voidcube.extensions.tools.session_goal_tool import (
    dispatch_session_goal,
    _handle_create_goal,
    _handle_get_goal,
    _handle_update_goal,
)
from voidcube.runtime.agent.runner import AIAgent
from voidcube.runtime.agent.tool_execution import PreparedToolCall
from voidcube.interfaces.cli.session_goal_runtime import (
    BLOCKED,
    create_goal,
    get_goal,
    stop_goal_after_turn,
)


def test_session_goal_tool_reads_and_pauses_the_current_session_goal():
    host = SimpleNamespace(session_id="session-a", _session_goals={})
    create_goal(host, "Finish the task")

    result = json.loads(dispatch_session_goal("get", host=host))
    assert result["success"] is True
    assert result["goal"]["objective"] == "Finish the task"

    paused = json.loads(
        dispatch_session_goal("update", host=host, status="paused", reason="Waiting")
    )
    assert paused["success"] is True
    assert paused["goal"]["status"] == "paused"


def test_session_goal_tool_creates_only_when_no_unfinished_goal_exists():
    host = SimpleNamespace(session_id="session-a", _session_goals={})

    created = json.loads(dispatch_session_goal(
        "create", host=host, objective="  Finish   the task "
    ))
    rejected = json.loads(dispatch_session_goal(
        "create", host=host, objective="Start another task"
    ))

    assert created["success"] is True
    assert created["goal"]["objective"] == "Finish the task"
    assert rejected["success"] is False
    assert rejected["goal"]["objective"] == "Finish the task"


def test_session_goal_tool_replaces_a_completed_goal():
    host = SimpleNamespace(session_id="session-a", _session_goals={})
    create_goal(host, "First task")
    dispatch_session_goal("update", host=host, status="completed")

    result = json.loads(dispatch_session_goal(
        "create", host=host, objective="Second task"
    ))

    assert result["success"] is True
    assert result["goal"]["objective"] == "Second task"
    assert result["goal"]["status"] == "active"


def test_session_goal_tool_rejects_empty_or_oversized_objective():
    host = SimpleNamespace(session_id="session-a", _session_goals={})

    empty = json.loads(dispatch_session_goal("create", host=host, objective=" \n "))
    long = json.loads(dispatch_session_goal("create", host=host, objective="x" * 4001))

    assert empty["success"] is False
    assert long["success"] is False
    assert host._session_goals == {}


def test_session_goal_tool_requires_three_turns_to_mark_blocked():
    host = SimpleNamespace(session_id="session-a", _session_goals={})
    create_goal(host, "Finish the task")

    first = json.loads(dispatch_session_goal(
        "audit_blocker", host=host, reason="Missing access", turn_id="turn-1"
    ))
    rejected = dispatch_session_goal(
        "update", host=host, status="blocked", reason="Missing access"
    )
    second = json.loads(dispatch_session_goal(
        "audit_blocker", host=host, reason="Missing access", turn_id="turn-2"
    ))
    third = json.loads(dispatch_session_goal(
        "audit_blocker", host=host, reason="Missing access", turn_id="turn-3"
    ))

    assert first["goal"]["status"] == second["goal"]["status"] == "active"
    assert json.loads(rejected)["success"] is False
    assert third["goal"]["status"] == "blocked"


def test_individual_update_goal_cannot_bypass_blocker_audit():
    host = SimpleNamespace(session_id="session-a", _session_goals={})
    create_goal(host, "Finish the task")

    rejected = json.loads(
        _handle_update_goal(
            {"status": "blocked", "reason": "Missing access"},
            host=host,
        )
    )

    assert rejected["success"] is False
    assert rejected["error"] == "status must be paused or completed"
    assert get_goal(host)["status"] == "active"


def test_turn_failure_stops_active_goal_until_explicit_resume():
    host = SimpleNamespace(session_id="session-a", _session_goals={})
    create_goal(host, "Finish the task")

    assert stop_goal_after_turn(host, "Model execution failed") is True
    assert get_goal(host)["status"] == BLOCKED
    assert get_goal(host)["reason"] == "Model execution failed"

    assert stop_goal_after_turn(host, "another failure") is False


def test_agent_goal_objective_steering_is_turn_local_and_drained_once():
    agent = object.__new__(AIAgent)
    import threading
    agent._goal_steering_lock = threading.Lock()
    agent._goal_steering_messages = []

    agent.steer_goal_objective("  Revised   objective ")

    [message] = agent._drain_goal_steering()
    assert "Revised objective" in message
    assert "<session_goal_objective>" in message
    assert agent._drain_goal_steering() == []


def test_goal_state_mutation_invalidates_queued_continuation():
    invalidations = []
    host = SimpleNamespace(
        session_id="session-a",
        _session_goals={},
        _discard_pending_goal_continuations=lambda: invalidations.append("discard"),
    )
    create_goal(host, "Finish the task")
    invalidations.clear()

    paused = json.loads(
        dispatch_session_goal("update", host=host, status="paused", reason="User pause")
    )

    assert paused["success"] is True
    assert invalidations == ["discard"]


def test_goal_lifecycle_emits_structured_events_when_host_subscribes():
    events = []
    host = SimpleNamespace(
        session_id="session-a",
        _session_goals={},
        _goal_event_sink=events.append,
    )

    create_goal(host, "Finish the task")
    dispatch_session_goal("update", host=host, status="paused", reason="User pause")
    from voidcube.interfaces.cli.session_goal_runtime import clear_goal, update_goal
    update_goal(host, "active", "Resume")
    update_goal(host, "completed", "Verified")
    clear_goal(host)

    assert [event.kind for event in events] == [
        GoalEventKind.CREATED,
        GoalEventKind.PAUSED,
        GoalEventKind.UPDATED,
        GoalEventKind.COMPLETED,
        GoalEventKind.CLEARED,
    ]
    assert all(isinstance(event, GoalEvent) for event in events)
    assert events[0].goal["objective"] == "Finish the task"
    assert events[1].reason == "User pause"


def test_individual_goal_tools_share_the_session_backend():
    host = SimpleNamespace(session_id="session-a", _session_goals={})

    created = json.loads(_handle_create_goal(
        {"objective": "Finish the task"}, host=host
    ))
    fetched = json.loads(_handle_get_goal({}, host=host))
    completed = json.loads(_handle_update_goal(
        {"status": "complete"}, host=host
    ))

    assert created["success"] is True
    assert fetched["goal"]["objective"] == "Finish the task"
    assert completed["success"] is True
    assert completed["goal"]["status"] == "completed"


def test_aggregate_goal_tool_accepts_complete_status_alias():
    host = SimpleNamespace(session_id="session-a", _session_goals={})
    create_goal(host, "Finish the task")

    result = json.loads(
        dispatch_session_goal("update", host=host, status="complete")
    )

    assert result["success"] is True
    assert result["goal"]["status"] == "completed"


def test_session_goal_completion_uses_injected_port_backend():
    calls = []

    class Port:
        def get_goal(self, host):
            from voidcube.application.session_goal import get_goal
            return get_goal(host)

        def create_goal(self, host, objective):
            from voidcube.application.session_goal import create_goal
            return create_goal(host, objective)

        def update_goal(self, host, status, reason=None):
            from voidcube.application.session_goal import update_goal
            return update_goal(host, status, reason)

        def clear_goal(self, host):
            return False

        def audit_blocked_goal(self, host, reason, *, turn_id):
            return None

        def update_goal_objective(self, host, objective, *, reason=None):
            return False

        def complete_backend(self, host, reason):
            calls.append((host.session_id, reason))
            return True

    host = SimpleNamespace(
        session_id="session-a",
        _session_goals={},
        _session_goal_port=Port(),
    )
    create_goal(host, "Finish the task")

    result = json.loads(dispatch_session_goal("update", host=host, status="complete"))

    assert result["success"] is True
    assert calls == [("session-a", None)]


def test_agent_routes_individual_goal_tool_names_to_session_backend(tmp_path):
    db = SessionDB(tmp_path / "sessions.db")
    agent = object.__new__(AIAgent)
    agent.session_id = "session-a"
    agent._session_db = db
    agent._goal_audit_turn_id = "turn-1"
    emitted = []
    agent._event_port = type(
        "EventPort",
        (),
        {"emit": lambda self, event: emitted.append(event)},
    )()

    def call(name, arguments):
        return json.loads(
            agent._route_tool_call(
                PreparedToolCall(None, 0, f"call-{name}", name, arguments),
                messages=[],
                effective_task_id="session-a",
            )
        )

    created = call("create_goal", {"objective": "Finish the task"})
    fetched = call("get_goal", {})
    completed = call("update_goal", {"status": "complete"})

    assert created["goal"]["objective"] == "Finish the task"
    assert fetched["goal"]["status"] == "active"
    assert completed["goal"]["status"] == "completed"
    assert [event.kind.value for event in emitted] == [
        "goal.created",
        "goal.completed",
    ]
    db.close()


def test_default_session_goal_port_finishes_persistent_audit_turn(tmp_path):
    from voidcube.application.ports import DefaultSessionGoalPort

    db = SessionDB(tmp_path / "sessions.db")
    db.create_session_goal("session-a", "Finish the task")
    db.audit_session_goal_blocker("session-a", "Missing access", turn_id="turn-1")
    host = SimpleNamespace(session_id="session-a", _session_db=db)

    DefaultSessionGoalPort().finish_goal_audit_turn(host, "turn-2")

    goal = db.get_session_goal("session-a")
    assert goal["blocked_reason"] is None
    assert goal["blocked_streak"] == 0
    db.close()
