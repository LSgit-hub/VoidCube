"""M1 contract tests for the Goal Manager service and Agent tools."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from plugins.goal_manager.db.connection import GoalStore
from plugins.goal_manager.domain.graph import GoalConflict
from plugins.goal_manager.server import create_app
from plugins.goal_manager.tools.client import GoalClient, GoalServiceError
from plugins.goal_manager.tools.schemas import SCHEMAS
from voidcube.extensions.plugins import registry as plugin_registry
from voidcube.extensions.tools import model_tools
from voidcube.extensions.tools.registry import registry


@pytest.fixture
def store(tmp_path):
    instance = GoalStore(tmp_path / "goals.db")
    try:
        yield instance
    finally:
        instance.close()


def test_sqlite_wal_and_owner_lease(store):
    with store._connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert store.db_path.with_name("goals.db.owner").exists()


def test_project_creates_root_and_audit_event(store):
    result = store.create_project("VoidCube", reason="建立目标项目")
    assert result["root"]["node_type"] == "project"
    assert result["project"]["root_node_id"] == result["root"]["id"]
    events = store.list_events(result["project"]["id"])
    assert {event["event_type"] for event in events} >= {"create_project", "create_node"}


def test_session_project_can_start_in_progress(store):
    result = store.create_project("Active", reason="session start", root_status="in_progress")
    assert result["root"]["status"] == "in_progress"


def test_project_creation_is_idempotent(store):
    first = store.create_project(
        "Retryable goal", description="same objective", reason="bind", idempotency_key="session:key-1"
    )
    second = store.create_project(
        "Retryable goal", description="same objective", reason="bind retry", idempotency_key="session:key-1"
    )

    assert second["idempotent_reused"] is True
    assert second["project"]["id"] == first["project"]["id"]
    assert second["root"]["id"] == first["root"]["id"]
    assert len(store.list_events(first["project"]["id"])) == 2
    assert len(store.list_projects()) == 1

    with pytest.raises(ValueError, match="different project"):
        store.create_project(
            "Different goal", description="same objective", reason="bad reuse", idempotency_key="session:key-1"
        )


def test_project_idempotency_schema_migrates_existing_database(tmp_path):
    db_path = tmp_path / "legacy-goals.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE goal_projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                root_node_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, deleted_at TEXT
            );
            """
        )
    legacy = GoalStore(db_path)
    try:
        result = legacy.create_project("Migrated", reason="migration", idempotency_key="legacy:key")
        assert result["project"]["idempotency_key"] == "legacy:key"
    finally:
        legacy.close()


def test_session_project_client_uses_stable_idempotency_key(monkeypatch):
    calls = []
    client = GoalClient(base_url="http://goal.test")

    def request(method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        return {"project": {"id": "proj_test"}, "root": {"id": "root_test"}}

    monkeypatch.setattr(client, "request", request)
    client.create_session_project("  Ship   the goal flow ", "session-42")
    client.create_session_project("Ship the goal flow", "session-42")

    assert calls[0][2]["idempotency_key"] == calls[1][2]["idempotency_key"]
    assert calls[0][2]["idempotency_key"].startswith("session:")
    assert calls[0][2]["root_status"] == "in_progress"


def test_generic_project_create_derives_session_idempotency_key(monkeypatch):
    client = GoalClient(base_url="http://goal.test")
    calls = []

    def request(method, path, payload=None, **kwargs):
        calls.append(payload)
        return {"project": {"id": "proj_test"}, "root": {"id": "root_test"}}

    monkeypatch.setattr(client, "request", request)
    client.call_tool("goal_project_create", {
        "name": "Mem analysis", "description": "Analyze Mem", "reason": "plan",
        "session_id": "session-42",
    })
    assert calls[0]["idempotency_key"].startswith("session:")
    assert calls[0]["root_status"] == "in_progress"


def test_protocol_tools_route_to_goal_service_contracts(monkeypatch):
    client = GoalClient(base_url="http://goal.test")
    calls = []

    def request(method, path, payload=None, **kwargs):
        calls.append({"method": method, "path": path, "payload": payload, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(client, "request", request)
    client.call_tool("goal_intent_contract_set", {
        "projectId": "proj_1", "outcome": "ship", "successCriteria": ["green tests"],
        "openQuestions": ["release window?"], "reason": "capture intent", "session_id": "s1",
    })
    client.call_tool("goal_protocol_next_action", {"projectId": "proj_1", "limit": 3})
    client.call_tool("goal_plan_review", {"projectId": "proj_1"})
    client.call_tool("goal_replan", {"projectId": "proj_1", "reason": "revise plan"})
    client.call_tool("goal_lifecycle_get", {"nodeId": "goal_1"})
    client.call_tool("goal_record_execution_result", {
        "nodeId": "goal_1", "status": "succeeded", "summary": "ran", "outputs": {"exit_code": 0},
        "reason": "record execution",
    })
    client.call_tool("goal_record_observation", {
        "nodeId": "goal_1", "executionResultId": "exec_1", "summary": "observed",
        "signals": ["ok"], "reason": "record observation",
    })
    client.call_tool("goal_verify_evidence", {
        "nodeId": "goal_1", "evidenceId": "ev_1", "accepted": True, "summary": "verified",
        "criterionIndex": 0, "reason": "verify",
    })
    client.call_tool("goal_apply_evidence_verification", {
        "nodeId": "goal_1", "verificationId": "ver_1", "expectedVersion": 2,
        "reason": "apply",
    })
    client.call_tool("goal_submit_for_review", {
        "nodeId": "goal_1", "expectedVersion": 3, "reason": "submit",
    })

    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "/api/goals/projects/proj_1/intent-contract"
    assert calls[0]["payload"]["success_criteria"] == ["green tests"]
    assert calls[0]["payload"]["open_questions"] == ["release window?"]
    assert calls[0]["payload"]["session_id"] == "s1"
    assert calls[1]["path"] == "/api/goals/projects/proj_1/protocol-next-action"
    assert calls[1]["query"] == {"limit": 3}
    assert calls[2]["path"] == "/api/goals/projects/proj_1/plan-review"
    assert calls[3]["path"] == "/api/goals/projects/proj_1/replan"
    assert calls[4]["path"] == "/api/goals/nodes/goal_1/lifecycle"
    assert calls[5]["path"] == "/api/goals/nodes/goal_1/execution-results"
    assert calls[5]["payload"]["outputs"] == {"exit_code": 0}
    assert calls[6]["payload"]["execution_result_id"] == "exec_1"
    assert calls[7]["payload"]["criterion_index"] == 0
    assert calls[8]["payload"]["verification_id"] == "ver_1"
    assert calls[8]["payload"]["expected_version"] == 2
    assert calls[9]["path"] == "/api/goals/nodes/goal_1/submit-for-review"


def test_session_project_client_retries_server_failure_with_same_key(monkeypatch):
    payloads = []
    client = GoalClient(base_url="http://goal.test")

    def request(method, path, payload=None, **kwargs):
        payloads.append(dict(payload))
        if len(payloads) == 1:
            raise GoalServiceError(503, {"detail": "response lost"})
        return {"project": {"id": "proj_test"}, "root": {"id": "root_test"}}

    monkeypatch.setattr(client, "request", request)
    result = client.create_session_project("Retry safely", "session-42")

    assert result["project"]["id"] == "proj_test"
    assert len(payloads) == 2
    assert payloads[0]["idempotency_key"] == payloads[1]["idempotency_key"]


def test_cycle_detection_returns_path(store):
    project = store.create_project("P", reason="init")
    project_id = project["project"]["id"]
    a = store.create_node(project_id, {"node_type": "task", "title": "A"}, reason="add A")["node"]
    b = store.create_node(project_id, {"node_type": "task", "title": "B"}, reason="add B")["node"]
    store.create_edge(
        {"source_id": a["id"], "target_id": b["id"], "edge_type": "depends_on"},
        reason="A depends on B",
    )
    with pytest.raises(GoalConflict) as error:
        store.create_edge(
            {"source_id": b["id"], "target_id": a["id"], "edge_type": "depends_on"},
            reason="B depends on A",
        )
    assert error.value.payload["cycle_path"][0] == b["id"]


def test_batch_is_atomic_and_rollback_is_lifo(store):
    project = store.create_project("P", reason="init")
    project_id = project["project"]["id"]
    with pytest.raises((ValueError, KeyError, GoalConflict)):
        store.apply_batch(
            project_id,
            [
                {"op": "create_node", "temp_id": "ok", "node_type": "task", "title": "kept?"},
                {"op": "unsupported"},
            ],
            reason="intentional failure",
        )
    assert store.overview(project_id)["nodes"] == [project["root"]]

    applied = store.apply_batch(
        project_id,
        [{"op": "create_node", "temp_id": "n1", "node_type": "task", "title": "N1"}],
        reason="create batch",
    )
    node_id = applied["temp_ids"]["n1"]
    assert store.get_node(node_id)["title"] == "N1"
    newer = store.create_node(project_id, {"node_type": "task", "title": "N2"}, reason="newer")
    with pytest.raises(GoalConflict):
        store.rollback(applied["batch_id"])
    store.rollback(newer["batch_id"])
    store.rollback(applied["batch_id"])
    with pytest.raises(KeyError):
        store.get_node(node_id)


def test_rollback_can_be_redone_and_new_writes_close_redo_branch(store):
    project = store.create_project("P", reason="init")
    project_id = project["project"]["id"]
    applied = store.apply_batch(
        project_id,
        [{"op": "create_node", "temp_id": "n1", "node_type": "task", "title": "N1"}],
        reason="create batch",
    )
    node_id = applied["temp_ids"]["n1"]
    store.rollback(applied["batch_id"])
    with pytest.raises(KeyError):
        store.get_node(node_id)

    history = store.history(project_id)
    assert history["can_redo"] is True
    assert history["redo_batch_id"] == applied["batch_id"]
    assert history["can_undo"] is True
    assert history["undo_batch_id"] != applied["batch_id"]

    redone = store.redo(project_id=project_id)
    assert redone == {"batch_id": applied["batch_id"], "redone": True}
    assert store.get_node(node_id)["title"] == "N1"
    assert store.history(project_id)["can_redo"] is False

    store.rollback(applied["batch_id"])
    store.create_node(project_id, {"node_type": "task", "title": "new branch"}, reason="new write")
    with pytest.raises(GoalConflict):
        store.redo(project_id=project_id, batch_id=applied["batch_id"])


def test_completion_claims_are_verified_on_all_write_paths(store):
    project = store.create_project("P", reason="init")
    pid = project["project"]["id"]
    with pytest.raises(ValueError, match="progress=1"):
        store.create_node(pid, {"node_type": "task", "title": "T", "status": "completed", "progress": 0.5}, reason="invalid")
    node = store.create_node(pid, {"node_type": "task", "title": "T"}, reason="add")["node"]
    with pytest.raises(ValueError, match="progress=1"):
        store.update_node(node["id"], 1, {"status": "completed", "progress": 0.5}, reason="invalid")
    with pytest.raises(ValueError, match="progress=1"):
        store.apply_batch(pid, [{"op": "update_node", "node_id": node["id"], "patch": {"status": "completed", "progress": 0.5}}], reason="invalid")
    with pytest.raises(ValueError, match="acceptance criteria"):
        store.update_node(node["id"], 1, {"status": "completed", "progress": 1, "acceptance_criteria": [{"title": "test", "met": False}]}, reason="invalid")
    valid = store.update_node(node["id"], 1, {"status": "completed", "progress": 1}, reason="finish")
    assert valid["node"]["completed_at"] is not None
    reopened = store.update_node(node["id"], 2, {"status": "in_progress", "progress": 0.5}, reason="reopen")
    assert reopened["node"]["completed_at"] is None


def test_goal_manager_complete_checks_required_children(store):
    project = store.create_project("P", reason="init")
    pid = project["project"]["id"]
    child = store.create_node(pid, {"node_type": "task", "title": "Child"}, reason="add")["node"]
    store.create_edge(
        {"source_id": project["root"]["id"], "target_id": child["id"], "edge_type": "decomposes_to"},
        reason="decompose",
    )
    check = store.completion_check(project["root"]["id"])
    assert check["valid"] is False
    assert check["blockers"][0]["node_id"] == child["id"]
    with pytest.raises(GoalConflict, match="completion blocked"):
        store.complete_node(project["root"]["id"], reason="finish")
    store.update_node(child["id"], 1, {"status": "completed", "progress": 1}, reason="finish child")
    completed = store.complete_node(project["root"]["id"], reason="finish")
    assert completed["node"]["status"] == "completed"


def test_optimistic_lock_and_soft_delete(store):
    project = store.create_project("P", reason="init")
    node = store.create_node(project["project"]["id"], {"node_type": "task", "title": "T"}, reason="add")["node"]
    updated = store.update_node(node["id"], 1, {"title": "T2"}, reason="rename")
    assert updated["node"]["version"] == 2
    with pytest.raises(GoalConflict) as error:
        store.update_node(node["id"], 1, {"title": "stale"}, reason="stale")
    assert error.value.payload["latest"]["version"] == 2
    deleted = store.delete_node(node["id"], reason="remove")
    assert deleted["node"]["deleted_at"] is not None
    with pytest.raises(KeyError):
        store.get_node(node["id"])
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM goal_nodes WHERE id=?", (node["id"],)).fetchone()[0] == 1


def test_confirm_token_for_root_delete(store):
    project = store.create_project("P", reason="init")
    root_id = project["root"]["id"]
    with pytest.raises(Exception) as error:
        store.delete_node(root_id, cascade=True, reason="delete root")
    token = getattr(error.value, "token", None)
    assert token
    deleted = store.delete_node(root_id, cascade=True, reason="delete root", confirm_token=token)
    assert deleted["node"]["deleted_at"] is not None


def test_next_actions_respects_dependency_direction(store):
    project = store.create_project("P", reason="init")
    pid = project["project"]["id"]
    prerequisite = store.create_node(pid, {"node_type": "task", "title": "Prerequisite"}, reason="add")["node"]
    dependent = store.create_node(pid, {"node_type": "task", "title": "Dependent", "priority": 10}, reason="add")["node"]
    store.create_edge(
        {"source_id": dependent["id"], "target_id": prerequisite["id"], "edge_type": "depends_on"},
        reason="wire dependency",
    )
    assert dependent["id"] not in {item["id"] for item in store.next_actions(pid)}
    store.update_node(prerequisite["id"], 1, {"status": "completed", "progress": 1}, reason="finish")
    assert dependent["id"] in {item["id"] for item in store.next_actions(pid)}


def test_execution_protocol_intent_review_and_actions(store):
    project = store.create_project("Protocol", reason="init", root_status="in_progress")
    pid = project["project"]["id"]
    assert store.protocol_next_action(pid)["action_type"] == "clarify"
    review = store.review_plan(pid)
    assert review["valid"] is False
    assert any(item["code"] == "missing_intent_contract" for item in review["findings"])

    store.set_intent_contract(
        pid,
        {"outcome": "ship", "success_criteria": ["tests pass"], "open_questions": ["which environment?"]},
        reason="capture intent",
    )
    assert store.protocol_next_action(pid)["action_type"] == "clarify"

    store.set_intent_contract(
        pid,
        {"outcome": "ship", "success_criteria": ["tests pass"]},
        reason="resolve intent",
    )
    task = store.create_node(
        pid,
        {"node_type": "task", "title": "Run tests", "acceptance_criteria": [{"title": "tests pass"}]},
        reason="add task",
    )["node"]
    store.create_edge(
        {"source_id": project["root"]["id"], "target_id": task["id"], "edge_type": "decomposes_to"},
        reason="decompose",
    )
    action = store.protocol_next_action(pid)
    assert action["action_type"] == "execute"
    assert action["nodes"][0]["id"] == task["id"]


def test_execution_protocol_investigate_and_blocked(store):
    project = store.create_project("Protocol", reason="init", root_status="in_progress")
    pid = project["project"]["id"]
    store.set_intent_contract(pid, {"outcome": "ship"}, reason="intent")
    task = store.create_node(
        pid,
        {
            "node_type": "task", "title": "Research", "acceptance_criteria": [
                {"title": "answer unknown", "requires_investigation": True}
            ],
        },
        reason="add task",
    )["node"]
    store.create_edge(
        {"source_id": project["root"]["id"], "target_id": task["id"], "edge_type": "decomposes_to"},
        reason="decompose",
    )
    assert store.protocol_next_action(pid)["action_type"] == "investigate"

    store.update_node(task["id"], 1, {"status": "blocked"}, reason="external blocker")
    assert store.protocol_next_action(pid)["action_type"] == "blocked"


def test_intent_contract_rollback_and_redo(store):
    project = store.create_project("Protocol", reason="init")
    pid = project["project"]["id"]
    applied = store.set_intent_contract(pid, {"outcome": "first"}, reason="first intent")
    store.rollback(applied["batch_id"])
    assert store.get_intent_contract(pid)["intent_contract"] is None
    store.redo(project_id=pid)
    assert store.get_intent_contract(pid)["intent_contract"]["outcome"] == "first"


def test_plan_versions_capture_snapshots_and_replan_diff(store):
    project = store.create_project("Versioned", reason="init")
    pid = project["project"]["id"]
    first = store.create_plan_version(pid, reason="baseline")
    assert first["plan_version"]["version"] == 1
    assert first["plan_version"]["diff"] is None

    task = store.create_node(pid, {"node_type": "task", "title": "Implement"}, reason="add task")["node"]
    second = store.replan(pid, reason="expand execution layer")
    version = second["plan_version"]
    assert version["version"] == 2
    assert [item["id"] for item in version["diff"]["added_nodes"]] == [task["id"]]
    listed = store.list_plan_versions(pid)
    assert [item["after"]["version"] for item in listed] == [2, 1]

    store.rollback(second["batch_id"])
    assert [item["after"]["version"] for item in store.list_plan_versions(pid)] == [1]
    store.redo(project_id=pid)
    assert [item["after"]["version"] for item in store.list_plan_versions(pid)] == [2, 1]


def test_lifecycle_records_are_auditable_and_do_not_complete_nodes(store):
    project = store.create_project("Lifecycle", reason="init")
    pid = project["project"]["id"]
    task = store.create_node(
        pid,
        {"node_type": "task", "title": "Deploy", "acceptance_criteria": [{"title": "health check", "met": False}]},
        reason="add task",
    )["node"]

    execution = store.record_execution_result(
        task["id"], {"status": "succeeded", "summary": "deployment command completed", "outputs": {"release": "r1"}},
        reason="record command",
    )
    observation = store.record_observation(
        task["id"], {"execution_result_id": execution["execution_result"]["id"], "summary": "health endpoint returned 200", "signals": ["200"]},
        reason="observe service",
    )
    verification = store.verify_evidence(
        task["id"], {"accepted": True, "summary": "health evidence reviewed", "criterion_index": 0},
        reason="verify evidence",
    )
    acceptance = store.accept_result(
        task["id"], {"accepted": True, "summary": "release accepted", "accepted_by": "reviewer-1"},
        reason="accept result",
    )

    lifecycle = store.get_lifecycle(task["id"])
    assert lifecycle["execution_results"][0]["after"]["id"] == execution["execution_result"]["id"]
    assert lifecycle["observations"][0]["after"]["id"] == observation["observation"]["id"]
    assert lifecycle["evidence_verifications"][0]["after"]["id"] == verification["evidence_verification"]["id"]
    assert lifecycle["result_acceptances"][0]["after"]["id"] == acceptance["result_acceptance"]["id"]
    assert store.get_context(task["id"])["lifecycle"] == lifecycle

    with pytest.raises(GoalConflict, match="goal completion blocked"):
        store.complete_node(task["id"], reason="lifecycle alone is not completion")


def test_lifecycle_records_rollback_and_redo_without_duplicates(store):
    project = store.create_project("Lifecycle", reason="init")
    task = store.create_node(project["project"]["id"], {"node_type": "task", "title": "Run"}, reason="add")["node"]
    recorded = store.record_execution_result(
        task["id"], {"status": "succeeded", "summary": "ran"}, reason="record",
    )

    store.rollback(recorded["batch_id"])
    assert store.get_lifecycle(task["id"])["execution_results"] == []
    store.redo(project_id=project["project"]["id"])
    results = store.get_lifecycle(task["id"])["execution_results"]
    assert len(results) == 1
    assert results[0]["after"]["id"] == recorded["execution_result"]["id"]


def test_verified_evidence_can_be_applied_then_human_review_approves(store):
    project = store.create_project("Reviewed", reason="init")
    task = store.create_node(
        project["project"]["id"],
        {
            "node_type": "task",
            "title": "Release",
            "progress": 1,
            "acceptance_criteria": [{"title": "production verified", "met": False}],
        },
        reason="add task",
    )["node"]
    rejected = store.verify_evidence(
        task["id"], {"accepted": False, "summary": "health check failed", "criterion_index": 0}, reason="reject",
    )
    with pytest.raises(GoalConflict, match="was not accepted"):
        store.apply_evidence_verification(
            task["id"], rejected["evidence_verification"]["id"], 1, reason="apply rejected",
        )

    verified = store.verify_evidence(
        task["id"], {"accepted": True, "summary": "health check passed", "criterion_index": 0}, reason="verify",
    )
    applied = store.apply_evidence_verification(
        task["id"], verified["evidence_verification"]["id"], 1, reason="apply verified",
    )
    assert applied["node"]["acceptance_criteria"][0]["met"] is True
    assert applied["node"]["acceptance_criteria"][0]["verification_id"] == verified["evidence_verification"]["id"]

    review = store.submit_for_review(task["id"], 2, reason="request approval")
    assert review["node"]["status"] == "waiting_review"
    with pytest.raises(ValueError, match="requires actor_type"):
        store.approve_review(task["id"], 3, reason="agent cannot approve", actor_type="agent")
    completed = store.approve_review(task["id"], 3, reason="review approved", actor_type="user", actor_id="reviewer-1")
    assert completed["node"]["status"] == "completed"
    assert completed["node"]["completed_at"] is not None


def test_review_can_be_rejected_by_human_reviewer(store):
    project = store.create_project("Reviewed", reason="init")
    task = store.create_node(
        project["project"]["id"],
        {
            "node_type": "task", "title": "Release", "progress": 1,
            "acceptance_criteria": [{"title": "verified", "met": True}],
        },
        reason="add task",
    )["node"]
    review = store.submit_for_review(task["id"], 1, reason="submit")
    with pytest.raises(ValueError, match="requires actor_type"):
        store.reject_review(task["id"], 2, reason="agent cannot reject", actor_type="agent")
    rejected = store.reject_review(task["id"], 2, reason="needs correction", actor_type="supervisor")
    assert rejected["node"]["status"] == "in_progress"
    assert rejected["node"]["version"] == review["node"]["version"] + 1
    assert rejected["node"]["acceptance_criteria"][0]["met"] is True


def test_review_workflow_requires_current_version_and_ready_node(store):
    project = store.create_project("Reviewed", reason="init")
    task = store.create_node(
        project["project"]["id"],
        {"node_type": "task", "title": "Release", "acceptance_criteria": [{"title": "verified", "met": False}]},
        reason="add task",
    )["node"]
    with pytest.raises(GoalConflict, match="submission blocked") as blocked:
        store.submit_for_review(task["id"], 1, reason="too early")
    assert {item["code"] for item in blocked.value.payload["blockers"]} == {
        "acceptance_criteria_unmet", "progress_incomplete"
    }
    store.update_node(task["id"], 1, {"progress": 1, "acceptance_criteria": [{"title": "verified", "met": True}]}, reason="ready")
    with pytest.raises(GoalConflict, match="version conflict"):
        store.submit_for_review(task["id"], 1, reason="stale")


def test_api_and_tool_schemas(tmp_path):
    db_path = tmp_path / "test_goal_service_api.db"
    app = create_app({"db_path": str(db_path)})
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/goals/projects",
                json={"name": "API", "reason": "M1 test", "idempotency_key": "api:key-1"},
            )
            assert response.status_code == 201
            project_id = response.json()["project"]["id"]
            root_id = response.json()["root"]["id"]
            assert client.get(f"/api/goals/nodes/{root_id}/completion-check").json()["valid"] is True
            completed = client.post(
                f"/api/goals/nodes/{root_id}/complete",
                json={"reason": "API completion"},
            )
            assert completed.status_code == 200
            assert completed.json()["node"]["status"] == "completed"
            retry = client.post(
                "/api/goals/projects",
                json={"name": "API", "reason": "retry", "idempotency_key": "api:key-1"},
            )
            assert retry.status_code == 201
            assert retry.json()["project"]["id"] == project_id
            edge_response = client.post(
                "/api/goals/edges",
                json={
                    "source_id": root_id,
                    "target_id": root_id,
                    "edge_type": "depends_on",
                    "reason": "invalid self edge",
                },
            )
            assert edge_response.status_code == 409
            assert edge_response.json()["cycle_path"]
            assert client.get(f"/api/goals/projects/{project_id}/next-actions").status_code == 200
            contract = client.put(
                f"/api/goals/projects/{project_id}/intent-contract",
                json={"outcome": "ship", "success_criteria": ["green"], "reason": "API intent"},
            )
            assert contract.status_code == 200
            assert client.get(f"/api/goals/projects/{project_id}/intent-contract").json()["intent_contract"]["outcome"] == "ship"
            assert client.get(f"/api/goals/projects/{project_id}/plan-review").status_code == 200
            protocol = client.get(f"/api/goals/projects/{project_id}/protocol-next-action")
            assert protocol.status_code == 200
            assert protocol.json()["action_type"] == "complete"
            created_version = client.post(
                f"/api/goals/projects/{project_id}/plan-versions",
                json={"reason": "API baseline"},
            )
            assert created_version.status_code == 201
            assert created_version.json()["plan_version"]["version"] == 1
            replanned = client.post(
                f"/api/goals/projects/{project_id}/replan",
                json={"reason": "API replan"},
            )
            assert replanned.status_code == 201
            assert replanned.json()["plan_version"]["version"] == 2
            assert client.get(f"/api/goals/projects/{project_id}/plan-versions").status_code == 200
            task = client.post(
                "/api/goals/nodes",
                json={"project_id": project_id, "node_type": "task", "title": "API lifecycle", "reason": "add task"},
            )
            assert task.status_code == 201
            task_id = task.json()["node"]["id"]
            execution = client.post(
                f"/api/goals/nodes/{task_id}/execution-results",
                json={"status": "succeeded", "summary": "ran", "outputs": ["result"], "reason": "record"},
            )
            assert execution.status_code == 201
            observation = client.post(
                f"/api/goals/nodes/{task_id}/observations",
                json={"execution_result_id": execution.json()["execution_result"]["id"], "summary": "seen", "reason": "observe"},
            )
            assert observation.status_code == 201
            assert client.post(
                f"/api/goals/nodes/{task_id}/evidence-verifications",
                json={"accepted": True, "summary": "verified", "reason": "verify"},
            ).status_code == 201
            assert client.post(
                f"/api/goals/nodes/{task_id}/result-acceptance",
                json={"accepted": True, "summary": "accepted", "reason": "accept"},
            ).status_code == 201
            lifecycle = client.get(f"/api/goals/nodes/{task_id}/lifecycle")
            assert lifecycle.status_code == 200
            assert len(lifecycle.json()["execution_results"]) == 1
            assert len(lifecycle.json()["observations"]) == 1
            assert len(lifecycle.json()["evidence_verifications"]) == 1
            assert len(lifecycle.json()["result_acceptances"]) == 1
            review_task = client.post(
                "/api/goals/nodes",
                json={
                    "project_id": project_id, "node_type": "task", "title": "API review", "progress": 1,
                    "acceptance_criteria": [{"title": "verified", "met": False}], "reason": "add review task",
                },
            )
            assert review_task.status_code == 201
            review_task_id = review_task.json()["node"]["id"]
            verification = client.post(
                f"/api/goals/nodes/{review_task_id}/evidence-verifications",
                json={"accepted": True, "summary": "verified", "criterion_index": 0, "reason": "verify"},
            )
            assert verification.status_code == 201
            applied = client.post(
                f"/api/goals/nodes/{review_task_id}/apply-evidence-verification",
                json={"verification_id": verification.json()["evidence_verification"]["id"], "expected_version": 1, "reason": "apply"},
            )
            assert applied.status_code == 200
            submitted = client.post(
                f"/api/goals/nodes/{review_task_id}/submit-for-review",
                json={"expected_version": 2, "reason": "submit"},
            )
            assert submitted.status_code == 200
            assert submitted.json()["node"]["status"] == "waiting_review"
            approved = client.post(
                f"/api/goals/nodes/{review_task_id}/approve-review",
                json={"expected_version": 3, "reason": "approve", "actor_type": "user", "actor_id": "reviewer"},
            )
            assert approved.status_code == 200
            assert approved.json()["node"]["status"] == "completed"
            reject_task = client.post(
                "/api/goals/nodes",
                json={
                    "project_id": project_id, "node_type": "task", "title": "API reject", "progress": 1,
                    "acceptance_criteria": [{"title": "verified", "met": True}], "reason": "add reject task",
                },
            ).json()["node"]
            assert client.post(
                f"/api/goals/nodes/{reject_task['id']}/submit-for-review",
                json={"expected_version": 1, "reason": "submit"},
            ).status_code == 200
            rejected = client.post(
                f"/api/goals/nodes/{reject_task['id']}/reject-review",
                json={"expected_version": 2, "reason": "reject", "actor_type": "user"},
            )
            assert rejected.status_code == 200
            assert rejected.json()["node"]["status"] == "in_progress"
    finally:
        app.state.goal_store.db_path.unlink(missing_ok=True)
        app.state.goal_store.db_path.with_name("test_goal_service_api.db.owner").unlink(missing_ok=True)

    assert len(SCHEMAS) == 24
    from plugins.goal_manager.tools.agent_tools import NON_IDEMPOTENT_WRITE_TOOLS, READ_TOOLS
    assert READ_TOOLS.isdisjoint(NON_IDEMPOTENT_WRITE_TOOLS)
    assert {"goal_protocol_next_action", "goal_plan_review", "goal_lifecycle_get"} <= READ_TOOLS
    assert {
        "goal_intent_contract_set", "goal_replan", "goal_record_execution_result",
        "goal_record_observation", "goal_verify_evidence", "goal_apply_evidence_verification",
        "goal_submit_for_review",
    } <= NON_IDEMPOTENT_WRITE_TOOLS
    for schema in SCHEMAS.values():
        assert schema["parameters"]["type"] == "object"
        assert "properties" in schema["parameters"]

    plugin_registry.reset_scan_cache()
    model_tools._tools_discovered = False
    model_tools.ensure_tools_discovered()
    definitions = registry.get_definitions(list(SCHEMAS))
    assert {item["function"]["name"] for item in definitions} >= set(SCHEMAS)


def test_event_stream_uses_event_id_cursor(tmp_path):
    app = create_app({"db_path": str(tmp_path / "goals.db")})
    with TestClient(app) as client:
        created = client.post(
            "/api/goals/projects",
            json={"name": "SSE", "reason": "stream test"},
        )
        assert created.status_code == 201
        project_id = created.json()["project"]["id"]
        events = client.get(f"/api/goals/events?project_id={project_id}").json()["events"]
        assert events

        response = client.get(
            f"/api/goals/events/stream?project_id={project_id}"
            f"&after={events[0]['id']}&poll_seconds=0.2&max_seconds=1"
        )
        initial = client.get(
            f"/api/goals/events/stream?project_id={project_id}"
            "&poll_seconds=0.2&max_seconds=1"
        )
        canonical = client.get(
            f"/api/goals/projects/{project_id}/events"
            f"?after={events[0]['id']}&poll_seconds=0.2&max_seconds=1"
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert f"id: {events[1]['id']}" in response.text
    assert f"id: {events[0]['id']}" not in response.text
    assert "id:" not in initial.text
    assert canonical.status_code == 200
    assert f"id: {events[1]['id']}" in canonical.text
