from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from plugins.memory.mem.outbox import MemoryWriteOutbox

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voidcube.infrastructure.gateway.internal_gateway import GatewayConfig, InternalGateway, ServiceInfo


@pytest.mark.asyncio
async def test_autonomous_tier1_finding_uses_durable_gateway_outbox(tmp_path):
    gateway = InternalGateway(GatewayConfig())
    gateway._memory_outbox = MemoryWriteOutbox(tmp_path / "gateway.sqlite3")

    await gateway._record_turn_to_tier1(
        "autonomous-session",
        "agent",
        "执行成果已完成。",
        metadata={"task_id": "task-1", "source": "autonomous_task_finding"},
    )

    item = gateway._memory_outbox.next_due()
    assert item is not None
    assert item["memory_actor"] == "api_a"
    assert item["memory_domain"] == "agent_interaction"
    assert item["metadata"]["turn_dedup_key"] == item["write_id"]
    gateway._memory_outbox.mark_delivered(item["write_id"])
    assert gateway._memory_outbox.pending_count() == 0


def test_gateway_binds_memory_scope_to_registered_session():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)
    first = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "scoped-session",
            "owner_id": "owner-a",
            "workspace_id": "workspace-a",
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "scoped-session",
            "owner_id": "owner-b",
            "workspace_id": "workspace-b",
        },
    )
    assert second.status_code == 409
    assert gateway._agent_session_cache["scoped-session"]["owner_id"] == "owner-a"

    body, query = gateway._inject_memory_actor(
        json.dumps(
            {
                "owner_id": "attacker-owner",
                "workspace_id": "attacker-workspace",
            }
        ).encode("utf-8"),
        [("owner_id", "attacker-owner"), ("workspace_id", "attacker-workspace")],
        method="POST",
        memory_actor="api_a",
        memory_scope={"owner_id": "owner-a", "workspace_id": "workspace-a"},
    )
    assert json.loads(body) == {
        "owner_id": "owner-a",
        "workspace_id": "workspace-a",
        "memory_actor": "api_a",
    }
    assert query == [
        ("memory_actor", "api_a"),
        ("owner_id", "owner-a"),
        ("workspace_id", "workspace-a"),
    ]


def test_gateway_allows_cli_and_memory_provider_to_share_scope():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)
    payload = {
        "session_id": "shared-scope-session",
        "owner_id": "local-user",
        "workspace_id": "VoidCube",
    }

    cli_registration = client.post(
        "/v1/sessions/register",
        json={**payload, "source": "cli"},
    )
    memory_registration = client.post(
        "/v1/sessions/register",
        json={**payload, "source": "agent_memory_provider"},
    )

    assert cli_registration.status_code == 200
    assert memory_registration.status_code == 200


def _register_memory_target(client: TestClient) -> None:
    response = client.post(
        "/register",
        json={
            "service_name": "memory-service",
            "service_type": "memory",
            "address": "http://memory-service",
        },
    )
    assert response.status_code == 201


def _register_agent_identity(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/register",
        json={
            "service_name": "api-a-agent",
            "service_type": "agent",
            "address": "http://agent-service",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    return {
        "X-VoidCube-Service-Id": payload["service_id"],
        "X-VoidCube-Service-Token": payload["service_token"],
    }


def test_gateway_activity_touch_endpoint_updates_snapshot():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.get("/admin/activity")
    assert response.status_code == 200
    initial = response.json()
    assert initial["last_user_request_at"] is None
    assert initial["last_self_learning_activity_at"] is None
    assert initial["last_autonomous_chain_activity_at"] is None
    assert initial["counts"]["self_learning_activity_count"] == 0
    assert initial["counts"]["autonomous_chain_activity_count"] == 0

    touch_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain",
            "source_service": "supervisor",
        },
    )
    assert touch_response.status_code == 200

    updated = client.get("/admin/activity").json()
    assert updated["last_autonomous_chain_activity_at"] is not None
    assert updated["counts"]["autonomous_chain_activity_count"] == 1

    plan_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_plan",
            "source_service": "supervisor",
        },
    )
    execute_response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_execute",
            "source_service": "executor",
        },
    )

    assert plan_response.status_code == 200
    assert execute_response.status_code == 200
    refined = client.get("/admin/activity").json()
    assert refined["last_autonomous_chain_plan_at"] is not None
    assert refined["last_autonomous_chain_execute_at"] is not None
    assert refined["counts"]["autonomous_chain_activity_count"] == 3
    assert refined["counts"]["autonomous_chain_plan_count"] == 1
    assert refined["counts"]["autonomous_chain_execute_count"] == 1
    assert refined["recent_metadata"]["autonomous_chain"]["source_service"] == "executor"
    assert refined["recent_metadata"]["autonomous_chain_plan"]["source_service"] == "supervisor"
    assert refined["recent_metadata"]["autonomous_chain_execute"]["source_service"] == "executor"


def test_gateway_activity_touch_derives_runtime_task_profile_from_broad_metadata():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_plan",
            "source_service": "supervisor",
            "metadata": {
                "trace_id": "trace-plan-1",
                "task_type": "self_evolution",
                "kind": "body_switch",
                "decision_id": "decision-plan-1",
            },
        },
    )

    assert response.status_code == 200
    activity = client.get("/admin/activity").json()
    metadata = activity["recent_metadata"]["autonomous_chain_plan"]
    assert metadata["trace_id"] == "trace-plan-1"
    assert metadata["governance_task_type"] == "self_evolution"
    assert metadata["governance_task_type_label"] == "自主改进"
    assert metadata["task_family"] == "body_switch"
    assert metadata["task_family_label"] == "身体切换"
    assert metadata["execution_kind"] == "body_switch"
    assert metadata["execution_kind_label"] == "身体切换"
    assert metadata["decision_id"] == "decision-plan-1"
    assert metadata["task_identity"]["display_kind"] == "body_switch"
    assert metadata["task_identity"]["display_label"] == "身体切换"
    assert metadata["task_identity"]["requested_kind"] == "body_switch"
    assert metadata["task_identity"]["requested_kind_label"] == "身体切换"
    assert metadata["task_identity"]["summary"] == "身体切换"
    assert "task_type" not in metadata
    assert "task_type_label" not in metadata


def test_gateway_activity_touch_derives_runtime_task_profile_from_nested_runtime_profile():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_execute",
            "source_service": "executor",
            "metadata": {
                "trace_id": "trace-exec-2",
                "runtime_task_profile": {
                    "governance_task_type": "memory_maintenance",
                    "task_family": "memory_maintenance",
                    "execution_kind": "memory_maintenance",
                },
                "decision_id": "decision-exec-2",
            },
        },
    )

    assert response.status_code == 200
    activity = client.get("/admin/activity").json()
    metadata = activity["recent_metadata"]["autonomous_chain_execute"]
    assert metadata["trace_id"] == "trace-exec-2"
    assert metadata["governance_task_type"] == "memory_maintenance"
    assert metadata["governance_task_type_label"] == "记忆维护"
    assert metadata["task_family"] == "memory_maintenance"
    assert metadata["task_family_label"] == "记忆维护"
    assert metadata["execution_kind"] == "memory_maintenance"
    assert metadata["execution_kind_label"] == "记忆维护"
    assert metadata["decision_id"] == "decision-exec-2"


def test_gateway_activity_log_is_bounded_and_trace_filterable():
    gateway = InternalGateway(GatewayConfig(activity_log_limit=2))
    client = TestClient(gateway.app)

    first = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_plan",
            "source_service": "supervisor",
            "metadata": {
                "trace_id": "trace-log-1",
                "task_id": "task-log-1",
                "task_family": "body_switch",
            },
        },
    )
    second = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "self_learning",
            "source_service": "self-learning",
            "metadata": {
                "trace_id": "trace-log-2",
                "task_id": "task-log-2",
                "task_family": "self_learning",
            },
        },
    )
    ignored = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "unsupported",
            "source_service": "unknown",
            "metadata": {"trace_id": "trace-ignored"},
        },
    )
    third = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "autonomous_chain_execute",
            "source_service": "executor",
            "metadata": {
                "trace_id": "trace-log-1",
                "task_id": "task-log-3",
                "kind": "body_switch",
            },
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert ignored.status_code == 200
    assert third.status_code == 200

    log = client.get("/admin/activity/log").json()
    assert log["status"] == "ok"
    assert log["activity_log_limit"] == 2
    assert log["count"] == 2
    assert [event["activity_kind"] for event in log["events"]] == [
        "autonomous_chain_execute",
        "self_learning",
    ]
    assert all(event["metadata"].get("trace_id") != "trace-ignored" for event in log["events"])

    filtered = client.get(
        "/admin/activity/log",
        params={"trace_id": "trace-log-1", "limit": 10},
    ).json()
    assert filtered["count"] == 1
    assert filtered["events"][0]["activity_kind"] == "autonomous_chain_execute"
    assert filtered["events"][0]["metadata"]["trace_id"] == "trace-log-1"
    assert filtered["events"][0]["metadata"]["task_family"] == "body_switch"
    assert filtered["events"][0]["metadata"]["execution_kind"] == "body_switch"
    assert filtered["events"][0]["metadata"]["task_identity"]["display_kind"] == "body_switch"


def test_gateway_does_not_publish_self_learning_service_route():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "self-learning-1",
            "service_name": "archived-learning-record-service",
            "service_type": "self_learning",
            "address": "http://127.0.0.1:65526",
        },
    )
    assert register_response.status_code == 201

    health = client.get("/").json()
    assert "self_learning" not in health["registered_services"]

    routes = client.get("/admin/routes").json()
    retired_proxy_prefix = "/" + "self" + "-learning/"
    assert all(route["path_prefix"] != retired_proxy_prefix for route in routes["routes"])

    response = client.post(
        "/api/" + "self" + "-learning/conclusions/submit",
        json={"summary": "noop"},
    )

    assert response.status_code == 404
    activity = client.get("/admin/activity").json()
    assert activity["last_self_learning_activity_at"] is None
    assert activity["counts"]["self_learning_activity_count"] == 0


def test_gateway_agent_query_rejects_legacy_proxy_without_memory_write(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)
    recorded_turns = []

    async def record_turn(*args, **kwargs):
        recorded_turns.append((args, kwargs))

    monkeypatch.setattr(gateway, "_record_turn_to_tier1", record_turn)

    register_response = client.post(
        "/register",
        json={
            "service_name": "agent-slot-A",
            "service_type": "agent",
            "address": "http://127.0.0.1:65530",
            "metadata": {"slot_id": "slot-A", "body_version": "bootstrap"},
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/v1/agent/query",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 410
    assert "Gateway agent proxy has been removed" in response.json()["detail"]

    activity = client.get("/admin/activity").json()
    assert activity["last_user_request_at"] is not None
    assert activity["counts"]["user_request_count"] == 1
    assert activity["counts"]["agent_work_count"] == 0
    assert recorded_turns == []


def test_gateway_agent_query_still_records_activity_while_autonomous_gate_is_active():
    gateway = InternalGateway(GatewayConfig())
    gateway._autonomous_chain_gate_active = True
    client = TestClient(gateway.app)

    response = client.post(
        "/v1/agent/query",
        json={"session_id": "cli-session-1", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 410
    assert "Gateway agent proxy has been removed" in response.json()["detail"]

    activity = client.get("/admin/activity").json()
    assert activity["last_user_request_at"] is not None
    assert activity["counts"]["user_request_count"] == 1


def test_gateway_exposes_only_autonomous_chain_gate_admin_route():
    gateway = InternalGateway(GatewayConfig())
    route_paths = {route.path for route in gateway.app.routes}

    assert "/admin/autonomous-chain-gate" in route_paths
    deprecated_gate_route = "/admin/" + "gover" + "nor-mode"
    assert deprecated_gate_route not in route_paths


def test_gateway_body_status_exposes_cli_executor_and_passive_body_slots_only():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/register",
        json={
            "service_id": "agent-slot-a",
            "service_name": "agent-slot-A",
            "service_type": "agent",
            "address": "http://127.0.0.1:65530",
            "metadata": {"slot_id": "slot-A", "body_version": "bootstrap"},
        },
    )
    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    status = client.get("/admin/body/status")
    assert status.status_code == 200
    payload = status.json()
    assert "active_body" not in payload
    assert "body_routing" not in payload
    assert payload["active_cli_executor"]["session_id"] == "cli-session-1"
    assert payload["body_slots"][0]["slot_id"] == "slot-A"
    assert "lifecycle_state" not in payload["body_slots"][0]
    assert "is_active_body" not in payload["body_slots"][0]


def test_gateway_activity_touch_adds_task_identity_summary():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_work",
            "source_service": "cli_agent",
            "metadata": {
                "task_id": "body-1",
                "title": "改进 shell 替身",
                "execution_kind": "body_improvement",
                "task_family": "body_upgrade",
                "governance_task_type": "self_evolution",
            },
        },
    )

    assert response.status_code == 200
    activity = client.get("/admin/activity").json()
    metadata = activity["recent_metadata"]["agent_work"]
    identity = metadata["task_identity"]
    assert identity["task_id"] == "body-1"
    assert identity["title"] == "改进 shell 替身"
    assert identity["execution_kind"] == "body_improvement"
    assert identity["display_kind"] == "body_improvement"
    assert identity["governance_task_type_label"] == "自主改进"
    assert identity["task_family_label"] == "替身升级"
    assert identity["execution_kind_label"] == "替身改进"
    assert identity["display_label"] == "替身改进"
    assert "改进 shell 替身 (替身改进)" == identity["summary"]
    assert "labels" not in identity
    assert "label_texts" not in identity


def test_gateway_agent_scene_touch_updates_scene_cache_and_prefers_cli_agent():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    session_response = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    assert session_response.status_code == 200

    response = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": "cli-session-1",
            "metadata": {
                "scene": "learning",
                "task_id": "learn-1",
                "execution_kind": "self_learning",
                "subagent_foreground_count": 2,
                "subagent_background_count": 1,
                "subagent_total_count": 3,
                "subagent_focus_task_id": "delegate-1",
                "subagent_focus_tool": "read_file",
                "subagent_focus_preview": "read_file",
            },
        },
    )

    assert response.status_code == 200
    scenes = client.get("/admin/scenes").json()["scenes"]
    summary = client.get("/admin/scenes").json()["summary"]
    assert scenes["agent"]["scene"] == "learning"
    assert scenes["agent"]["scene_projection_scope"] == "agent_top_level_projection"
    assert scenes["agent"]["canonical_lanes"] == ["supervisor_task", "user_chat"]
    assert scenes["agent"]["lane_contract"]["supervisor_task"] == "autonomous_chain_observation"
    assert scenes["agent"]["lane_contract"]["user_chat"] == "user_chain_status"
    assert scenes["agent"]["scene_task_id"] == "learn-1"
    assert scenes["agent"]["source_service"] == "cli_agent"
    assert scenes["agent"]["subagent_foreground_count"] == 2
    assert scenes["agent"]["subagent_background_count"] == 1
    assert scenes["agent"]["subagent_focus_tool"] == "read_file"
    assert summary["agent"] == "idle"
    assert summary["agent_user_chat"] == "idle"
    assert summary["agent_supervisor_task"] == "learning"

    health = client.get("/").json()
    active_cli = health["active_cli_executor"]
    assert active_cli["session_id"] == "cli-session-1"
    assert active_cli["is_active_cli_executor"] is True
    assert active_cli["scene"] == "learning"
    assert active_cli["scene_projection_scope"] == "agent_lane"
    assert active_cli["agent_lane"] == "supervisor_task"
    assert active_cli["subagent_foreground_count"] == 2
    assert active_cli["subagent_background_count"] == 1
    assert active_cli["subagent_focus_preview"] == "read_file"
    assert active_cli["lease_status"] == "healthy"
    assert active_cli["is_stale"] is False


def test_gateway_active_cli_executor_marks_stale_lease():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-stale",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    assert register.status_code == 200

    gateway._agent_session_cache["cli-session-stale"]["last_used_at"] = (
        datetime.now() - timedelta(seconds=gateway._active_cli_stale_after_seconds + 5)
    )

    status = client.get("/admin/body/status").json()
    active_cli = status["active_cli_executor"]
    assert active_cli["session_id"] == "cli-session-stale"
    assert active_cli["lease_status"] == "stale"
    assert active_cli["is_stale"] is True
    assert active_cli["idle_seconds"] >= gateway._active_cli_stale_after_seconds


def test_gateway_register_session_does_not_override_existing_active_cli_executor():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    first = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    second = client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-2",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    status = client.get("/admin/body/status").json()
    assert status["active_cli_executor"]["session_id"] == "cli-session-1"


def test_gateway_idle_scene_does_not_steal_active_cli_executor():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )
    client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": "cli-session-1",
            "metadata": {
                "scene": "learning",
                "task_id": "learn-1",
                "execution_kind": "self_learning",
            },
        },
    )
    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-2",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    idle_touch = client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": "cli-session-2",
            "metadata": {
                "scene": "idle",
            },
        },
    )

    assert idle_touch.status_code == 200
    status = client.get("/admin/body/status").json()
    assert status["active_cli_executor"]["session_id"] == "cli-session-1"
    assert status["active_cli_executor"]["scene"] == "learning"


def test_gateway_active_cli_executor_does_not_fallback_to_agent_top_level_scene():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/v1/sessions/register",
        json={
            "session_id": "cli-session-1",
            "model": "agnes-2.0-flash",
            "provider": "agnesai",
            "source": "cli",
        },
    )

    # Simulate a stale top-level agent scene without a matching lane owner.
    gateway._active_cli_session_id = "cli-session-1"
    gateway._scenes_cache["agent"].update(
        {
            "scene": "executing",
            "scene_projection_scope": "agent_top_level_projection",
            "source_service": "cli_agent",
            "subagent_focus_tool": "grep",
        }
    )

    status = client.get("/admin/body/status").json()
    active_cli = status["active_cli_executor"]

    assert active_cli["session_id"] == "cli-session-1"
    assert active_cli.get("scene") is None
    assert active_cli.get("scene_projection_scope") is None
    assert active_cli.get("agent_lane") is None
    assert active_cli.get("subagent_focus_tool") is None




def test_gateway_recovers_evicted_cli_session_from_matching_execution_lease():
    gateway = InternalGateway(GatewayConfig())
    task = {
        "task_id": "learn-recover",
        "status": "running",
        "governance_task_type": "self_learning",
        "execution_lease": {
            "generation": 4,
            "attempt_id": "attempt-recover",
            "owner_session_id": "cli-session-recover",
            "state": "active",
        },
    }
    data = {
        "session_id": "cli-session-recover",
        "execution_lease": {
            "generation": 4,
            "attempt_id": "attempt-recover",
        },
    }

    recovered = gateway._validate_agent_pull_session(
        task_id="learn-recover",
        task=task,
        data=data,
        decision="completed",
        actor="cli_agent",
    )

    assert recovered == "cli-session-recover"
    with pytest.raises(HTTPException) as exc:
        gateway._validate_agent_pull_session(
            task_id="learn-recover",
            task=task,
            data={**data, "execution_lease": {"generation": 4, "attempt_id": "wrong"}},
            decision="completed",
            actor="cli_agent",
        )
    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "path",
    [
        "/api/mem/recall",
        "/api/mem/remember",
        "/api/mem/recall/traces?session_id=session-1&status=hit&limit=3",
        "/api/mem/promotion-candidates",
        "/api/mem/promotion-candidates/candidate-1/consent",
    ],
)
def test_gateway_retired_memory_proxy_returns_410(path):
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    response = client.get(path) if path.endswith("traces?session_id=session-1&status=hit&limit=3") else client.post(path, json={})

    assert response.status_code == 410
    assert "retired" in response.json()["detail"]


def test_memory_client_binds_actor_and_scope_and_rejects_spoofed_fields(monkeypatch):
    from voidcube.infrastructure.memory.client import MemoryClient, MemoryClientIdentity

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"candidates":[]}'

    def fake_urlopen(request, timeout=None):
        del timeout
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr(
        "voidcube.infrastructure.memory.client.urlopen",
        fake_urlopen,
    )
    client = MemoryClient(
        "http://memory-service",
        identity=MemoryClientIdentity(
            actor="governor",
            owner_id="owner-a",
            workspace_id="workspace-a",
            memory_domain="agent_interaction",
        ),
    )

    response = client.request_json(
        "POST",
        "/promotion-candidates",
        {"reason": "approved by governance", "memory_actor": "governor"},
    )
    assert response == {"candidates": []}
    assert captured["body"]["memory_actor"] == "governor"
    assert captured["body"]["owner_id"] == "owner-a"
    assert captured["body"]["workspace_id"] == "workspace-a"

    with pytest.raises(Exception):
        client.request_json(
            "POST",
            "/promotion-candidates",
            {"reason": "approved by governance", "memory_actor": "api_a"},
        )


def test_gateway_registration_requires_root_token_when_configured():
    gateway = InternalGateway(GatewayConfig(auth_token="root-secret"))
    client = TestClient(gateway.app)
    payload = {
        "service_name": "api-a-agent",
        "service_type": "agent",
        "address": "http://agent-service",
    }

    assert client.post("/register", json=payload).status_code == 401
    response = client.post(
        "/register",
        json=payload,
        headers={"Authorization": "Bearer root-secret"},
    )
    assert response.status_code == 201
    assert response.json()["service_token"]


def test_gateway_records_tier1_turn_in_durable_outbox(monkeypatch, tmp_path):
    gateway = InternalGateway(GatewayConfig())
    gateway._memory_outbox = MemoryWriteOutbox(tmp_path / "gateway.sqlite3")
    gateway._memory_service_url = "http://memory-service"

    asyncio.run(
        gateway._record_turn_to_tier1(
            "session-atomic",
            "user",
            "hello",
            metadata={"source": "test"},
        )
    )

    item = gateway._memory_outbox.next_due()
    assert item is not None
    assert item["session_id"] == "session-atomic"
    assert item["speaker"] == "user"
    assert item["text"] == "hello"
    assert item["metadata"]["turn_dedup_key"] == item["write_id"]


def test_gateway_tier1_enqueue_failure_updates_activity_counter(monkeypatch):
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)
    def fail_enqueue(payload):
        del payload
        raise OSError("memory down")

    monkeypatch.setattr(gateway._memory_outbox, "enqueue", fail_enqueue)

    asyncio.run(
        gateway._record_turn_to_tier1(
            "session-fail",
            "agent",
            "finding",
            metadata={"task_id": "task-fail"},
        )
    )

    activity = client.get("/admin/activity").json()

    assert activity["last_memory_write_failure_at"] is not None
    assert activity["counts"]["memory_write_failure_count"] == 1
    metadata = activity["recent_metadata"]["memory_write_failure"]
    assert metadata["task_id"] == "task-fail"
    assert metadata["speaker"] == "agent"
    assert "memory down" in metadata["error"]






def test_gateway_executor_registration_adds_standard_route_and_health_count():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "executor-1",
            "service_name": "executor-service",
            "service_type": "executor",
            "address": "http://127.0.0.1:65528",
        },
    )

    assert register_response.status_code == 201
    health = client.get("/").json()
    routes = client.get("/admin/routes").json()
    assert health["registered_services"]["executor"] == 1
    assert health["executor_access_policy"]["preferred_gateway_prefix"] == "/api/executor"
    assert health["executor_access_policy"]["direct_executor_prefix"] == "/executor"
    assert health["executor_access_policy"]["failure_mode"] == "executor_required"
    assert routes["executor_access_policy"]["preferred_gateway_prefix"] == "/api/executor"
    executor_route = next(
        route for route in routes["routes"] if route["path_prefix"] == "/executor/"
    )
    assert executor_route["target_service"] == "executor"
    assert executor_route["target_instance"] == "executor-1"
    assert executor_route["route_policy"]["status"] == "preferred_execution_surface"
    assert executor_route["route_policy"]["preferred_gateway_prefix"] == "/api/executor"

    services = client.get("/admin/services").json()
    executor_service = next(
        service for service in services["services"] if service["service_id"] == "executor-1"
    )
    assert executor_service["executor_access_policy"]["preferred_gateway_prefix"] == "/api/executor"
    assert executor_service["executor_access_policy"]["failure_mode"] == "executor_required"
    assert executor_service["metadata"] == {}

    service_detail = client.get("/admin/services/executor-1").json()
    assert service_detail["executor_access_policy"]["direct_executor_prefix"] == "/executor"
    assert "task_type" not in service_detail["metadata"]
    assert "governance_task_type" not in service_detail["metadata"]
    assert "task_family" not in service_detail["metadata"]
    assert "execution_kind" not in service_detail["metadata"]

    health_executor_route = next(
        route for route in health["routes"] if route["path_prefix"] == "/executor/"
    )
    assert health_executor_route["route_policy"]["status"] == "preferred_execution_surface"


@pytest.mark.parametrize(
    ("service_type", "route_prefix"),
    [
        ("supervisor", "/supervisor/"),
        ("executor", "/executor/"),
    ],
)
def test_gateway_replaces_previous_instance_for_routed_singleton_service(
    service_type,
    route_prefix,
):
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    first = client.post(
        "/register",
        json={
            "service_id": f"{service_type}-old",
            "service_name": f"{service_type}-old",
            "service_type": service_type,
            "address": "http://127.0.0.1:65520",
        },
    )
    second = client.post(
        "/register",
        json={
            "service_id": f"{service_type}-new",
            "service_name": f"{service_type}-new",
            "service_type": service_type,
            "address": "http://127.0.0.1:65521",
        },
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert client.get(f"/admin/services/{service_type}-old").status_code == 404
    services = client.get("/admin/services").json()["services"]
    matching = [item for item in services if item["service_type"] == service_type]
    assert [item["service_id"] for item in matching] == [f"{service_type}-new"]
    routes = client.get("/admin/routes").json()["routes"]
    route = next(item for item in routes if item["path_prefix"] == route_prefix)
    assert route["target_instance"] == f"{service_type}-new"


@pytest.mark.parametrize(
    ("service_type", "gateway_path", "upstream_path"),
    [
        ("supervisor", "supervisor/runtime/activity", "/runtime/activity"),
        ("executor", "executor/body/registry", "/executor/body/registry"),
    ],
)
def test_gateway_maps_service_prefix_to_canonical_upstream_path(
    service_type,
    gateway_path,
    upstream_path,
):
    assert InternalGateway._upstream_route_path(service_type, gateway_path) == upstream_path


def test_gateway_memory_registration_invalidates_cached_address():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/register",
        json={
            "service_id": "memory-old",
            "service_name": "memory-old",
            "service_type": "memory",
            "address": "http://memory-old",
        },
    )
    assert gateway._resolve_memory_service_url() == "http://memory-old"

    client.post(
        "/register",
        json={
            "service_id": "memory-new",
            "service_name": "memory-new",
            "service_type": "memory",
            "address": "http://memory-new",
        },
    )

    assert gateway._resolve_memory_service_url() == "http://memory-new"


def test_gateway_registration_validation_preserves_bad_request_status():
    client = TestClient(InternalGateway(GatewayConfig()).app)

    response = client.post("/register", json={"service_name": "incomplete"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Missing required fields"


def test_gateway_health_update_preserves_missing_service_status():
    client = TestClient(InternalGateway(GatewayConfig()).app)

    response = client.post("/health/missing-service", json={"healthy": False})

    assert response.status_code == 404
    assert response.json()["detail"] == "Service not found"


def test_gateway_keeps_distinct_agent_slot_registrations():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    for slot_id in ("slot-A", "slot-B"):
        response = client.post(
            "/register",
            json={
                "service_id": f"agent-{slot_id}",
                "service_name": f"agent-{slot_id}",
                "service_type": "agent",
                "address": f"http://agent-{slot_id.lower()}",
                "metadata": {"slot_id": slot_id},
            },
        )
        assert response.status_code == 201

    services = client.get("/admin/services").json()["services"]
    agent_ids = [
        item["service_id"] for item in services if item["service_type"] == "agent"
    ]
    assert agent_ids == ["agent-slot-A", "agent-slot-B"]
    assert all(
        route["target_service"] != "agent"
        for route in client.get("/admin/routes").json()["routes"]
    )


def test_gateway_supervisor_route_is_marked_as_governance_runtime_surface():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "supervisor-1",
            "service_name": "supervisor-service",
            "service_type": "supervisor",
            "address": "http://127.0.0.1:65527",
        },
    )

    assert register_response.status_code == 201
    routes = client.get("/admin/routes").json()
    health = client.get("/").json()

    supervisor_route = next(
        route for route in routes["routes"] if route["path_prefix"] == "/supervisor/"
    )
    assert supervisor_route["target_service"] == "supervisor"
    assert supervisor_route["target_instance"] == "supervisor-1"
    assert supervisor_route["route_policy"]["status"] == "governance_runtime_surface"
    assert supervisor_route["route_policy"]["service_role"] == "planning_governance_runtime"

    health_supervisor_route = next(
        route for route in health["routes"] if route["path_prefix"] == "/supervisor/"
    )
    assert health_supervisor_route["route_policy"]["status"] == "governance_runtime_surface"


def test_gateway_executor_route_updates_execute_activity_even_when_upstream_fails():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    register_response = client.post(
        "/register",
        json={
            "service_id": "executor-1",
            "service_name": "executor-service",
            "service_type": "executor",
            "address": "http://127.0.0.1:65528",
        },
    )
    assert register_response.status_code == 201

    response = client.post(
        "/api/executor/body/upgrade/execute",
        json={
            "slot_id": "slot-B",
            "trace_id": "trace-exec-1",
            "task_type": "self_evolution",
            "decision_id": "decision-exec-1",
            "execution_request": {
                "task_id": "task-exec-1",
                "trace_id": "trace-exec-1",
                "task_type": "self_evolution",
                "decision_id": "decision-exec-1",
                "kind": "body_switch",
                "source_actor": "mem_supervisor",
            },
        },
    )

    assert response.status_code in {500, 504}
    activity = client.get("/admin/activity").json()
    assert activity["last_autonomous_chain_execute_at"] is not None
    assert activity["last_autonomous_chain_activity_at"] is not None
    assert activity["counts"]["autonomous_chain_execute_count"] == 1
    assert activity["counts"]["autonomous_chain_activity_count"] == 1
    assert activity["recent_metadata"]["autonomous_chain_execute"]["trace_id"] == "trace-exec-1"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["governance_task_type"] == "self_evolution"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["governance_task_type_label"] == "自主改进"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_family"] == "body_switch"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_family_label"] == "身体切换"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["execution_kind"] == "body_switch"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["execution_kind_label"] == "身体切换"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["decision_id"] == "decision-exec-1"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_id"] == "task-exec-1"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_identity"]["display_kind"] == "body_switch"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_identity"]["display_label"] == "身体切换"
    assert activity["recent_metadata"]["autonomous_chain_execute"]["task_identity"]["requested_kind"] == "body_switch"
    assert "task_type" not in activity["recent_metadata"]["autonomous_chain_execute"]
    assert "task_type_label" not in activity["recent_metadata"]["autonomous_chain_execute"]


def _post_agent_scene(client, session_id, metadata):
    return client.post(
        "/admin/activity/touch",
        json={
            "activity_kind": "agent_scene",
            "source_service": "cli_agent",
            "session_id": session_id,
            "metadata": metadata,
        },
    )


def test_gateway_agent_lanes_keep_supervisor_and_user_chat_separate():
    """A user_chat report must not overwrite the supervisor_task lane."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    # supervisor-task executor reports its subagents
    resp = _post_agent_scene(
        client,
        "supervisor-session",
        {
            "scene": "learning",
            "task_id": "learn-9",
            "execution_kind": "self_learning",
            "agent_role": "supervisor_task",
            "subagent_foreground_count": 3,
            "subagent_focus_tool": "read_file",
        },
    )
    assert resp.status_code == 200

    # main CLI user-chat executor reports afterwards (would overwrite top-level)
    resp = _post_agent_scene(
        client,
        "user-session",
        {
            "scene": "executing",
            "agent_role": "user_chat",
            "subagent_foreground_count": 1,
            "subagent_focus_tool": "grep",
        },
    )
    assert resp.status_code == 200

    scenes_payload = client.get("/admin/scenes").json()
    agent_scene = scenes_payload["scenes"]["agent"]
    summary = scenes_payload["summary"]
    assert agent_scene["scene"] == "executing"
    assert agent_scene["scene_projection_scope"] == "agent_top_level_projection"
    assert agent_scene["subagent_focus_tool"] == "grep"

    lanes = agent_scene["lanes"]
    # supervisor_task lane is preserved, NOT overwritten by the later user_chat push
    assert lanes["supervisor_task"]["scene"] == "learning"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 3
    assert lanes["supervisor_task"]["subagent_focus_tool"] == "read_file"
    assert lanes["supervisor_task"]["session_id"] == "supervisor-session"
    # user_chat lane holds its own data
    assert lanes["user_chat"]["scene"] == "executing"
    assert lanes["user_chat"]["subagent_foreground_count"] == 1
    assert summary["agent"] == "executing"
    assert summary["agent_user_chat"] == "executing"
    assert summary["agent_supervisor_task"] == "learning"

    active_cli = client.get("/").json()["active_cli_executor"]
    assert active_cli["session_id"] == "user-session"
    assert active_cli["scene_projection_scope"] == "agent_lane"
    assert active_cli["agent_lane"] == "user_chat"
    assert active_cli["subagent_focus_tool"] == "grep"


def test_gateway_agent_lane_scene_heuristic_fallback_without_role():
    """Older reporters without agent_role still route by scene heuristic."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    _post_agent_scene(
        client,
        "legacy-session",
        {
            "scene": "code_editing",
            "task_id": "body-1",
            "subagent_foreground_count": 2,
        },
    )
    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["scene"] == "code_editing"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 2
    assert lanes["user_chat"]["scene"] == "idle"


def test_gateway_agent_idle_clears_only_that_sessions_lane():
    """An idle push clears the lane the session owned, leaving the other intact."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    _post_agent_scene(
        client,
        "supervisor-session",
        {
            "scene": "learning",
            "agent_role": "supervisor_task",
            "subagent_foreground_count": 3,
        },
    )
    _post_agent_scene(
        client,
        "user-session",
        {
            "scene": "executing",
            "agent_role": "user_chat",
            "subagent_foreground_count": 1,
        },
    )

    # supervisor session goes idle -> only supervisor_task lane is cleared
    _post_agent_scene(
        client,
        "supervisor-session",
        {"scene": "idle", "agent_role": "supervisor_task"},
    )

    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["scene"] == "idle"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 0
    # user_chat lane untouched by the supervisor session's idle
    assert lanes["user_chat"]["scene"] == "executing"
    assert lanes["user_chat"]["subagent_foreground_count"] == 1


def test_gateway_deleting_session_clears_its_agent_lane():
    """Deleting a session blanks the lane it owned and drops it from the map."""
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)

    client.post(
        "/v1/sessions/register",
        json={"session_id": "sup-1", "model": "m", "provider": "p", "source": "cli"},
    )
    _post_agent_scene(
        client,
        "sup-1",
        {
            "scene": "learning",
            "agent_role": "supervisor_task",
            "subagent_foreground_count": 4,
        },
    )
    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 4

    session_info = client.get("/v1/sessions/sup-1").json()
    assert session_info["lease_status"] == "healthy"
    assert session_info["is_stale"] is False
    assert session_info["stale_after_seconds"] == gateway._active_cli_stale_after_seconds

    resp = client.request("DELETE", "/v1/sessions/sup-1")
    assert resp.status_code == 200

    lanes = client.get("/admin/scenes").json()["scenes"]["agent"]["lanes"]
    assert lanes["supervisor_task"]["scene"] == "idle"
    assert lanes["supervisor_task"]["subagent_foreground_count"] == 0
    assert gateway._agent_session_lane.get("sup-1") is None


def _register_supervisor(gateway, address="http://127.0.0.1:6002"):
    gateway._services["supervisor-1"] = ServiceInfo(
        service_id="supervisor-1",
        service_name="supervisor",
        service_type="supervisor",
        address=address,
        health_endpoint=f"{address}/health",
        registered_at=datetime.now(),
        last_health_check=datetime.now(),
        healthy=True,
    )












def test_improvement_report_forwards_to_supervisor(monkeypatch):
    # P0-2 成果回流 (body path): the gateway forwards an Agent improvement report
    # to the supervisor's /body/improvement-report endpoint.
    gateway = InternalGateway(GatewayConfig())
    _register_supervisor(gateway)
    client = TestClient(gateway.app)

    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return {"status": "reviewed", "score_delta": 12}

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr("voidcube.infrastructure.gateway.internal_gateway.aiohttp.ClientSession", _FakeSession)

    report = {
        "slot_id": "slot-B",
        "task_id": "imp-7",
        "commit_hash": "abc123",
        "diff_summary": "skills/foo.py | 3 +-",
        "changed_files": ["skills/foo.py"],
        "improvement_description": "Improved foo handling.",
    }
    response = client.post("/v1/body/improvement-report", json=report)

    assert response.status_code == 200
    assert response.json()["score_delta"] == 12
    assert captured["url"].endswith("/body/improvement-report")
    assert captured["json"]["slot_id"] == "slot-B"
    assert captured["json"]["commit_hash"] == "abc123"


def test_improvement_report_503_when_no_supervisor():
    gateway = InternalGateway(GatewayConfig())
    client = TestClient(gateway.app)
    response = client.post("/v1/body/improvement-report", json={"slot_id": "slot-B"})
    assert response.status_code == 503
