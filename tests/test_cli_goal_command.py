from __future__ import annotations

from types import SimpleNamespace

import pytest

from voidcube.interfaces.cli.commands.handlers.goal import GoalCommandPorts, handle_goal_command
from voidcube.interfaces.cli.commands.router import parse_cli_command
from voidcube.interfaces.cli.session_goal_runtime import (
    ACTIVE,
    BLOCKED,
    COMPLETED,
    backend_status,
    bind_goal_backend,
    clear_goal,
    create_goal,
    get_goal,
    goal_prompt,
    goal_update_error,
    update_goal,
)
from plugins.goal_manager.db.connection import GoalStore
from voidcube.interfaces.cli.application import VoidcubeCLI


pytestmark = pytest.mark.unit


def _host() -> SimpleNamespace:
    return SimpleNamespace(session_id="goal-session", _session_goals={})


def test_goal_runtime_is_session_isolated_and_prompt_is_active_only():
    host = _host()
    other = SimpleNamespace(session_id="other-session", _session_goals={})

    create_goal(host, "Finish the TUI quality pass")
    assert get_goal(host)["status"] == ACTIVE
    assert get_goal(other) is None
    assert "Finish the TUI quality pass" in goal_prompt(get_goal(host))

    update_goal(host, COMPLETED, "verified")
    assert get_goal(host)["status"] == COMPLETED
    assert goal_prompt(get_goal(host)) == ""
    assert clear_goal(host) is True
    assert get_goal(host) is None


def test_active_goal_is_included_in_agent_system_prompt():
    host = _host()
    host.system_prompt = "Base instructions"
    create_goal(host, "Keep the command surface canonical")

    prompt = VoidcubeCLI._effective_system_prompt(host)

    assert prompt.startswith("Base instructions")
    assert "Active Session Goal" in prompt
    assert "Keep the command surface canonical" in prompt


def test_active_goal_prompt_teaches_goal_manager_workflow():
    host = _host()
    create_goal(host, "Plan and verify a multi-step change")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
    })

    prompt = goal_prompt(get_goal(host))

    assert "read the project/root context and next_actions" in prompt
    assert "decompose the root" in prompt
    assert "Re-read the latest node version" in prompt
    assert "Attach real test, CI, Git, file" in prompt
    assert "Never claim completion" in prompt


def test_blocked_goal_is_mirrored_to_goal_manager_root(monkeypatch):
    host = _host()
    create_goal(host, "Wait for the dependency")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "available",
    })
    updates: list[tuple[str, int, str, str, str | None]] = []

    class FakeClient:
        def project(self, project_id):
            assert project_id == "proj-1"
            return {"root": {"id": "root-1", "version": 3, "status": "in_progress"}}

        def update_node_status(self, node_id, expected_version, status, reason, *, session_id=None):
            updates.append((node_id, expected_version, status, reason, session_id))
            return {"node": {"id": node_id, "status": status, "version": 4}}

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", FakeClient)
    assert update_goal(host, BLOCKED, "Waiting for credentials") is True
    assert updates == [("root-1", 3, BLOCKED, "Waiting for credentials", "goal-session")]
    assert get_goal(host)["backend_status"] == "available"


def test_complete_goal_calls_goal_manager_before_local_transition(monkeypatch):
    host = _host()
    create_goal(host, "Complete through backend")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "available",
    })
    completed: list[tuple[str, str, str | None]] = []

    class CompletingClient:
        def complete_node(self, node_id, reason, *, session_id=None):
            completed.append((node_id, reason, session_id))
            return {"node": {"id": node_id, "status": "completed"}}

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", CompletingClient)
    assert update_goal(host, COMPLETED, "verified") is True
    assert completed == [("root-1", "verified", "goal-session")]
    assert get_goal(host)["status"] == COMPLETED


def test_complete_goal_stays_active_when_goal_manager_rejects(monkeypatch):
    host = _host()
    create_goal(host, "Do not complete early")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "available",
    })

    class RejectingClient:
        def complete_node(self, node_id, reason, *, session_id=None):
            raise RuntimeError("completion blocked")

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", RejectingClient)
    assert update_goal(host, COMPLETED, "premature") is False
    assert get_goal(host)["status"] == ACTIVE
    assert get_goal(host)["backend_status"] == "unavailable"


def test_completion_validation_failure_keeps_backend_available(monkeypatch):
    host = _host()
    create_goal(host, "Report completion blockers")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "available",
    })

    from plugins.goal_manager.tools.client import GoalServiceError

    class ValidationClient:
        def complete_node(self, node_id, reason, *, session_id=None):
            raise GoalServiceError(409, {"detail": "goal completion blocked", "blockers": []})

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", ValidationClient)
    assert update_goal(host, COMPLETED, "premature") is False
    assert get_goal(host)["status"] == ACTIVE
    assert get_goal(host)["backend_status"] == "available"
    assert goal_update_error(host) == "Goal Manager 未通过完成校验"


def test_goal_handler_displays_completion_blockers():
    output: list[str] = []
    ports = GoalCommandPorts(
        get_goal=lambda: {"objective": "goal", "status": ACTIVE},
        create_goal=lambda objective: {},
        update_goal=lambda status, reason: False,
        clear_goal=lambda: False,
        start_goal=None,
        reset_agent=lambda: None,
        emit=output.append,
        translate=lambda key, **kwargs: f"{key}:{kwargs.get('reason', '')}",
        get_update_error=lambda: "子目标未完成：实现测试",
    )
    handle_goal_command(parse_cli_command("/goal complete"), ports=ports)
    assert output == ["goal_command.complete_blocked_reason:子目标未完成：实现测试"]


def test_blocked_goal_stays_local_when_goal_manager_sync_fails(monkeypatch):
    host = _host()
    create_goal(host, "Keep local state")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "available",
    })

    class FailingClient:
        def project(self, project_id):
            raise RuntimeError("service unavailable")

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", FailingClient)
    assert update_goal(host, BLOCKED, "No network") is True
    assert get_goal(host)["status"] == BLOCKED
    assert get_goal(host)["backend_status"] == "unavailable"


def test_status_reconciles_blocked_goal_after_backend_recovery(monkeypatch):
    host = _host()
    create_goal(host, "Recover the goal service")
    host._session_goals[host.session_id].update({
        "status": BLOCKED, "reason": "Service was unavailable",
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "unavailable",
    })
    updates: list[tuple[str, int, str, str]] = []

    class RecoveringClient:
        def project(self, project_id):
            return {"root": {"id": "root-1", "version": 5, "status": "in_progress"}}

        def update_node_status(self, node_id, expected_version, status, reason, *, session_id=None):
            updates.append((node_id, expected_version, status, reason))
            return {"node": {"id": node_id, "version": expected_version + 1, "status": status}}

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", RecoveringClient)
    result = backend_status(host, get_goal(host))

    assert result["backend_status"] == "available"
    assert result["backend_project"]["root"]["status"] == BLOCKED
    assert updates == [("root-1", 5, BLOCKED, "Service was unavailable")]
    assert get_goal(host)["backend_status"] == "available"


def test_status_reconciles_resumed_goal_after_backend_recovery(monkeypatch):
    host = _host()
    create_goal(host, "Resume after outage")
    host._session_goals[host.session_id].update({
        "status": ACTIVE, "reason": "Dependency restored",
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "unavailable",
    })
    updates: list[tuple[str, int, str, str]] = []

    class RecoveringClient:
        def project(self, project_id):
            return {"root": {"id": "root-1", "version": 7, "status": "blocked"}}

        def update_node_status(self, node_id, expected_version, status, reason, *, session_id=None):
            updates.append((node_id, expected_version, status, reason))
            return {"node": {"id": node_id, "version": expected_version + 1, "status": status}}

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", RecoveringClient)
    result = backend_status(host, get_goal(host))

    assert result["backend_project"]["root"]["status"] == "in_progress"
    assert updates == [("root-1", 7, "in_progress", "Dependency restored")]
    assert get_goal(host)["backend_status"] == "available"


def test_goal_handler_creates_and_queues_objective():
    state: dict[str, object] = {"goal": None}
    output: list[str] = []
    queued: list[str] = []
    reset_count = 0

    def reset_agent() -> None:
        nonlocal reset_count
        reset_count += 1

    def translate(key: str, **kwargs: str) -> str:
        return key + (f":{kwargs}" if kwargs else "")

    ports = GoalCommandPorts(
        get_goal=lambda: state["goal"],
        create_goal=lambda objective: state.update(
            goal={"objective": objective, "status": ACTIVE}
        ) or state["goal"],
        update_goal=lambda _status, _reason: False,
        clear_goal=lambda: False,
        start_goal=queued.append,
        reset_agent=reset_agent,
        emit=output.append,
        translate=translate,
    )

    handle_goal_command(
        parse_cli_command("/goal Build a reliable terminal harness"),
        ports=ports,
    )

    assert queued == ["Build a reliable terminal harness"]
    assert reset_count == 1
    assert output[0].startswith("goal_command.created")


def test_goal_handler_requires_terminal_transition_before_clear():
    state: dict[str, object] = {
        "goal": {"objective": "active", "status": ACTIVE},
    }
    output: list[str] = []
    ports = GoalCommandPorts(
        get_goal=lambda: state["goal"],
        create_goal=lambda objective: state["goal"],
        update_goal=lambda status, reason: state["goal"].update(
            status=status, reason=reason
        ) or True,
        clear_goal=lambda: state.update(goal=None) or True,
        start_goal=None,
        reset_agent=lambda: None,
        emit=output.append,
        translate=lambda key, **kwargs: key,
    )

    handle_goal_command(parse_cli_command("/goal clear"), ports=ports)
    assert output == ["goal_command.clear_active"]

    handle_goal_command(
        parse_cli_command("/goal blocked waiting for credentials"),
        ports=ports,
    )
    assert state["goal"]["status"] == BLOCKED
    handle_goal_command(parse_cli_command("/goal clear"), ports=ports)
    assert state["goal"] is None


def test_goal_command_full_create_block_resume_complete_flow(monkeypatch, tmp_path):
    store = GoalStore(tmp_path / "goal-flow.db")
    host = _host()
    output: list[str] = []
    queued: list[str] = []

    class StoreClient:
        def health(self):
            return True

        def create_session_project(self, objective, session_id):
            return store.create_project(
                objective, description=objective, reason="bind session goal",
                session_id=session_id, idempotency_key=f"session:{session_id}",
                root_status="in_progress",
            )

        def project(self, project_id):
            return store.get_project(project_id)

        def update_node_status(self, node_id, expected_version, status, reason, *, session_id=None):
            return store.update_node(
                node_id, expected_version, {"status": status}, reason=reason,
                session_id=session_id,
            )

        def complete_node(self, node_id, reason, *, session_id=None):
            return store.complete_node(node_id, reason=reason, session_id=session_id)

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", StoreClient)
    ports = GoalCommandPorts(
        get_goal=lambda: get_goal(host),
        create_goal=lambda objective: create_goal(host, objective),
        update_goal=lambda status, reason: update_goal(host, status, reason),
        clear_goal=lambda: clear_goal(host),
        start_goal=queued.append,
        bind_backend=lambda objective: bind_goal_backend(host, objective),
        get_backend_status=lambda goal: backend_status(host, goal),
        get_update_error=lambda: goal_update_error(host),
        reset_agent=lambda: None,
        emit=output.append,
        translate=lambda key, **kwargs: key,
    )
    try:
        handle_goal_command(parse_cli_command("/goal Run the full flow"), ports=ports)
        binding = get_goal(host)
        assert binding["backend"] == "goal_manager"
        assert store.get_node(binding["root_node_id"])["status"] == "in_progress"

        handle_goal_command(parse_cli_command("/goal blocked waiting for input"), ports=ports)
        assert get_goal(host)["status"] == BLOCKED
        assert store.get_node(binding["root_node_id"])["status"] == "blocked"

        handle_goal_command(parse_cli_command("/goal resume input restored"), ports=ports)
        assert get_goal(host)["status"] == ACTIVE
        assert store.get_node(binding["root_node_id"])["status"] == "in_progress"

        handle_goal_command(parse_cli_command("/goal complete verified"), ports=ports)
        assert get_goal(host)["status"] == COMPLETED
        assert store.get_node(binding["root_node_id"])["status"] == "completed"
        assert queued == ["Run the full flow", "Run the full flow"]
    finally:
        store.close()
