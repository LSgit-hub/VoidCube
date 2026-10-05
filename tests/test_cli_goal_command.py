from __future__ import annotations

from types import SimpleNamespace
from queue import Queue

import pytest

from voidcube.interfaces.cli.commands.handlers.goal import GoalCommandPorts, handle_goal_command
from voidcube.domain.goals import classify_goal_backend
from voidcube.interfaces.cli.commands.router import parse_cli_command
from voidcube.interfaces.cli.commands.registry import _goal_command_ports
from voidcube.infrastructure.persistence.session_runtime import SessionDB
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
    update_goal_objective,
)
from plugins.goal_manager.db.connection import GoalStore
from voidcube.interfaces.cli.application import VoidcubeCLI


pytestmark = pytest.mark.unit


def _host() -> SimpleNamespace:
    return SimpleNamespace(session_id="goal-session", _session_goals={})


def _inject_goal_manager(host, client) -> None:
    host._goal_manager_port = client
    host._goal_manager_factory = lambda: client


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


def test_goal_objective_edit_preserves_status_and_invalidates_continuation():
    invalidations = []
    host = _host()
    host._discard_pending_goal_continuations = lambda: invalidations.append(True)
    create_goal(host, "Initial objective")
    update_goal(host, BLOCKED, "waiting")
    invalidations.clear()

    assert update_goal_objective(host, "Revised objective") is True
    assert get_goal(host)["objective"] == "Revised objective"
    assert get_goal(host)["status"] == BLOCKED
    assert invalidations == [True]


def test_active_goal_is_included_in_agent_system_prompt():
    host = _host()
    host.system_prompt = "Base instructions"
    create_goal(host, "Keep the command surface canonical")

    prompt = VoidcubeCLI._effective_system_prompt(host)

    assert prompt.startswith("Base instructions")
    assert "Active Session Goal" in prompt
    assert "Keep the command surface canonical" in prompt


def test_goal_continuation_only_queues_when_session_is_idle_and_active():
    host = _host()
    host._pending_input = Queue()
    create_goal(host, "Finish the lifecycle")

    VoidcubeCLI._continue_session_goal(host)
    continuation = host._pending_input.get_nowait()
    assert "Continue working toward the active session goal" in continuation

    host._pending_input.put("user input")
    VoidcubeCLI._continue_session_goal(host)
    assert host._pending_input.get_nowait() == "user input"

    update_goal(host, COMPLETED, "verified")
    VoidcubeCLI._continue_session_goal(host)
    assert host._pending_input.empty()


def test_goal_continuation_skips_when_session_goal_tool_is_unavailable():
    host = _host()
    host._pending_input = Queue()
    host.enabled_toolsets = ["web"]
    create_goal(host, "Finish the lifecycle")

    VoidcubeCLI._continue_session_goal(host)

    assert host._pending_input.empty()


def test_goal_status_change_discards_stale_continuations_but_keeps_user_input():
    host = _host()
    host._pending_input = Queue()
    host._pending_input.put("Continue working toward this active session goal: stale")
    host._pending_input.put("user input")
    host._pending_input.put(
        "Continue working toward the active session goal. stale continuation"
    )

    VoidcubeCLI._discard_pending_goal_continuations(host)

    assert host._pending_input.get_nowait() == "user input"
    assert host._pending_input.empty()


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


def test_effective_goal_prompt_resolves_linked_memory_only_for_goal_manager(monkeypatch):
    host = _host()
    host.system_prompt = "Base instructions"
    create_goal(host, "Use a prior decision")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
    })

    class FakeClient:
        def context(self, node_id):
            assert node_id == "root-1"
            return {"memory_refs": [{"memory_id": "m-1", "confidence": 1, "relation_type": "decision"}]}

    class FakeMemory:
        def resolve_goal_memory_refs(self, refs, *, session_id):
            assert refs[0]["memory_id"] == "m-1"
            assert session_id == "goal-session"
            return "<goal-memory-context>linked</goal-memory-context>"

    host._memory_provider = FakeMemory()
    _inject_goal_manager(host, FakeClient())

    prompt = VoidcubeCLI._effective_system_prompt(host)
    assert "<goal-memory-context>linked</goal-memory-context>" in prompt


def test_effective_goal_prompt_does_not_resolve_memory_for_session_goal(monkeypatch):
    host = _host()
    host.system_prompt = "Base instructions"
    create_goal(host, "Simple session task")

    def forbidden_client():
        raise AssertionError("session goals must not resolve linked memory")

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", forbidden_client)
    host._memory_provider = SimpleNamespace(
        resolve_goal_memory_refs=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("session goals must not resolve linked memory")
        )
    )
    prompt = VoidcubeCLI._effective_system_prompt(host)
    assert "goal-memory-context" not in prompt


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

    _inject_goal_manager(host, FakeClient())
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

    _inject_goal_manager(host, CompletingClient())
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

    _inject_goal_manager(host, RejectingClient())
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

    _inject_goal_manager(host, ValidationClient())
    assert update_goal(host, COMPLETED, "premature") is False
    assert get_goal(host)["status"] == ACTIVE
    assert get_goal(host)["backend_status"] == "available"
    assert goal_update_error(host) == "Goal Manager 未通过完成校验"


def test_completion_accepts_remote_already_completed_conflict(monkeypatch):
    host = _host()
    create_goal(host, "Complete idempotently")
    host._session_goals[host.session_id].update({
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "available",
    })

    from plugins.goal_manager.tools.client import GoalServiceError

    class AlreadyCompletedClient:
        def complete_node(self, node_id, reason, *, session_id=None):
            raise GoalServiceError(409, {
                "detail": "node version conflict",
                "latest": {"id": node_id, "status": "completed", "version": 4},
            })

    _inject_goal_manager(host, AlreadyCompletedClient())
    assert update_goal(host, COMPLETED, "verified elsewhere") is True
    assert get_goal(host)["backend_status"] == "available"


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


def test_goal_handler_status_surfaces_unavailable_goal_manager_backend():
    output: list[str] = []
    ports = GoalCommandPorts(
        get_goal=lambda: {
            "objective": "goal",
            "status": ACTIVE,
            "backend": "goal_manager",
            "backend_status": "unavailable",
        },
        create_goal=lambda objective: {},
        update_goal=lambda status, reason: False,
        clear_goal=lambda: False,
        start_goal=None,
        reset_agent=lambda: None,
        emit=output.append,
        translate=lambda key, **kwargs: key,
    )

    handle_goal_command(parse_cli_command("/goal status"), ports=ports)

    assert output == [
        "goal_command.header\n"
        "goal_command.status_active\n"
        "goal_command.objective\n"
        "goal_command.backend_unavailable"
    ]


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

    _inject_goal_manager(host, FailingClient())
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

    _inject_goal_manager(host, RecoveringClient())
    result = backend_status(host, get_goal(host))

    assert result["backend_status"] == "available"
    assert result["backend_project"]["root"]["status"] == BLOCKED
    assert updates == [("root-1", 5, BLOCKED, "Service was unavailable")]
    assert get_goal(host)["backend_status"] == "available"


def test_status_reports_unavailable_when_backend_status_persistence_is_stale():
    host = _host()

    class StaleRepository:
        def get_session_goal(self, session_id):
            return {
                "session_id": session_id,
                "objective": "Recover the goal service",
                "status": BLOCKED,
                "reason": "Service was unavailable",
                "backend": "goal_manager",
                "project_id": "proj-1",
                "root_node_id": "root-1",
                "backend_status": "unavailable",
                "revision": 4,
            }

        def bind_session_goal_backend(self, **_kwargs):
            return False

    class RecoveringClient:
        def project(self, project_id):
            return {"root": {"id": "root-1", "version": 5, "status": BLOCKED}}

    host._session_db = StaleRepository()
    _inject_goal_manager(host, RecoveringClient())

    result = backend_status(host, get_goal(host))

    assert result["backend_status"] == "unavailable"


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

    _inject_goal_manager(host, RecoveringClient())
    result = backend_status(host, get_goal(host))

    assert result["backend_project"]["root"]["status"] == "in_progress"
    assert updates == [("root-1", 7, "in_progress", "Dependency restored")]
    assert get_goal(host)["backend_status"] == "available"


def test_status_reconciliation_retries_once_after_remote_version_conflict(monkeypatch):
    host = _host()
    create_goal(host, "Recover after concurrent edit")
    host._session_goals[host.session_id].update({
        "status": ACTIVE, "reason": "retry",
        "backend": "goal_manager", "project_id": "proj-1", "root_node_id": "root-1",
        "backend_status": "unavailable",
    })
    updates: list[int] = []

    class ConflictError(RuntimeError):
        status_code = 409

    class ConflictingClient:
        def project(self, project_id):
            return {"root": {"id": "root-1", "version": 8, "status": "blocked"}}

        def update_node_status(self, node_id, expected_version, status, reason, *, session_id=None):
            updates.append(expected_version)
            if len(updates) == 1:
                raise ConflictError("stale version")
            return {"node": {"id": node_id, "version": expected_version + 1, "status": status}}

    _inject_goal_manager(host, ConflictingClient())
    result = backend_status(host, get_goal(host))

    assert result["backend_status"] == "available"
    assert updates == [8, 8]
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


def test_goal_routing_keeps_simple_requests_in_session_goals():
    decision = classify_goal_backend("Look up the current Python version")
    assert decision.backend == "session"
    assert decision.reasons == ()


def test_goal_routing_promotes_multistep_verified_change():
    decision = classify_goal_backend("修复 API 鉴权问题，然后补充回归测试并验证 CI")
    assert decision.backend == "goal_manager"
    assert {"multi_step", "verification", "change_or_risk"} <= set(decision.reasons)


def test_explicit_goal_manager_route_always_wins():
    decision = classify_goal_backend("简单目标", explicit=True)
    assert decision.backend == "goal_manager"
    assert decision.explicit is True


@pytest.mark.parametrize(
    ("objective", "expected_backend"),
    [
        ("查看当前 Python 版本", "session"),
        ("解释一下这个函数", "session"),
        ("把 README 的一句话改掉", "session"),
        ("分析日志并给出建议", "session"),
        ("检查这个文件是否有拼写错误", "session"),
        ("修复 API 鉴权问题，然后补充回归测试并验证 CI", "goal_manager"),
        ("重构数据库迁移，运行测试并发布版本", "goal_manager"),
        ("部署服务、检查健康状态、记录结果", "goal_manager"),
        ("实现功能 A；补充功能 B；更新文档", "goal_manager"),
    ],
)
def test_goal_routing_realistic_sample_set(objective, expected_backend):
    assert classify_goal_backend(objective).backend == expected_backend


def test_complex_goal_is_bound_to_goal_manager_automatically(monkeypatch):
    host = _host()
    output: list[str] = []
    calls: list[str] = []

    class Client:
        def health(self):
            return True

        def create_session_project(self, objective, session_id):
            calls.append(f"{session_id}:{objective}")
            return {"project": {"id": "proj-auto"}, "root": {"id": "root-auto"}}

    _inject_goal_manager(host, Client())
    ports = _wired_goal_ports(host, output)
    handle_goal_command(
        parse_cli_command("/goal 修复 API 鉴权问题，然后补充回归测试并验证 CI"),
        ports=ports,
    )

    goal = get_goal(host)
    assert goal["backend"] == "goal_manager"
    assert goal["project_id"] == "proj-auto"
    assert calls == ["goal-session:修复 API 鉴权问题，然后补充回归测试并验证 CI"]
    assert "goal_command.backend_auto_enabled" in output


def test_goal_handler_edits_unfinished_goal_and_requeues_new_objective():
    state: dict[str, object] = {
        "goal": {"objective": "old", "status": ACTIVE},
    }
    output: list[str] = []
    queued: list[str] = []
    ports = GoalCommandPorts(
        get_goal=lambda: state["goal"],
        create_goal=lambda objective: state["goal"],
        update_objective=lambda objective: state["goal"].update(
            objective=objective
        ) or True,
        update_goal=lambda _status, _reason: False,
        clear_goal=lambda: False,
        start_goal=queued.append,
        reset_agent=lambda: None,
        emit=output.append,
        translate=lambda key, **kwargs: key,
    )

    handle_goal_command(parse_cli_command("/goal edit revised objective"), ports=ports)

    assert state["goal"]["objective"] == "revised objective"
    assert queued == ["revised objective"]
    assert output == ["goal_command.edited"]


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


@pytest.mark.parametrize("flag", ["glq", "--glq"])
def test_goal_command_full_create_block_resume_complete_flow(monkeypatch, tmp_path, flag):
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

    _inject_goal_manager(host, StoreClient())
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
        handle_goal_command(parse_cli_command(f"/goal {flag} Run the full flow"), ports=ports)
        binding = get_goal(host)
        assert binding["backend"] == "goal_manager"
        assert binding["objective"] == "Run the full flow"
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


def _wired_goal_ports(host, output):
    host._pending_input = Queue()
    return _goal_command_ports(
        host, emit=output.append, translate=lambda key, **kwargs: key,
    )


@pytest.mark.parametrize("persistent", [False, True])
def test_default_goal_lifecycle_never_contacts_manager(monkeypatch, tmp_path, persistent):
    host = _host()
    output = []
    calls = []

    def forbidden_client():
        calls.append("client")
        raise AssertionError("Session goals must not contact Goal Manager")

    monkeypatch.setattr("plugins.goal_manager.tools.client.GoalClient", forbidden_client)
    if persistent:
        host._session_db = SessionDB(tmp_path / "sessions.db")
    try:
        ports = _wired_goal_ports(host, output)
        handle_goal_command(parse_cli_command("/goal Build glq documentation"), ports=ports)
        assert get_goal(host)["backend"] == "session"
        assert host._pending_input.get_nowait() == (
            "Continue working toward this active session goal: Build glq documentation"
        )
        assert "Goal Manager operating protocol" not in goal_prompt(get_goal(host))
        assert "Pursue this goal directly in the session" in goal_prompt(get_goal(host))
        prompt = goal_prompt(get_goal(host))
        assert "user-provided task data" in prompt
        assert "do not redefine success around an easier subset" in prompt
        assert "identify authoritative evidence for each one" in prompt
        assert "status='paused') only after an explicit user request" in prompt
        if persistent:
            host._session_db.close()
            host._session_db = SessionDB(tmp_path / "sessions.db")
            assert get_goal(host)["backend"] == "session"
        for command, expected in [
            ("status", ACTIVE),
            ("blocked waiting for input", BLOCKED),
            ("resume input restored", ACTIVE),
            ("complete verified", COMPLETED),
        ]:
            handle_goal_command(parse_cli_command(f"/goal {command}"), ports=ports)
            assert get_goal(host)["status"] == expected
        handle_goal_command(parse_cli_command("/goal clear"), ports=ports)
        assert get_goal(host) is None
        assert calls == []
    finally:
        if persistent:
            host._session_db.close()


@pytest.mark.parametrize("flag", ["glq", "--glq", "GLQ", "--GLQ"])
def test_glq_requires_objective_without_changing_existing_goal(flag):
    host = _host()
    create_goal(host, "Existing objective")
    before = get_goal(host)
    output = []
    ports = _wired_goal_ports(host, output)
    handle_goal_command(parse_cli_command(f"/goal {flag}\t "), ports=ports)
    assert get_goal(host) == before
    assert host._pending_input.empty()
    assert output == ["goal_command.glq_usage"]


@pytest.mark.parametrize("failure", ["health", "create"])
def test_glq_outage_reports_fallback_and_keeps_local_goal_usable(monkeypatch, failure):
    host = _host()
    output = []

    class UnavailableClient:
        def health(self):
            return failure != "health"

        def create_session_project(self, objective, session_id):
            raise RuntimeError("service unavailable")

    _inject_goal_manager(host, UnavailableClient())
    ports = _wired_goal_ports(host, output)
    handle_goal_command(parse_cli_command("/goal --glq\tShip the change"), ports=ports)
    assert get_goal(host)["backend"] == "session"
    assert get_goal(host)["objective"] == "Ship the change"
    assert host._pending_input.get_nowait() == (
        "Continue working toward this active session goal: Ship the change"
    )
    assert output == ["goal_command.backend_unavailable", "goal_command.created"]
    handle_goal_command(parse_cli_command("/goal complete verified"), ports=ports)
    assert get_goal(host)["status"] == COMPLETED


def test_glq_binding_persists_but_does_not_enable_manager_for_next_goal(monkeypatch, tmp_path):
    host = _host()
    host._session_db = SessionDB(tmp_path / "sessions.db")
    output = []
    calls = []

    class Client:
        def health(self):
            return True

        def create_session_project(self, objective, session_id):
            calls.append(objective)
            return {"project": {"id": "proj-1"}, "root": {"id": "root-1"}}

        def complete_node(self, node_id, reason, *, session_id=None):
            calls.append(node_id)

    _inject_goal_manager(host, Client())
    try:
        ports = _wired_goal_ports(host, output)
        handle_goal_command(parse_cli_command("/goal glq Ship with manager"), ports=ports)
        host._session_db.close()
        host._session_db = SessionDB(tmp_path / "sessions.db")
        assert get_goal(host)["backend"] == "goal_manager"
        assert "Goal Manager operating protocol" in goal_prompt(get_goal(host))
        handle_goal_command(parse_cli_command("/goal complete verified"), ports=ports)
        handle_goal_command(parse_cli_command("/goal Next session goal"), ports=ports)
        assert get_goal(host)["backend"] == "session"
        assert get_goal(host)["project_id"] is None
        assert calls == ["Ship with manager", "root-1"]
    finally:
        host._session_db.close()
