"""M1 contract tests for the Goal Manager service and Agent tools."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from plugins.goal_manager.db.connection import GoalStore
from plugins.goal_manager.domain.graph import GoalConflict
from plugins.goal_manager.server import create_app
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


def test_api_and_tool_schemas(tmp_path):
    db_path = tmp_path / "test_goal_service_api.db"
    app = create_app({"db_path": str(db_path)})
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/goals/projects",
                json={"name": "API", "reason": "M1 test"},
            )
            assert response.status_code == 201
            project_id = response.json()["project"]["id"]
            root_id = response.json()["root"]["id"]
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
    finally:
        app.state.goal_store.db_path.unlink(missing_ok=True)
        app.state.goal_store.db_path.with_name("test_goal_service_api.db.owner").unlink(missing_ok=True)

    assert len(SCHEMAS) == 14
    from plugins.goal_manager.tools.agent_tools import NON_IDEMPOTENT_WRITE_TOOLS, READ_TOOLS
    assert READ_TOOLS.isdisjoint(NON_IDEMPOTENT_WRITE_TOOLS)
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
